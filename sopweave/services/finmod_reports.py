"""Finmod reports application service."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from services.results import error, success
from storage.connections import get_connection as get_db

from services.app_data import _project
from storage.report_store import ReportStore
from agent_runtime.workspace import atomic_bytes, file_version, FileVersionConflict
from services.app_models import _build_llm_client, _chat_complete

logger = logging.getLogger(__name__)

REPORT_SYSTEM = (
    "你是「财务建模与分析」集成应用的**分析报告**撰写 AI。\n"
    "输入是确定性引擎输出的模型结果摘要 JSON + 图表清单（含已落盘路径）"
    "+ 数据统计摘要 + 用户诉求 + 图表风格偏好。\n"
    "请撰写**专业、克制、数据驱动的中文分析报告**（Markdown），面向无财务基础的业务人员。\n"
    "文风对标券商研究所研究报告：先结论后论证、客观陈述、用数值说话。\n\n"
    "硬性规则：\n"
    "1. 报告结构（结论先行）：\n"
    "   # 标题（一句话点题，无修饰）→ ## 一、核心观点（2-4 条判断+关键数值）\n"
    "   → ## 二、分析基础（数据来源/规模/统计表/口径/假设缺失）\n"
    "   → ## 三、分析过程（方法简述 1-2 句 → 发现 1/2/3，每发现=判断+数值依据+图 X）\n"
    "   → ## 四、结论与建议（可执行建议 + 风险提示）\n"
    "   → （可选）## 附录：术语速查（仅确有必要）\n"
    "2. 所有公式用 $$...$$ 包裹（禁止单 $，货币金额用普通文本；中文用 \\text{}）\n"
    "3. 图表引用：直接用图表清单给出的相对路径 ![图N](<图表清单里的路径>)（路径已含\n"
    "   报告目录前缀，直接使用，勿改动）；每张图正文必须有解读（图 X 显示……）；\n"
    "   图表数量与内容匹配，不堆砌\n"
    "4. 只输出报告本身，不要多余解释、不要客套、不要 markdown 围栏\n"
    "5. ★降 AI 味（最优先，违反即不合格）：\n"
    "   - 禁止比喻/象征（宛如、犹如、护城河、引擎、灯塔）、华丽形容词（卓越、显著、璀璨、稳健前行、赋能、助力）\n"
    "   - 禁止感叹号、排比句、对仗；禁止滥用引号「」和破折号「——」\n"
    "   - 禁止空泛结论（总体向好、值得关注、表现亮眼）——所有定性必须带数值\n"
    "   - 克制表达：用「较为/相对/存在/我们判断」代替「非常/明显/显然/大幅」（无对比时）\n"
    "   - 先判断后论证：每条观点先给结论一句话，再给数值/图依据\n"
    "   - 面向单点分析：不写宏观综述、不写行业全貌，只聚焦本次分析主题\n"
    "6. **解读而非复述**：每条发现=判断 + 数值 + 业务含义，禁止只贴数字\n"
    "7. 面向无基础读者：术语首次出现给 1 句白话解释；假设缺失时明确说明\n"
    "8. ★v3.6 若输入含「数据缺失清单」，在「二、分析基础」末尾追加「数据补充说明」："
    "列出每个模型的缺失变量、一句话说明对结果的影响、建议补充什么数据或如何用已有列派生"
)


def _build_report_user_content(goal: str, models: list[dict],
                               charts: list[dict],
                               data_stats: dict | None = None,
                               style_prefs: dict | None = None) -> str:
    """组装 LLM 用户消息：诉求 + 模型结果摘要 + 缺失变量 + 图表清单 + 数据统计 + 风格偏好。

    R5：新增 data_stats（核心数据统计供 AI 解读）与 style_prefs（图表风格偏好，
    作为「优先建议」供 AI 参考决定图表构成）。
    """
    lines = [f"用户诉求：{goal or '（未提供）'}", ""]
    # R5：数据统计摘要（供「二、数据概览」引用）
    if data_stats:
        lines.append("## 数据统计摘要（确定性引擎，供「数据概览」节引用）")
        lines.append(json.dumps(data_stats, ensure_ascii=False)[:4000])
        lines.append("")
    # R5：图表风格偏好（用户表达；AI 据此决定图表构成，可增减）
    if style_prefs:
        prefs_zh = {
            "level": {"L1": "简单", "L2": "中等", "L3": "复杂"}.get(str(style_prefs.get("level")), style_prefs.get("level")),
            "colors": style_prefs.get("colors"),
            "categories": style_prefs.get("categories"),
        }
        lines.append("## 图表风格偏好（用户表达，作为优先建议；AI 可据内容增减图表）")
        lines.append(json.dumps(prefs_zh, ensure_ascii=False))
        lines.append("")
    lines.append("## 模型结果摘要（确定性引擎输出，JSON）")
    lines.append(json.dumps([
        {"model_id": m.get("code"), "name": m.get("name"),
         "summary": m.get("summary", {}), "ok": m.get("ok", False),
         "missing_vars": m.get("missing_vars") or []}  # v3.6：缺失变量（报告补充说明）
        for m in models
    ], ensure_ascii=False)[:12000])
    # v3.6：缺失变量汇总（供报告「数据补充说明」节）
    all_missing = {}
    for m in models:
        if m.get("missing_vars"):
            all_missing.setdefault(f"{m.get('code')} {m.get('name')}", []).extend(m["missing_vars"])
    if all_missing:
        lines.append("")
        lines.append("## 数据缺失清单（v3.6）")
        for k, vars in all_missing.items():
            lines.append(f"- {k}：{ '、'.join(vars) }")
    lines.append("")
    lines.append("## 图表清单（已落盘，相对路径直接引用；带 importance 供 AI 参考优先级）")
    for ch in charts:
        rel = ch.get("rel_path") or ch.get("path") or ""
        imp = ch.get("importance") or "secondary"
        lines.append(f"- {ch.get('name') or ch.get('template')} (importance={imp}): ![图]({rel})")
    return "\n".join(lines)


def _fallback_md_report(goal: str, models: list[dict], charts: list[dict],
                        data_stats: dict | None = None,
                        style_prefs: dict | None = None) -> str:
    """模型不可用时的确定性降级报告（R5.1：核心观点/分析基础/分析过程/结论建议）。"""
    lines = [
        f"# {goal or '财务建模分析'}（确定性引擎）", "",
        "## 一、核心观点", "",
        f"本次分析围绕「{goal or '未提供诉求'}」，由确定性财务引擎执行"
        f"（0 AI 参与计算），所选模型 {len(models)} 个，产出图表 {len(charts)} 张。", "",
    ]
    # R5.1：分析基础（数据概览）
    lines.append("## 二、分析基础（数据与口径）")
    lines.append("")
    if data_stats:
        rows = data_stats.get("rows") or data_stats.get("row_count") or 0
        cols = data_stats.get("columns") or len(data_stats.get("cols") or [])
        files = data_stats.get("files") or []
        lines.append(f"- 数据量：{rows} 行 × {cols} 列" + (f"，来源文件：{'、'.join(files)}" if files else ""))
        for k, v in list(data_stats.items()):
            if k in ("rows", "row_count", "columns", "cols", "files"):
                continue
            lines.append(f"- {k}：{json.dumps(v, ensure_ascii=False)[:200]}")
        lines.append("")
    else:
        lines.extend(["- 数据量以执行引擎 row_count 为准；变量口径见各模型映射。", ""])
    lines.append("## 三、分析过程（模型与方法）")
    lines.append("")
    lines.append("本次分析由确定性财务引擎执行（0 AI 参与计算），所选模型如下：")
    lines.append("")
    for m in models:
        lines.append(f"- **{m.get('code')} {m.get('name')}**"
                     f"{'（执行成功）' if m.get('ok') else '（执行失败）'}")
    lines.append("")
    lines.append("| 模型 | 结果摘要 |")
    lines.append("|---|---|")
    for m in models:
        summary = json.dumps(m.get("summary", {}), ensure_ascii=False)[:500]
        lines.append(f"| {m.get('code')} {m.get('name')} | {summary} |")
    lines.append("")
    if charts:
        lines.append("## 图表")
        lines.append("")
        for ch in charts:
            rel = ch.get("rel_path") or ch.get("path") or ""
            lines.append(f"![{ch.get('name') or ch.get('template')}]({rel})")
        lines.append("")
    # v3.6：数据缺失说明（确定性文本，AI 不可用降级时也给出）
    missing_models = [m for m in models if m.get("missing_vars")]
    if missing_models:
        lines.append("## 数据补充说明")
        lines.append("")
        lines.append("> 注：以下模型因数据缺失，结果仅供参考；建议补充数据后重新执行。")
        for m in missing_models:
            mv = m.get("missing_vars") or []
            lines.append(f"- **{m.get('code')} {m.get('name')}**：缺少 {'、'.join(mv)}")
        lines.append("")
    lines.append("## 四、结论与建议")
    lines.append("")
    lines.append("> 注：当前模型不可用，本报告由确定性引擎直接生成；如需 AI 归因与建议，请检查模型配置后重新执行。")
    return "\n".join(lines)


def _finmod_work_dir(project_id: str) -> str:
    """获取隐藏项目 work_dir（找不到则回退默认 finmod 工作目录）。"""
    proj = _project(project_id)
    if proj and proj.get("work_dir"):
        return str(proj["work_dir"])
    return error("报告项目不存在", 404)


def _finmod_report_dir(run_id: str, project_id: str) -> str:
    return str(ReportStore(get_db()).allocate(project_id, run_id, Path(_finmod_work_dir(project_id))))


def _finmod_report_rel_base(run_id: str, project_id: str) -> str:
    """返回「财务分析报告N」相对项目根的路径（如 财务分析报告/财务分析报告1）。"""
    work_dir = Path(_finmod_work_dir(project_id)).resolve()
    report_dir = Path(_finmod_report_dir(run_id, project_id)).resolve()
    return report_dir.relative_to(work_dir).as_posix()


def finmod_save_chart(p: dict):
    """图表落盘：SVG/PNG 字符串 → 隐藏项目「财务分析报告N/」（阶段 1）。

    body: {run_id, project_id, filename, content, format, mime?}
    Returns: {path, rel_path(相对项目根), filename}
    """
    run_id = (p.get("run_id") or "").strip()
    project_id = (p.get("project_id") or "").strip()
    filename = (p.get("filename") or "").strip()
    content = p.get("content") or ""
    fmt = (p.get("format") or "svg").lower()
    if not run_id or not filename or not content:
        return error("缺少 run_id/filename/content")
    if not filename.endswith(f".{fmt}"):
        filename = f"{filename}.{fmt}"
    # 文件名安全（防路径穿越）
    safe_name = os.path.basename(filename)
    # 图片与报告同处「财务分析报告N」目录（与 md 平级）
    report_dir = _finmod_report_dir(run_id, project_id)
    full_path = os.path.join(report_dir, safe_name)
    try:
        if fmt == "png":
            import base64
            raw = base64.b64decode(content, validate=True)
        else:
            raw = content.encode("utf-8")
        target = Path(full_path)
        version = atomic_bytes(target, raw, p.get("expected_version") or file_version(target))
        rel_base = _finmod_report_rel_base(run_id, project_id)
        return success({
            "path": full_path,
            "rel_path": f"{rel_base}/{safe_name}",
            "filename": safe_name,
            "version": version,
        })
    except Exception as e:  # noqa: BLE001
        if isinstance(e, FileVersionConflict):
            return error(str(e), 409)
        logger.exception("finmod/save-chart 失败")
        return error(f"图表落盘失败: {e}")


def finmod_export_md(p: dict):
    """报告组装：LLM 报告正文 + 图表相对路径 → 单文件 md 落盘 run 目录。

    body: {project_id, run_id, goal, model_id, models[], charts[],
           data_stats?, style_prefs?}   # R5：数据统计 + 风格偏好
    Returns: {path, rel_path, filename, report, used_llm}
    """
    project_id = (p.get("project_id") or "").strip()
    run_id = (p.get("run_id") or "").strip()
    goal = (p.get("goal") or "").strip()
    model_id = (p.get("model_id") or "").strip()
    models = list(p.get("models") or [])
    charts = list(p.get("charts") or [])
    data_stats = p.get("data_stats") or {}   # R5：数据统计摘要（run 返回）
    style_prefs = p.get("style_prefs") or {}  # R5：图表风格偏好
    if not run_id:
        return error("缺少 run_id")

    report = ""
    used_llm = False
    try:
        client = _build_llm_client(model_id)
    except ValueError as exc:
        logger.warning("财务建模报告模型不可用，降级确定性报告: %s", exc)
        client = None
    if client:
        try:
            report = _chat_complete(
                client,
                [
                    {"role": "system", "content": REPORT_SYSTEM},
                    {"role": "user", "content": _build_report_user_content(
                        goal, models, charts, data_stats, style_prefs)},
                ],
                temperature=0.3,
                max_tokens=6000,
            )
            if report.strip():
                used_llm = True
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM 报告生成失败，降级为确定性报告: %s", e)
    if not report.strip():
        report = _fallback_md_report(goal, models, charts, data_stats, style_prefs)

    # 阶段 1：报告落盘到隐藏项目「财务分析报告N/财务分析报告N.md」
    report_dir = _finmod_report_dir(run_id, project_id)
    n = Path(report_dir).name  # 财务分析报告N
    filename = f"{n}.md"
    full_path = os.path.join(report_dir, filename)
    try:
        version = atomic_bytes(Path(full_path), report.encode("utf-8"), p.get("expected_version") or "missing")
        rel_path = f"{_finmod_report_rel_base(run_id, project_id)}/{filename}"
        from storage.workbench_store import WorkbenchStore
        if get_db().execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
            WorkbenchStore(get_db()).append_event(run_id,'app.report',{'run_id':run_id,'project_id':project_id,'rel_path':rel_path,'filename':filename,'version':version,'goal':goal,'used_llm':used_llm})
        return success({
            "path": full_path,
            "rel_path": rel_path,
            "filename": filename,
            "report": report,
            "used_llm": used_llm,
            "version": version,
        })
    except Exception as e:  # noqa: BLE001
        if isinstance(e, FileVersionConflict):
            return error(str(e), 409)
        logger.exception("finmod/export-md 失败")
        return error(f"报告落盘失败: {e}")
