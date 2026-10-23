"""Finmod guidance application service."""
from __future__ import annotations
import json
import logging
from services import finmod_refs
from services.json_compat import extract_top_block, python_literal_to_json
from services.results import error, success
from storage.connections import get_connection as get_db
from storage.project_data_store import ProjectDataStore

from services.app_models import _build_llm_client, _chat_complete
from services.finmod_mapping import _build_varmap_user_content, _derive_formula, _is_derivable_var, _model_var_coverage, _numeric_candidate_cols, _sheet_columns

logger = logging.getLogger(__name__)

GUIDE_SYSTEM = (
    "你是「财务建模与分析」集成应用的引导 AI，面向无财务基础的业务人员。\n"
    "你只输出建议（JSON），绝不执行任何计算、不读取数据、不调用工具。\n"
    "根据用户的一句话诉求与数据文件列名索引，推荐 1-3 个最适合的财务模型，\n"
    "并给出白话教学文案。\n\n"
    "硬性规则：\n"
    "1. 模式 mode 由系统判定给出（消息中标注），你必须原样使用，不得更改\n"
    "2. directions 的 model_id 必须从「模型目录」中选择（用完整 id 如 d1-dupont，\n"
    "   或编号如 D1），不得虚构不存在的模型\n"
    "3. teaching 教学文案：用大白话解释该模型是干什么的、为什么适合用户诉求、\n"
    "   会用用户文件的哪些列（引用具体列名）、关键公式（$$ LaTeX 块级，中文用 \\text{} 包裹）\n"
    "4. reason 推荐理由：像同事在茶水间闲聊一样口语化，1-2 句。必须引用用户诉求\n"
    "   或数据文件中的具体列名/sheet 名/数值作依据；禁止空泛总结（如\"可以全面分析\"）；\n"
    "   禁止连接词（首先/其次/此外/综上/同时）；禁止营销动词（快速/直接锁定/深度/\n"
    "   全面/助力/赋能/一步到位）；不用感叹号，不用排比句式\n"
    "5. recommended_charts：从「图表模板」中选择 1-3 个模板名（如 radar/tornado/line）\n"
    "6. 模式 A（无数据）：只填 data_needs（每条含 字段/类型/示例/用途/建议图表），directions 可为空\n"
    "7. 模式 C（无诉求）：directions 与 data_needs 都为空，explanation 给出讲解引导文案。\n"
    "   explanation 必须用 markdown 分点排版：每个「步骤/建议/示例」用 `- ` 开头单独一行，\n"
    "   数字序号（如 1）2）3））也一律换行成为独立列表项，禁止把多句话挤成一行大段文本；\n"
    "   可适当用 **加粗** 突出关键词。\n\n"
    "输出严格 JSON（不要 markdown 代码块围栏，不要多余文字）：\n"
    "{\n"
    '  "mode": "A" | "B" | "C",\n'
    '  "directions": [{"model_id": "...", "reason": "...", "teaching": "...", "recommended_charts": ["radar"]}],\n'
    '  "data_needs": [{"field": "...", "type": "数值|文本|日期", "example": "...", "purpose": "...", "suggested_chart": "..."}],\n'
    '  "explanation": "模式 C 讲解文案或整体引导说明"\n'
    "}"
)


def _detect_mode(goal: str, has_data: bool) -> str:
    """确定性模式判定（需求文档 §3.3，LLM 不可覆盖）。

    A 数据需求：有目标 + 无数据；B 已有数据：有目标 + 已上传；C 知识讲解：无目标。
    """
    if not (goal or "").strip():
        return "C"
    return "B" if has_data else "A"


def _build_files_index(store: ProjectDataStore, project_id: str) -> list[dict]:
    """数据文件列名索引摘要（列名+类型+行数，不读全量数据）。

    每个文件取前 3 个 sheet（按行数降序取数据主体），每 sheet 输出
    列名（含类型）摘要。LLM 输入用，token 可控。
    """
    out: list[dict] = []
    try:
        files = store.list_files(project_id) or []
    except Exception:  # noqa: BLE001
        return out
    for f in files:
        if f.get("status") != "done":
            continue
        sheets = (f.get("sheets") or [])[:3]
        items = []
        for sh in sheets:
            # headers = [{name, type}, ...]（list_files 已解析 headers_json）
            headers = sh.get("headers") or []
            items.append({
                "sheet": sh.get("sheet_name") or "",
                "rows": sh.get("row_count") or 0,
                "columns": [
                    {"name": h.get("name") or "", "type": h.get("type") or ""}
                    for h in headers
                ],
            })
        if items:
            out.append({
                "file": f.get("file_name") or "",
                "total_rows": f.get("total_rows") or 0,
                "sheets": items,
            })
    return out


def _model_catalog_text() -> str:
    """模型目录摘要文本（编号 + 名称 + 分类），供 LLM 选择 directions。"""
    meta = finmod_refs.get_meta()
    lines = []
    for cat in meta["model_categories"]:
        names = "、".join(f"{m['code']} {m['name']}" for m in cat["models"])
        lines.append(f"{cat['key']} {cat['label']}：{names}")
    return "\n".join(lines)


def _chart_catalog_text() -> str:
    """图表模板目录摘要文本（模板名 + 中文名 + 档位），供 LLM 推荐图表。"""
    meta = finmod_refs.get_meta()
    parts = []
    for g in meta["chart_levels"]:
        ids = "、".join(f"{c['id']}({c['name']})" for c in g["charts"])
        parts.append(f"{g['key']} {g['label']}：{ids}")
    return "\n".join(parts)


def _resolve_model_ref(ref: str) -> dict | None:
    """把 LLM 输出的模型引用（编号 D1 / 完整 id d1-dupont）归一化为 meta。"""
    raw = (ref or "").strip().lower()
    if not raw:
        return None
    for m in finmod_refs.scan_models():
        if m["code"].lower() == raw or m["id"].lower() == raw:
            return m
    return None


def _build_guide_user_content(goal: str, mode: str, files_index: list[dict]) -> str:
    lines = [
        f"系统判定模式：{mode}（{'有目标+有数据' if mode == 'B' else '有目标+无数据' if mode == 'A' else '无目标，讲解' }）",
        "",
        f"用户诉求：{goal or '（未提供——用户只想了解财务模型知识，请输出模式 C 讲解引导）'}",
        "",
    ]
    if files_index:
        lines.append("数据文件列名索引（只读列名/类型，不读数据）：")
        for f in files_index:
            lines.append(f"- 文件「{f['file']}」（共 {f['total_rows']} 行）")
            for sh in f["sheets"]:
                cols = "、".join(f"{c['name']}({c['type']})" for c in sh["columns"])
                lines.append(f"  - sheet「{sh['sheet']}」（{sh['rows']} 行）：{cols or '（无列）'}")
    else:
        lines.append("数据文件：无（用户尚未上传数据文件）")
    lines.append("")
    lines.append("模型目录（directions 从这里选）：")
    lines.append(_model_catalog_text())
    lines.append("")
    lines.append("图表模板（recommended_charts 从这里选）：")
    lines.append(_chart_catalog_text())
    return "\n".join(lines)


def _parse_guide_json(text: str) -> dict | None:
    """解析 LLM 输出 JSON（剥离 Markdown 围栏并兼容 Python 字面量）。

    兼容三种形态：纯 JSON / Python 单引号 dict / ```json 围栏包裹。
    """
    if not (text or "").strip():
        return None
    import re as _re
    cleaned = text.strip()
    # 剥掉 ```json ... ``` 围栏
    if cleaned.startswith("```"):
        cleaned = _re.sub(r"^```[^\n]*\n", "", cleaned)
        cleaned = _re.sub(r"\n?```\s*$", "", cleaned)
    try:
        # 1) 直接解析（合法 JSON 或经单引号容错）
        fixed = python_literal_to_json(cleaned)
        obj = json.loads(fixed, strict=False)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    try:
        # 2) 顶层块兜底（原文含前后说明文字时提取 {..}）
        blob = extract_top_block(cleaned)
        obj = json.loads(blob, strict=False)
        if isinstance(obj, dict):
            return obj
    except Exception:  # noqa: BLE001
        pass
    return None


def _normalize_ai_mappings(obj: dict, models: list[dict]) -> list[dict]:
    """容错：LLM 未按标准 {"mappings":[...]} 输出时，识别替代结构并归一化为标准 mappings。

    deepseek 系模型常输出：
      - {"matched_columns": {"产品收入_i": "销售收入金额_含税_元", ...}}
      - {"column_mapping": {...}} / {"columns": {...}} / {"推荐列": {...}} / {"建议": {...}}
      - 值可能是列名（str）或 {mapped_col|column|建议列|推荐列: 列名, ...}。

    返回 [{model_id, var_name, mapped_col, confidence, reason, candidates}]。
    无法识别的变量名直接跳过；mapped_col 为空仍返回（confidence 低，提示用户）。
    """
    if not isinstance(obj, dict):
        return []
    # 1. 变量名→列名 的映射（从多个候选 key 提取）
    var2col: dict[str, str] = {}
    for key in ("matched_columns", "column_mapping", "columns", "column_map",
                "推荐列", "建议列", "映射", "mapping", "recommend", "建议"):
        blob = obj.get(key)
        if isinstance(blob, dict):
            for vn, val in blob.items():
                if isinstance(val, str):
                    var2col[str(vn)] = val
                elif isinstance(val, dict):
                    col = val.get("mapped_col") or val.get("column") or \
                          val.get("建议列") or val.get("推荐列") or val.get("col")
                    if isinstance(col, str) and col:
                        var2col[str(vn)] = col
    if not var2col:
        return []

    # 2. 用基座模型变量对齐 var_name（含正则识别率/占比等词），生成标准 mappings
    out: list[dict] = []
    for m in models:
        mid = m.get("model_id") or ""
        for v in m.get("variables") or []:
            vn = str(v.get("var_name") or "")
            if v.get("direction") not in ("input",):
                continue
            mapped = var2col.get(vn) or var2col.get(f"{vn}（{v.get('var_meaning')}）")
            if not mapped and v.get("var_meaning"):
                # 含义/单位后缀容错（如 「产品收入_i（第 i 产品线收入）」）
                for k in var2col:
                    if k.startswith(vn):
                        mapped = var2col[k]
                        break
            conf = 0.5 if mapped else 0.3
            out.append({
                "model_id": mid, "var_name": vn, "mapped_col": mapped or "",
                "confidence": conf, "reason": "", "candidates": [],
            })
    return out


def _finmod_guide(project_id: str, model_id: str, goal: str,
                  file_names: list[str] | None = None) -> dict:
    """引导编排器：诉求 + 列名索引 → 建议 JSON（含降级）。"""
    store = ProjectDataStore(get_db())
    files_index = _build_files_index(store, project_id)
    mode = _detect_mode(goal, bool(files_index))

    try:
        client = _build_llm_client(model_id)
    except ValueError as exc:
        logger.warning("财务建模引导模型不可用，将进入手动选择: %s", exc)
        client = None
    if not client:
        # 降级：LLM 不可用 → 前端提示手动路径（模式仍确定性判定）
        return {
            "success": False,
            "mode": mode,
            "message": "AI 引导不可用（模型不可用或未配置），可手动进入模型库选择模型",
            "directions": [],
            "data_needs": [],
        }

    content = _build_guide_user_content(goal, mode, files_index)
    raw = ""
    last_err: Exception | None = None
    for attempt in range(2):  # 失败重试 1 次
        try:
            raw = _chat_complete(
                client,
                [
                    {"role": "system", "content": GUIDE_SYSTEM},
                    {"role": "user", "content": content},
                ],
                temperature=0.3,
                max_tokens=4000,
            )
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("finmod guide 第 %d 次调用失败: %s", attempt + 1, e)
    if not raw.strip():
        logger.warning("finmod guide 输出为空: %s", last_err)
        return {
            "success": False,
            "mode": mode,
            "message": "AI 引导不可用（模型返回为空），可手动进入模型库选择模型",
            "directions": [],
            "data_needs": [],
        }

    obj = _parse_guide_json(raw) or {}
    # 模式以确定性判定为准（LLM 的 mode 字段忽略）
    directions: list[dict] = []
    for d in obj.get("directions") or []:
        if not isinstance(d, dict):
            continue
        meta = _resolve_model_ref(str(d.get("model_id") or ""))
        if not meta:
            continue
        # v2.7 数据完备性：模式 B（有数据）时计算模型变量覆盖度，
        # 让步骤 2 就能看到「这个模型需要哪些变量、数据里缺什么」
        var_coverage: dict = {}
        if mode == "B":
            var_coverage = _model_var_coverage(store, project_id, meta["id"],
                                               file_names=file_names)
        directions.append({
            "model_id": meta["id"],
            "code": meta["code"],
            "name": meta["name"],
            "reason": str(d.get("reason") or ""),
            "teaching": str(d.get("teaching") or ""),
            "recommended_charts": [str(c) for c in (d.get("recommended_charts") or []) if c],
            "var_coverage": var_coverage,
        })
    data_needs: list[dict] = []
    for n in obj.get("data_needs") or []:
        if not isinstance(n, dict):
            continue
        data_needs.append({
            "field": str(n.get("field") or ""),
            "type": str(n.get("type") or ""),
            "example": str(n.get("example") or ""),
            "purpose": str(n.get("purpose") or ""),
            "suggested_chart": str(n.get("suggested_chart") or ""),
        })
    return {
        "success": True,
        "mode": mode,
        "goal": goal,
        "message": "AI 建议已生成",
        "directions": directions,
        "data_needs": data_needs,
        "explanation": str(obj.get("explanation") or ""),
    }


def finmod_guide(p: dict):
    """引导引擎：诉求 + 列名索引 → 建议 JSON（方向/数据需求/讲解，1-2 次调用）。

    body: {project_id, model_id, goal, file_names?: []}
    R1：file_names 传入时完备性校验按多文件评估。
    """
    project_id = (p.get("project_id") or "").strip()
    model_id = (p.get("model_id") or "").strip()
    goal = (p.get("goal") or "").strip()
    file_names = p.get("file_names") or []
    if isinstance(file_names, str):
        file_names = [file_names]
    file_names = [f for f in file_names if f]
    if not project_id:
        return error("缺少 project_id")
    try:
        result = _finmod_guide(project_id, model_id, goal, file_names=file_names or None)
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/guide 失败")
        return error(f"引导失败: {e}")
    if not result.get("success"):
        # 降级不视为 HTTP 错误：返回 success=false + 前端提示手动路径
        return success(result, status_code=200)
    return success(result)


VAR_MAP_SYSTEM = (
    "你是「财务建模与分析」集成应用的变量匹配 AI。\n"
    "根据模型的变量定义与数据文件列名索引，为每个【输入变量】推荐最契合的数据列。\n"
    "你只输出建议 JSON，绝不执行任何计算。\n\n"
    "硬性规则：\n"
    "1. 每个 input 变量输出一条 mapping：{model_id, var_name, mapped_col, confidence, reason, candidates}\n"
    "2. 匹配优先：精确列名 > 语义相近（如「业务员」↔「销售人员」）> 归一化包含；\n"
    "   列名无法匹配时给 candidates（top3 最可能列），mapped_col 留空\n"
    "3. 类型约束：金额/数值变量只匹配数值列；维度/分类变量只匹配文本列；\n"
    "   期间变量只匹配日期列（period_cols 里）\n"
    "4. confidence：0.0~1.0（精确列名且类型匹配 ≥0.9；语义相近 0.7-0.85；猜测 <0.6）\n"
    "5. reason 口语化 1 句，像同事闲聊（如「列名就叫回款总额，直接对上」），\n"
    "   禁连接词/营销动词/感叹号/排比\n"
    "6. statistic 变量（n/x̄/s/CV 等）：不用映射列，输出 stat_binding：\n"
    "   {var_name, stat: \"COUNT\"|\"MEAN\"|\"STD\"|\"MEDIAN\"|\"VAR\", based_on: 同模型观测值变量名}——\n"
    "   基于同一模型 input 变量（如 x_i）绑定统计函数\n"
    "7. output 变量：跳过（模型计算结果，无需映射）\n\n"
    "输出严格 JSON（不要 markdown 围栏，不要多余文字）：\n"
    '{"mappings": [{"model_id":"g1-descriptive-stats","var_name":"x_i","mapped_col":"回款总额",'
    '"confidence":0.95,"reason":"...","candidates":["回款总额","应发金额"]}],\n'
    ' "stat_bindings": [{"model_id":"g1-descriptive-stats","var_name":"x̄","stat":"MEAN","based_on":"x_i"}]}'
)


def finmod_varmap_suggest(p: dict):
    """AI 变量匹配：模型变量 × 文件列名 → LLM 推荐（置信度 + 理由 + 统计量绑定）。

    body: {project_id, file_name?, file_names?: [], sheet_name?, model_ids: [], model_id: LLM 模型}
    R1：file_names 传入时多文件列索引合并（LLM 输入含来源文件标注）。
    Returns: {varmap: 确定性基座（含 direction/type），ai_suggestions, used_llm}
    """
    project_id = (p.get("project_id") or "").strip()
    file_name = (p.get("file_name") or "").strip()
    file_names = p.get("file_names") or []
    if isinstance(file_names, str):
        file_names = [file_names]
    file_names = [f for f in file_names if f]
    if not file_names and file_name:
        file_names = [file_name]
    sheet_name = (p.get("sheet_name") or "").strip()
    model_ids = p.get("model_ids") or []
    llm_model = (p.get("model_id") or "").strip()
    if not project_id or not file_names:
        return error("缺少 project_id/file_name")
    if not model_ids:
        return error("缺少 model_ids")
    try:
        content, base = _build_varmap_user_content(project_id, file_name, sheet_name,
                                                   list(model_ids), file_names=list(file_names))
        ai_suggestions: dict = {"mappings": [], "stat_bindings": []}
        used_llm = False
        try:
            client = _build_llm_client(llm_model)
        except ValueError as exc:
            logger.warning("财务建模变量匹配模型不可用，降级确定性映射: %s", exc)
            client = None
        if client:
            try:
                raw = _chat_complete(
                    client,
                    [{"role": "system", "content": VAR_MAP_SYSTEM},
                     {"role": "user", "content": content}],
                    temperature=0.2,
                    max_tokens=4000,
                )
                obj = _parse_guide_json(raw) or {}
                ai_suggestions = {
                    "mappings": [m for m in (obj.get("mappings") or []) if isinstance(m, dict)],
                    "stat_bindings": [s for s in (obj.get("stat_bindings") or []) if isinstance(s, dict)],
                }
                # v3.6 容错：LLM（尤其是 deepseek 系）常输出自定义结构而非标准
                # {"mappings":[{model_id,var_name,mapped_col,...}]}。识别这类替代结构
                # （matched_columns/column_mapping/columns/建议/推荐列 等：值=列名或
                # {mapped_col/column/建议列}），归一化为标准 mappings，避免「AI 匹配不可用」。
                if not ai_suggestions["mappings"] and not ai_suggestions["stat_bindings"]:
                    ai_suggestions["mappings"] = _normalize_ai_mappings(obj, base["models"])
                    if ai_suggestions["mappings"]:
                        used_llm = True
                used_llm = bool(ai_suggestions["mappings"] or ai_suggestions["stat_bindings"])
                # v3.1：LLM 常把 var_meaning 拼进 var_name（如「本期/去年同期（对比期数值）」）
                # 而基座 var_name 是规范名（如「本期/去年同期」）——归一化回基座名，
                # 否则前端 over[v.var_name] 匹配不上，AI 高置信映射无法自动应用。
                if used_llm:
                    canonical = {
                        (m["model_id"], v["var_name"])
                        for m in base["models"] for v in m["variables"]
                    }
                    def _canon_var(mid: str, ai_vn: str) -> str:
                        # 基座里存在完全一致名→直接用；否则用基座变量名是 ai_vn 前缀/子串的
                        if (mid, ai_vn) in canonical:
                            return ai_vn
                        for (cmid, cvn) in canonical:
                            if cmid != mid:
                                continue
                            # ai_vn = 规范名 + （含义）后缀 → 取以 cvn 开头的那个
                            if cvn and ai_vn and ai_vn.startswith(cvn):
                                return cvn
                        return ai_vn
                    for m in ai_suggestions["mappings"]:
                        m["var_name"] = _canon_var(
                            str(m.get("model_id") or ""), str(m.get("var_name") or ""))
            except Exception as e:  # noqa: BLE001
                logger.warning("finmod/varmap-suggest LLM 失败，降级确定性: %s", e)
        # 与 /varmap 一致的统计字段（input/statistic 计数 + message）
        input_vars = [v for m in base["models"] for v in m["variables"]
                      if v["direction"] == "input"]
        missing = sum(1 for v in input_vars if v["status"] == "missing")
        statistic_n = sum(1 for m in base["models"] for v in m["variables"]
                          if v["direction"] == "statistic")
        base["missing_count"] = missing
        base["input_count"] = len(input_vars)
        base["statistic_count"] = statistic_n
        base["message"] = (
            f"共 {len(input_vars)} 个输入变量，{missing} 个未匹配"
            f"（AI 已推荐或可手动选列）" + (f"；{statistic_n} 个统计量将自动计算" if statistic_n else "")
        )
        # v3.4：为 derivable（可派生）变量生成候选派生公式（内置公式库 + 数值列）
        derived_formulas: list[dict] = []
        try:
            # 宽松数值列候选（int/float/num 类型 + 财务词列兜底）；多文件时用合并列索引
            _store = ProjectDataStore(get_db())
            if file_names and len(file_names) > 1:
                from tools.finmod_merge import collect_all_columns
                _cols = collect_all_columns(_store, project_id, file_names, sheet_name)["cols_info"]
            else:
                _cols = _sheet_columns(_store, project_id, file_names[0])
            num_col_names = _numeric_candidate_cols(_cols)
            for m in base["models"]:
                for v in m["variables"]:
                    if v["direction"] != "input" or v["status"] != "missing":
                        continue
                    if not _is_derivable_var(v, base.get("all_cols") or [], num_col_names):
                        continue
                    d = _derive_formula(v, num_col_names)
                    if d:
                        derived_formulas.append({
                            "model_id": m["model_id"],
                            "var_name": v["var_name"],
                            "formula": d["formula"],
                            "based_on": d["based_on"],
                            "note": d["note"],
                        })
        except Exception as _dfe:  # noqa: BLE001
            logger.warning("finmod/varmap-suggest 派生公式生成失败: %s", _dfe)
            derived_formulas = []
        return success({
            "varmap": base,
            "ai_suggestions": ai_suggestions,
            "derived_formulas": derived_formulas,
            "used_llm": used_llm,
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/varmap-suggest 失败")
        return error(f"变量匹配失败: {e}")
