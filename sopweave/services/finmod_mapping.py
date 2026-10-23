"""Finmod mapping application service."""
from __future__ import annotations
import logging
import re
from services import finmod_refs
from services.results import error, success
from storage.connections import get_connection as get_db
from storage.project_data_store import ProjectDataStore

from services.app_data import _similar

logger = logging.getLogger(__name__)

_DERIVABLE_VAR_RE = re.compile(
    r"(率$|比率|占比|周转|差异|偏差|盈亏|保本|平衡点|边际贡献|杠杆|倍数|"
    r"环比|同比|同期|对比期|累计|系数|指数值|预测值|结果|区间|网格|情景|净利$)"
)


def _is_derivable_var(v: dict, cols: list[str], num_col_names: list[str]) -> bool:
    """判断未匹配的 input 变量是否「可派生」——由已有数值列/同名列运算可得。"""
    vn = str(v.get("var_name") or "")
    meaning = str(v.get("var_meaning") or "")
    return bool(_DERIVABLE_VAR_RE.search(vn) or _DERIVABLE_VAR_RE.search(meaning))


def _derive_formula(v: dict, num_col_names: list[str]) -> dict | None:
    """为可派生变量生成候选公式（v3.4 内置公式库）。

    常见场景：
      - 占比/率：`占比 = 分项 / 合计`、`率 = 分子 / 分母`（取语义匹配的前 2 数值列）
      - 差异/偏差：`差异 = 实际 - 预算`
      - 环比/同比：`同比 = PCT_CHANGE(列, 1)`（时序）
    关键约束：候选列必须与变量语义有词级匹配（如「占比」→含「占比/比例」列），
    否则返回 None（避免用不相关列生成错误公式，如用「销售额计算比例」算杜邦指标）。
    """
    vn = str(v.get("var_name") or "")
    meaning = str(v.get("var_meaning") or "")
    blob = f"{vn} {meaning}"
    cols = [c for c in num_col_names if _num_reliable(c)]

    def _semantic_match(keywords: list[str]) -> list[str]:
        """候选列里含语义关键词的列（按出现顺序）。"""
        return [c for c in cols if any(k in c for k in keywords)]

    # 占比/率（分子/分母）——语义词：占比/比例/份额/比率（不含「周转率」等量词）
    if re.search(r"(占比|比例|份额|比率)", blob):
        seg = _semantic_match(["占比", "比例", "份额", "比率"])
        rate = _semantic_match(["占比", "比例", "份额", "比率", "率"])
        base = seg or rate
        if base:
            if len(base) >= 2:
                return {"formula": f"{vn}={base[0]}/{base[1]}", "based_on": base[:2],
                        "note": f"由 {base[0]} ÷ {base[1]} 计算"}
            if len(base) == 1:
                return {"formula": f"{vn}={base[0]}/SUM({base[0]})", "based_on": [base[0]],
                        "note": f"由 {base[0]} 占比计算"}
    # 差异/偏差（实际-预算）——语义词：实际/预算/标准/计划
    if re.search(r"(差异|偏差)", blob):
        pairs = _semantic_match(["实际", "预算", "标准", "计划", "本期", "上期", "去年"])
        if len(pairs) >= 2:
            return {"formula": f"{vn}={pairs[0]}-{pairs[1]}", "based_on": pairs[:2],
                    "note": f"由 {pairs[0]} − {pairs[1]} 计算"}
    # 环比/同比/变化率（时序）——时间列
    if re.search(r"(环比|同比|变化率|增长率|增速)", blob):
        time_cols = _semantic_match(["回款", "收入", "金额", "销售", "提成"])
        if time_cols:
            return {"formula": f"{vn}=PCT_CHANGE({time_cols[0]},1)", "based_on": [time_cols[0]],
                    "note": f"由 {time_cols[0]} 环比变化计算"}
    # v3.4 兜底：率/周转/占比类指标——用财务量级列作候选（分子/分母），生成候选公式。
    # 注：候选列可能不完全精准（如杜邦「总资产周转率」数据里无总资产列），
    # 仅作 AI 建议，用户可在步骤 3 调整；note 标注「候选，可调整」。
    if re.search(r"(周转|率$|比率|占比|倍数|系数)", blob):
        fin = _finance_numeric_cols(cols)  # 优先财务量级列
        pool = fin or [c for c in cols if _num_reliable(c)]
        if len(pool) >= 2:
            return {"formula": f"{vn}={pool[0]}/{pool[1]}", "based_on": pool[:2],
                    "note": f"候选：{pool[0]} ÷ {pool[1]}（可调整）"}
        if pool:
            return {"formula": f"{vn}={pool[0]}/SUM({pool[0]})", "based_on": [pool[0]],
                    "note": f"候选：{pool[0]} 占比（可调整）"}
    return None


def _numeric_candidate_cols(cols_info: list[dict]) -> list[str]:
    """宽松的数值列候选：数值类型 + 财务量级词列（金额/回款/收入/成本等）。

    该文件导入常把数值列标成 text（列名含 金额/回款/收入 等），故两类都纳入，
    供 _assess_readiness 可派生判定与 varmap-suggest 派生公式生成复用。
    """
    names = [c["name"] for c in cols_info]
    out = []
    for c in cols_info:
        typ = str(c.get("type") or "").lower()
        if any(k in typ for k in ("int", "float", "decimal", "num")):
            out.append(c["name"])
    fin_words = ("金额", "回款", "收入", "成本", "费用", "销售额", "货款", "利润", "提成")
    for n in names:
        if n not in out and any(k in n for k in fin_words) and _num_reliable(n):
            out.append(n)
    return out


def _num_reliable(col: str) -> bool:
    """列名是否可能是可靠数值列（排除序数列/名称/时间/明显非数值名）。"""
    if not col:
        return False
    if any(k in col for k in ("序号", "编号", "名称", "方式", "渠道", "状态",
                              "备注", "说明", "时间", "日期", "年月", "季度",
                              "Id", "ID", "id", "序", "列号")):
        return False
    return True


def _finance_numeric_cols(cols: list[str]) -> list[str]:
    """筛选适合做派生的财务量级数值列：优先金额/回款/收入/成本/费用/销售额等。

    用量级词（金额/回款/收入/成本/费用/销售额/货款/利润）优先，避免误用
    「比例/名称/时间」类列（如 提成比例、销售额计算比例、提成项目名称）。
    """
    finance_words = ("金额", "回款", "收入", "成本", "费用", "销售额", "货款", "利润", "毛利")
    fin = [c for c in cols if any(k in c for k in finance_words)]
    # 排除比例性列（销售额计算比例/提成比例等），它们不是量级列
    fin = [c for c in fin if not any(k in c for k in ("比例", "比率", "率"))]
    if fin:
        return fin
    # 兜底：排除比例/名称/时间后的其余列
    excluded = ("比例", "比率", "率", "名称", "时间", "日期", "方式", "状态", "编号")
    return [c for c in cols if _num_reliable(c) and not any(k in c for k in excluded)]


def _assess_readiness(store: ProjectDataStore, project_id: str,
                      model_id: str,
                      file_names: list[str] | None = None) -> dict:
    """统一口径的模型完备性评估（v3.2，步骤 1/2/3 共用，替代宽松 _model_var_coverage）。

    用「精确映射」口径（与 _finmod_varmap 一致），并把未匹配变量拆成两类：
      - derivable：可由已有数值列派生的缺失变量（占比/率/同比/周转/结果指标…）
      - truly_missing：既无同名列、也无法派生的变量 → 才真正需要补数据
    R1：file_names 传入（多文件）时按多文件合并列索引评估。
    返回：
      ready / matched / missing / derivable / truly_missing /
      statistic / input_vars / missing_names（= truly_missing 变量名，兼容旧前端）/
      suggestion（人类可读结论）/ has_file
    """
    try:
        files = store.list_files(project_id) or []
        if not files:
            return {"ready": False, "matched": 0, "missing": 0, "derivable": [],
                    "truly_missing": [], "statistic": 0, "input_vars": 0,
                    "missing_names": [], "has_file": False, "suggestion": "无数据文件"}
        if file_names:
            target_files = [f for f in file_names if f]
        else:
            target_files = [next((f["file_name"] for f in files if f.get("status") == "done"),
                                 files[0]["file_name"])]
        file_name = target_files[0]
        if len(target_files) > 1:
            from tools.finmod_merge import collect_all_columns
            col_idx = collect_all_columns(store, project_id, target_files)
            cols_info = col_idx["cols_info"]
            all_cols = col_idx["all_cols"]
            num_col_names = _numeric_candidate_cols(cols_info)
        else:
            cols_info = _sheet_columns(store, project_id, file_name)
            all_cols = [c["name"] for c in cols_info]
            num_col_names = _numeric_candidate_cols(cols_info)
        result = _finmod_varmap(store, project_id, file_name, "", [model_id],
                                file_names=list(target_files))
        model = result["models"][0] if result.get("models") else None
        if not model:
            return {"ready": False, "matched": 0, "missing": 0, "derivable": [],
                    "truly_missing": [], "statistic": 0, "input_vars": 0,
                    "missing_names": [], "has_file": True, "suggestion": "模型未找到"}
        variables = model["variables"]
        input_vars = [v for v in variables if v["direction"] == "input"]
        statistic_n = sum(1 for v in variables if v["direction"] == "statistic")
        derivable: list[str] = []
        truly_missing: list[str] = []
        derivable_details: list[dict] = []
        truly_details: list[dict] = []
        matched = 0
        for v in input_vars:
            vn = str(v.get("var_name") or "")
            vt = str(v.get("var_type") or "numeric")
            detail = {"var_name": vn, "meaning": str(v.get("var_meaning") or ""),
                      "unit": str(v.get("var_unit") or ""), "var_type": vt}
            if v.get("status") == "matched":
                matched += 1
                continue
            # 精确口径未匹配 → 拆分类：可派生（正则命中 且 内置公式库能生成公式）
            # 仅正则命中但数据无对应基础列（如杜邦指标在提成表无净利润/总资产）
            # → 归为真正缺失，避免用不相关列生成错误公式。
            if _is_derivable_var(v, all_cols, num_col_names):
                if _derive_formula(v, num_col_names):
                    derivable.append(vn)
                    derivable_details.append(detail)
                    continue
            truly_missing.append(vn)
            truly_details.append(detail)
        missing = len(truly_missing)
        # suggestion 人类可读结论
        if not input_vars:
            suggestion = "模型自动计算，无需外部变量"
        elif matched == len(input_vars):
            suggestion = "可直接建模，AI 已自动映射"
        elif derivable and not truly_missing:
            suggestion = f"缺 {len(derivable)} 个（均可用已有列派生），可直接建模"
        elif derivable:
            suggestion = (f"缺 {len(truly_missing)} 个需补数据 + "
                          f"{len(derivable)} 个可派生；AI 将自动派生")
        else:
            suggestion = f"缺 {len(truly_missing)} 个数据，建议补充"
        return {
            "ready": not truly_missing,
            "matched": matched,
            "missing": missing,
            "derivable": derivable,
            "truly_missing": truly_missing,
            "derivable_details": derivable_details,   # v3.3 可派生变量详情（供简历派生公式）
            "truly_missing_details": truly_details,   # v3.3 真缺变量详情（含义/单位 → 建议补什么）
            "statistic": statistic_n,
            "input_vars": len(input_vars),
            "missing_names": truly_missing,  # 兼容旧前端（缺真正数据才提示补）
            "has_file": True,
            "suggestion": suggestion,
            "files": target_files,  # R1：参与完备性评估的文件列表
        }
    except Exception:  # noqa: BLE001
        return {"ready": False, "matched": 0, "missing": 0, "derivable": [],
                "truly_missing": [], "statistic": 0, "input_vars": 0,
                "missing_names": [], "has_file": False, "suggestion": "完备性评估失败"}


def _model_var_coverage(store: ProjectDataStore, project_id: str,
                        model_id: str,
                        file_names: list[str] | None = None) -> dict:
    """模型变量 × 数据列 → 覆盖度（v3.2 统一口径，委托 _assess_readiness）。

    v2.7 宽松口径（numeric 变量有数值列即"已齐"）会导致 D4 等模型误报"变量已齐"、
    步骤 3 却强制映射引擎不用的变量。v3.2 改为统一精确口径（_assess_readiness），
    并把缺失拆成 derivable（可派生）/truly_missing（真缺），前端可更准确提示。
    保持旧字段（matched/missing/statistic/input_vars/missing_names/has_file）兼容，
    并新增 ready/derivable/truly_missing/suggestion。
    """
    return _assess_readiness(store, project_id, model_id, file_names=file_names)


def _sheet_columns(store: ProjectDataStore, project_id: str,
                   file_name: str, sheet_name: str = "") -> list[dict]:
    """文件某 sheet 的列清单 [{name, type, excel_col}]（sheet 缺省取首个数据主体）。"""
    file_rec = store.find_file_by_name(project_id, file_name)
    if not file_rec:
        raise ValueError(f"未找到数据文件「{file_name}」")
    sheets = store.list_sheets(file_rec["id"]) or []
    if not sheets:
        raise ValueError(f"文件「{file_name}」没有工作表")
    if sheet_name:
        sheet = next((s for s in sheets if s.get("sheet_name") == sheet_name), None)
        if sheet is None:
            raise ValueError(f"未找到工作表「{sheet_name}」")
    else:
        sheet = next(
            (s for s in sheets if not any(k in (s.get("sheet_name") or "")
                                          for k in ("说明", "目录", "封面"))),
            sheets[0],
        )
    headers = sheet.get("headers") or []
    return [h for h in headers if h.get("name")]


def _is_period_col(col: dict) -> bool:
    """期间列识别（日期类型 / 列名含日期/时间/年/月/期间关键词）。"""
    name = str(col.get("name") or "")
    typ = str(col.get("type") or "").lower()
    if "date" in typ or "datetime" in typ or "time" in typ:
        return True
    return any(k in name for k in ("日期", "时间", "期间", "月份", "年月", "年度"))


def _suggest_col_mapping(var_name: str, all_cols: list[str]) -> str:
    """模型变量名 → 列名相似度匹配（精确 > 归一化包含）。"""
    if var_name in all_cols:
        return var_name
    for c in all_cols:
        if _similar(c, var_name):
            return c
    # 变量名含单位后缀变体（如 净利润(万元) → 净利润）
    return ""


def _finmod_varmap(store: ProjectDataStore, project_id: str,
                   file_name: str, sheet_name: str,
                   model_ids: list[str],
                   file_names: list[str] | None = None) -> dict:
    """模型所需变量 × 文件列名 → 预填映射建议（确定性相似度匹配 + 方向/类型）。

    v2.0 V1：variables 增加 direction（input/statistic/output）+ var_type（numeric/category/period）。
      - direction=input：需用户映射列（字符串相似度预填）
      - direction=statistic：统计量（n/x̄/s/CV），不映射列，`suggested_mode=statistic`（V2 AI 绑定函数）
      - direction=output：模型输出参数，不映射（展示说明）

    R1 多文件（2026-08-23）：`file_names` 传入时，用 `finmod_merge.collect_all_columns`
    合并多文件列名索引（all_cols 并集 + period_cols），变量映射对**所有文件**生效。
    兼容旧调用：`file_names` 为空且 `file_name` 给定时走原单文件逻辑。

    Returns:
        {
          file(s), sheet, all_cols, period_cols,
          file_of_col: {col: files},   # R1：每列来源文件（多文件时）
          models: [{model_id, code, name, variables: [
              {var_name, var_meaning, var_unit, direction, var_type,
               mapped_col, status, suggested_mode}
          ]}]
        }
    """
    # R1：多文件列索引合并
    from tools.finmod_merge import collect_all_columns
    multi = file_names and len(file_names) > 0
    if multi:
        col_idx = collect_all_columns(store, project_id, file_names, sheet_name)
        all_cols = col_idx["all_cols"]
        period_cols = col_idx["period_cols"]
        file_of_col = {c["name"]: c["files"] for c in col_idx["cols_info"]}
    else:
        cols_info = _sheet_columns(store, project_id, file_name, sheet_name)
        all_cols = [c["name"] for c in cols_info]
        period_cols = [c["name"] for c in cols_info if _is_period_col(c)]
        file_of_col = {c["name"]: [file_name] for c in cols_info}
    models_out: list[dict] = []
    for mid in model_ids:
        detail = finmod_refs.get_model_detail(mid)
        if not detail:
            continue
        variables = detail.get("variables") or []
        vars_out: list[dict] = []
        for v in variables:
            var_name = str(v.get("var_name") or "").strip()
            if not var_name:
                continue
            direction = str(v.get("direction") or "input")
            var_type = str(v.get("var_type") or "numeric")
            mapped = ""
            status = "missing"
            suggested_mode = "direct"
            if direction == "input":
                mapped = _suggest_col_mapping(var_name, all_cols)
                status = "matched" if mapped else "missing"
                # 类型感知：numeric 变量优先数值列（列名相似 + 数值列优先）；period 只接受期间列
                if not mapped and var_type == "period":
                    mapped = period_cols[0] if period_cols else ""
                    status = "matched" if mapped else "missing"
            elif direction == "statistic":
                # 统计量变量：不映射列；由 AI/前端绑定统计函数 + 来源列（V2）
                suggested_mode = "statistic"
                status = "statistic"  # 展示为「自动统计」而非缺失
            else:  # output
                suggested_mode = "output"
                status = "output"
            vars_out.append({
                "var_name": var_name,
                "var_meaning": str(v.get("var_meaning") or ""),
                "var_unit": str(v.get("var_unit") or ""),
                "direction": direction,
                "var_type": var_type,
                "mapped_col": mapped,
                "status": status,
                "suggested_mode": suggested_mode,
            })
        models_out.append({
            "model_id": detail["id"],
            "code": detail["code"],
            "name": detail["name"],
            "variables": vars_out,
        })
    return {
        "file": file_name,
        "files": file_names if multi else [file_name],
        "file_of_col": file_of_col,  # R1：列 → 来源文件列表（多文件时）
        "sheet": sheet_name,
        "all_cols": all_cols,
        "period_cols": period_cols,
        "models": models_out,
    }


def finmod_varmap(p: dict):
    """变量预填映射：模型所需变量 × 文件列名 → 建议（确定性，无 LLM）。

    body: {project_id, file_name?, file_names?: [], sheet_name?, model_ids: []}
    R1：传 file_names[] 时做多文件列索引合并（对所有文件映射）。
    """
    project_id = (p.get("project_id") or "").strip()
    file_name = (p.get("file_name") or "").strip()
    file_names = p.get("file_names") or []
    if isinstance(file_names, str):
        file_names = [file_names]
    file_names = [f for f in file_names if f]
    # 兼容：单文件传 file_name → 归一化为 file_names
    if not file_names and file_name:
        file_names = [file_name]
    model_ids = p.get("model_ids") or []
    if not project_id:
        return error("缺少 project_id")
    if not file_names:
        return error("缺少 file_name/file_names")
    if not model_ids:
        return error("缺少 model_ids")
    try:
        store = ProjectDataStore(get_db())
        result = _finmod_varmap(store, project_id, file_names[0],
                                (p.get("sheet_name") or ""), list(model_ids),
                                file_names=list(file_names))
        # 统计缺失（只统计 direction=input 的变量；statistic/output 不算缺失）
        input_vars = [v for m in result["models"] for v in m["variables"]
                      if v["direction"] == "input"]
        missing = sum(1 for v in input_vars if v["status"] == "missing")
        statistic_n = sum(1 for m in result["models"] for v in m["variables"]
                          if v["direction"] == "statistic")
        result["missing_count"] = missing
        result["input_count"] = len(input_vars)
        result["statistic_count"] = statistic_n
        result["file_count"] = len(file_names)
        result["message"] = (
            f"共 {len(input_vars)} 个输入变量，{missing} 个未匹配"
            f"（可手动选列或派生）" + (f"；{statistic_n} 个统计量将自动计算" if statistic_n else "")
            + (f"；解析 {len(file_names)} 个文件" if len(file_names) > 1 else "")
        )
        return success(result)
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/varmap 失败")
        return error(f"变量映射失败: {e}")


def _build_varmap_user_content(project_id: str, file_name: str, sheet_name: str,
                               model_ids: list[str],
                               file_names: list[str] | None = None) -> tuple[str, dict]:
    """组装 varmap-suggest 的 LLM 用户消息；返回 (content, base_result)。

    base_result = `_finmod_varmap` 确定性结果（LLM 降级兜底 + 统计量/输出标记）。
    R1：file_names 传入时列索引按多文件合并（含来源文件标注）。
    """
    store = ProjectDataStore(get_db())
    base = _finmod_varmap(store, project_id, file_name, sheet_name, model_ids,
                          file_names=file_names)
    lines: list[str] = []
    # 列名索引（含类型 + 前 3 样本值 + 来源文件）
    if file_names and len(file_names) > 0:
        from tools.finmod_merge import collect_all_columns
        col_idx = collect_all_columns(store, project_id, file_names, sheet_name)
        samples = {}
        for fn in file_names:
            try:
                samples.update(_col_samples(store, project_id, fn, sheet_name))
            except Exception:  # noqa: BLE001
                continue
        lines.append("数据文件列名索引（含类型/样本值/来源文件，只读不执行）：")
        for c in col_idx["cols_info"]:
            name = c["name"]
            sam = "、".join(str(x) for x in (samples.get(name) or [])[:3])
            src = "、".join(c.get("files") or [])
            lines.append(f"- {name}（类型:{c.get('type') or '未知'}，样本:{sam or '—'}，来源:{src or '—'}）")
        lines.append(f"期间列候选：{'、'.join(base['period_cols']) or '（无）'}")
    else:
        cols_info = _sheet_columns(store, project_id, file_name, sheet_name)
        samples = _col_samples(store, project_id, file_name, sheet_name)
        lines.append("数据文件列名索引（含类型与样本值，只读不执行）：")
        for c in cols_info:
            name = c.get("name") or ""
            sam = "、".join(str(x) for x in (samples.get(name) or [])[:3])
            lines.append(f"- {name}（类型:{c.get('type') or '未知'}，样本:{sam or '—'}）")
        lines.append(f"期间列候选：{'、'.join(base['period_cols']) or '（无）'}")
    lines.append("")
    # 模型变量定义（只列出 input + statistic；output 跳过）
    lines.append("模型变量（direction 标注）：")
    for m in base["models"]:
        lines.append(f"## {m['code']} {m['name']}（{m['model_id']}）")
        for v in m["variables"]:
            tag = f"[{v['direction']}] {v['var_name']}（{v['var_meaning'] or '无含义'}，{v['var_type']}）"
            if v["direction"] == "input":
                tag += f"，当前预填: {v['mapped_col'] or '（未匹配）'}"
            lines.append(f"- {tag}")
    return "\n".join(lines), base


def _col_samples(store: ProjectDataStore, project_id: str, file_name: str,
                 sheet_name: str) -> dict[str, list]:
    """每列前 3 个非空样本值（LLM 类型判断用；只读主库，读前 5 行）。"""
    try:
        from tools.finmod_eval import load_dataframe
        df = load_dataframe(store, project_id, file_name, sheet_name)
        out: dict[str, list] = {}
        for c in df.columns[:20]:
            vals = [str(v) for v in df[c].dropna().head(3).tolist()]
            out[str(c)] = vals
        return out
    except Exception:  # noqa: BLE001 —— 样本读取失败不阻塞（列名索引仍可用）
        return {}


def finmod_prep_data(p: dict):
    """数据预处理：steps 步骤链 → 变换后列信息 + 预览 + 日志。

    body: {project_id, file_name?, file_names?: [], sheet_name?, steps: [{op, ...}]}
    R1：file_names 传入（多文件）时先自动合并成一个 DataFrame，再执行步骤链。
    Returns: {success, columns: [{name, type}], rows, preview: [前5行], logs, errors}
    """
    project_id = (p.get("project_id") or "").strip()
    file_name = (p.get("file_name") or "").strip()
    file_names = p.get("file_names") or []
    if isinstance(file_names, str):
        file_names = [file_names]
    file_names = [f for f in file_names if f]
    if not file_names and file_name:
        file_names = [file_name]
    steps = p.get("steps") or []
    if not project_id or not file_names:
        return error("缺少 project_id/file_name")
    if not steps:
        return error("缺少 steps")
    try:
        from tools.finmod_prep import run_prep_steps
        from tools.finmod_merge import merge_files_for_model
        store = ProjectDataStore(get_db())
        # R1：多文件先自动合并再预处理（单文件走原逻辑）
        base_df = None
        if len(file_names) > 1:
            merged = merge_files_for_model(store, project_id, file_names, (p.get("sheet_name") or ""))
            base_df = merged["df"]
        result = run_prep_steps(store, project_id, file_names[0],
                                (p.get("sheet_name") or ""), list(steps), base_df=base_df)
        # 不返回内存 df（仅预览/日志；run 时再执行同一 steps）
        result.pop("df", None)
        return success(result)
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/prep-data 失败")
        return error(f"数据预处理失败: {e}")


def finmod_derived_eval(p: dict):
    """派生变量表达式求值（_finmod_eval，确定性，无 LLM）。

    body: {project_id, file_name?, file_names?: [], sheet_name?, formulas: ["利润=收入-成本"],
           mappings?: {模型变量名: 实际列名}}
    R1：file_names 传入时多文件合并成一个 DataFrame 求值。
    Returns:
        {results: [{name, type, sample[], rows, dtype}], errors: [{line, name, kind, message}],
         all_cols, row_count}
    """
    project_id = (p.get("project_id") or "").strip()
    file_name = (p.get("file_name") or "").strip()
    file_names = p.get("file_names") or []
    if isinstance(file_names, str):
        file_names = [file_names]
    file_names = [f for f in file_names if f]
    if not file_names and file_name:
        file_names = [file_name]
    formulas = p.get("formulas") or []
    mappings = p.get("mappings") or {}
    if not project_id or not file_names:
        return error("缺少 project_id/file_name")
    if not formulas:
        return success({"results": {}, "errors": [], "all_cols": [], "row_count": 0})
    try:
        from tools.finmod_eval import eval_named_formulas
        from tools.finmod_merge import merge_files_for_model
        store = ProjectDataStore(get_db())
        if len(file_names) > 1:
            merged = merge_files_for_model(store, project_id, file_names, (p.get("sheet_name") or ""))
            df = merged["df"]
        else:
            from tools.finmod_eval import load_dataframe
            df = load_dataframe(store, project_id, file_names[0], (p.get("sheet_name") or ""))
        results, errors = eval_named_formulas(
            [str(f) for f in formulas], df, aliases=mappings, limit=5,
        )
        return success({
            "results": results,
            "errors": errors,
            "all_cols": [str(c) for c in df.columns],
            "row_count": int(df.shape[0]),
        })
    except Exception as e:  # noqa: BLE001
        logger.exception("finmod/derived-eval 失败")
        return error(f"派生变量求值失败: {e}")
