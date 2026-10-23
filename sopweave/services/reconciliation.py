"""Reconciliation application service."""
from __future__ import annotations
import json
import logging
import os
import re
import threading
from typing import Any
from services.results import ServiceError, error, success
from storage.workbench_store import WorkbenchStore
from storage.connections import get_connection as get_db
from storage.project_data_store import ProjectDataStore
from storage.tmp_db import TmpRun, get_run, register_run, unregister_run
from tools.reconcile_tool import _valid_columns, reconcile_variables

from services.app_data import _copy_project_data_to_tmp, _file_sheets, _match_amount_col, _pick_base_sheet, _project
from services.app_models import _build_llm_client, _chat_complete

logger = logging.getLogger(__name__)

MAX_DIFFS = 500
_RECON_WRITEBACK_LOCK = threading.Lock()


REPORT_SYSTEM = (
    "你是「数据核对与校验」集成应用的报告撰写 AI。\n"
    "输入是确定性核对引擎输出的差异数据与核对参数，请据此撰写专业的中文核对报告。\n"
    "只输出报告本身，不要任何多余解释、不要客套。\n\n"
    "报告结构（Markdown）：\n"
    "1. 核对信息（依据表/分表/匹配键/核对变量/容差/匹配不上处理）\n"
    "2. 总览（OK/A1/A2/B/C 计数）\n"
    "3. 差异明细表（Markdown 表格：级别/关键值/依据金额/目标金额/差异/原因，只列有差异的行）\n"
    "4. 原因分类（按 reason 汇总各类条数）\n"
    "5. 修改方案（按级别给出处理建议：A 类自动填充/修正，B 类人工逐条确认，C 类人工复核）\n\n"
    "金额保留两位小数并加千分位；使用中文。"
)


def _fmt_amount(v: Any) -> str:
    if v is None or v == "" or v == "-":
        return "—"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _fallback_report(
    params: dict,
    result: dict,
    *,
    reason: str = "model_unavailable",
) -> str:
    """确定性降级报告，说明未使用 AI 报告的真实原因。"""
    s = result.get("summary", {})
    mk = params.get("matchKeys") or {}
    keys = [mk.get("k1") or "", mk.get("k2On") and mk.get("k2") or "",
            mk.get("k3On") and mk.get("k3") or ""]
    keys = [k for k in keys if k]
    heading = (
        "确定性核对报告（AI 事实校验未通过）"
        if reason == "fact_correction"
        else "核对报告（确定性引擎 · 未接入 AI 归因）"
    )
    lines = [
        f"## {heading}",
        "",
        "### 核对信息",
        f"- 依据表：{params.get('baseFile')}",
        f"- 分表：{params.get('detailFile')}",
        f"- 匹配键：{' > '.join(keys) if keys else '—'}",
        f"- 核对变量：{(params.get('variables') or [])[0] or '—'}",
        f"- 依据表金额列：{params.get('baseAmountCol') or '（自动匹配）'}",
        f"- 容差：{params.get('tolerance')} 元",
        "",
        "### 总览",
        "| 级别 | 数量 | 说明 |",
        "|---|---|---|",
        f"| OK | {s.get('ok', 0)} | 一致 |",
        f"| A1 | {s.get('a1', 0)} | 漏填缺失（应填充） |",
        f"| A2 | {s.get('a2', 0)} | 小额误差（应修正） |",
        f"| B | {s.get('b', 0)} | 待确认 |",
        f"| C | {s.get('c', 0)} | 无法匹配（人工复核） |",
        f"| 合计 | {s.get('total', 0)} | 依据表行数 |",
        "",
        f"匹配情况：总计 {s.get('total', 0)} 行，匹配 {s.get('matched', 0)} 行，"
        f"未匹配（C）{s.get('c', 0)} 行。",
        "",
        "### 差异明细",
        "",
    ]
    diffs = result.get("differences", [])[:50]
    if diffs:
        lines.append("| 级别 | 关键值 | 依据金额 | 目标金额 | 差异 | 原因 |")
        lines.append("|---|---|---|---|---|---|")
        for d in diffs:
            lines.append(
                f"| {d.get('level', 'B')} | {d.get('key1', '—')} "
                f"| {_fmt_amount(d.get('source_amount'))} | {_fmt_amount(d.get('target_amount'))} "
                f"| {_fmt_amount(d.get('diff'))} | {d.get('reason', '—')} |"
            )
    else:
        lines.append("（无差异记录）")
    unmatched = result.get("unmatched", [])[:20]
    if not unmatched:
        unmatched = [
            {"key": item.get("key", "—"), "reason": item.get("reason", "")}
            for item in result.get("audit_items", [])[:20]
        ]
    if unmatched:
        lines.append("")
        lines.append("### 无法匹配（C 类）")
        for u in unmatched:
            lines.append(f"- {u.get('key', '—')}：{u.get('reason', '')}")
    lines.append("")
    if reason == "fact_correction":
        lines.append("> 注：AI 报告与核对引擎事实不一致，系统已改用确定性核对结果生成报告，以确保计数与明细准确。")
    else:
        lines.append("> 注：当前模型不可用，本报告由核对引擎直接生成；如需 AI 归因与修改方案，请检查模型配置后重新核对。")
    return "\n".join(lines)


def _report_facts_consistent(report: str, result: dict) -> bool:
    """Reject report prose that contradicts the deterministic summary."""
    if not isinstance(report, str) or not report.strip():
        return False
    s = result.get("summary", {})
    expected = {
        "OK": int(s.get("ok") or 0),
        "A1": int(s.get("a1") or 0),
        "A2": int(s.get("a2") or 0),
        "B": int(s.get("b") or 0),
        "C": int(s.get("c") or 0),
    }
    text = report.replace("｜", "|")
    for label, count in expected.items():
        if not re.search(
            rf"(?<![A-Za-z0-9]){re.escape(label)}\s*(?:\||:|：)?\s*{count}(?!\d)",
            text,
            re.IGNORECASE,
        ):
            return False
    claimed = [
        int(value)
        for value in re.findall(r"未匹配(?:（C）)?\s*[:：]?\s*(\d+)", text)
    ]
    if any(value != expected["C"] for value in claimed):
        return False
    if expected["C"] and re.search(
        r"本次无(?:任何)?(?:匹配不上的|未匹配|无法匹配)(?:记录|项)?",
        text,
    ):
        return False
    return True


def _build_user_content(params: dict, result: dict) -> str:
    """组装用户消息：核对参数 + 差异摘要（差异 JSON 截断控制 token）。"""
    mk = params.get("matchKeys") or {}
    keys = [mk.get("k1") or "", mk.get("k2On") and mk.get("k2") or "",
            mk.get("k3On") and mk.get("k3") or ""]
    keys = [k for k in keys if k]
    s = result.get("summary", {})
    action_raw = params.get("unmatchedAction") or "list"
    action_label = {
        "list": "A. 列清单供人工复核",
        "fallback": "B. 按（人员+客户）降级匹配",
        "mark": "C. 全部标记无法匹配",
    }.get(action_raw, f"自定义：{action_raw}")
    lines = [
        "## 核对参数（用户向导已确认）",
        f"- 依据表：{params.get('baseFile')}",
        f"- 分表：{params.get('detailFile')}",
        f"- 匹配键层级：{' > '.join(keys) if keys else '—'}",
        f"- 核对变量（分表字段）：{(params.get('variables') or [])[0] or '—'}",
        f"- 依据表金额列：{params.get('baseAmountCol') or '（自动匹配）'}",
        f"- 容差：{params.get('tolerance')} 元",
        f"- 匹配不上处理：{action_label}",
        f"- 差异自动处理：{'开启（A 类自动写回）' if params.get('autoFix') else '关闭（仅报告不写回）'}",
        "",
        "## 核对结果总览",
        f"总计 {s.get('total', 0)} 行，匹配 {s.get('matched', 0)}；"
        f"未匹配（C）{s.get('c', 0)} 行；"
        f"OK {s.get('ok', 0)}，A1 {s.get('a1', 0)}，A2 {s.get('a2', 0)}，"
        f"B {s.get('b', 0)}，C {s.get('c', 0)}。",
        "",
        "## 差异明细（JSON，供撰写报告；全部非 OK 差异）",
        json.dumps(
            {
                "differences": result.get("differences", []),
                "unmatched": result.get("unmatched", []),
            },
            ensure_ascii=False,
        )[:12000],
    ]
    return "\n".join(lines)


def _tool_to_recon(result: dict) -> dict:
    """把 reconcile_variables 输出转成前端 ReconData 结构（差异表直出）。"""
    s = result.get("summary", {})
    diffs: list[dict] = []
    for d in result.get("differences", []):
        key1 = d.get("key1") or ""
        key2 = d.get("key2") or ""
        diffs.append({
            "level": (d.get("level") or "B").upper(),
            "contract": str(key1),
            "person": str(key2) if key2 and key2 != key1 else "",
            "client": "",
            "base_amount": d.get("source_amount"),
            "target_amount": d.get("target_amount"),
            "diff": d.get("diff"),
            "reason": d.get("reason") or "",
            "suggestion": "",
        })
    for u in result.get("unmatched", []):
        diffs.append({
            "level": "C",
            "contract": str(u.get("key") or ""),
            "person": "",
            "client": "",
            "base_amount": None,
            "target_amount": None,
            "diff": None,
            "reason": u.get("reason") or "目标表无对应记录",
            "suggestion": "",
        })
    return {
        "summary": {
            "ok": int(s.get("ok") or 0),
            "a1": int(s.get("a1") or 0),
            "a2": int(s.get("a2") or 0),
            "b": int(s.get("b") or 0),
            "c": int(s.get("c") or 0),
        },
        "diffs": diffs[:MAX_DIFFS],
    }


def _resolve_verify_params(params: dict, store: ProjectDataStore,
                          project_id: str) -> dict:
    """从向导参数解析核对参数（匹配键/金额列/sheet），供 verify/audit 共用。"""
    mk = params.get("matchKeys") or {}
    match_keys = [mk.get("k1") or "", mk.get("k2On") and mk.get("k2") or "",
                  mk.get("k3On") and mk.get("k3") or ""]
    match_keys = [k for k in match_keys if k]
    variables = params.get("variables") or []
    target_col = variables[0] if variables else ""
    if not target_col:
        raise ValueError("缺少核对变量（variables）")
    try:
        tolerance = float(1 if params.get("tolerance") in (None,'') else params['tolerance'])
    except (TypeError, ValueError):
        tolerance = 1.0
    if tolerance < 0:
        raise ValueError("容差不能为负数")
    action_raw = params.get("unmatchedAction") or "list"
    unmatched_action = "fallback_person_customer" if action_raw == "fallback" else "list"

    base_file = params.get("baseFile") or ""
    detail_file = params.get("detailFile") or ""
    if not base_file or not detail_file:
        raise ValueError("缺少依据表/分表")

    # 依据表金额列：优先用向导显式选择（baseAmountCol）；未提供时自动匹配
    src_col = (params.get("baseAmountCol") or "").strip() or None
    if not src_col:
        src_col = _match_amount_col(store, project_id, base_file, detail_file, target_col)
    if src_col is None:
        raise ValueError(f"无法在依据表中找到与核对变量「{target_col}」匹配的金额列，请检查参数")
    amount_col = f"{src_col}->{target_col}" if src_col != target_col else target_col

    # 依据表 sheet 自动选择（跳过说明页，取含匹配键的数据主体 sheet）
    base_sheet = _pick_base_sheet(store, project_id, base_file, match_keys, src_col)
    return {
        "base_file": base_file,
        "detail_file": detail_file,
        "base_sheet": base_sheet,
        "match_keys": match_keys,
        "amount_col": amount_col,
        "tolerance": tolerance,
        "unmatched_action": unmatched_action,
        "src_col": src_col,
    }


def recon_verify(p: dict):
    project_id = p.get("project_id") or ""
    model_id = p.get("model_id") or ""
    params = p.get("params") or {}
    if not project_id:
        return error("缺少 project_id")

    # 解析核对参数（主库用于解析列名/sheet，临时库用于实际核对）
    main_store = ProjectDataStore(get_db())
    try:
        rp = _resolve_verify_params(params, main_store, project_id)
    except ValueError as e:
        return error(str(e))
    client = None
    try:
        client = _build_llm_client(model_id)
    except ValueError as e:
        logger.warning("核对模型不可用，将使用确定性报告: %s", e)

    # 1) 创建本次执行临时库 + 复制数据快照
    run = TmpRun.create()
    register_run(run.run_id, run)
    try:
        WorkbenchStore(get_db()).start_result('recon',run.run_id,project_id,p)
        _copy_project_data_to_tmp(get_db(), run.db, project_id)

        # 2) 确定性核对（轮次感知：未匹配 → 审计项）
        result = reconcile_variables(
            file1=rp["base_file"],
            file2=rp["detail_file"],
            sheet1=rp["base_sheet"],
            match_keys=rp["match_keys"],
            amount_col=rp["amount_col"],
            tolerance=rp["tolerance"],
            unmatched_action=rp["unmatched_action"],
            project_id=project_id,
            merge_sheets2=True,
            db_override=run.db,
            audit_mode=True,
        )
        if not result.get("success"):
            return error(result.get("error") or "核对失败")

        # 3) 一次 LLM 调用生成报告（模型不可用 → 确定性降级报告）
        report = ""
        report_mode = "deterministic_fallback"
        if client:
            try:
                report = _chat_complete(
                    client,
                    [
                        {"role": "system", "content": REPORT_SYSTEM},
                        {"role": "user", "content": _build_user_content(params, result)},
                    ],
                )
                if report.strip() and _report_facts_consistent(report, result):
                    report_mode = "ai_verified_facts"
                elif report.strip():
                    logger.warning("LLM 报告与确定性核对事实不一致，改用确定性报告")
                    report_mode = "deterministic_fact_correction"
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM 报告生成失败，降级为确定性报告: %s", e)
        if not report.strip():
            report = _fallback_report(params, result)
        elif report_mode == "deterministic_fact_correction":
            report = _fallback_report(params, result, reason="fact_correction")

        recon_data = _tool_to_recon(result)
        audit_items = result.get("audit_items", [])
        response = {
            "run_id": run.run_id,
            "runId": run.run_id,
            "report": report,
            "reportMode": report_mode,
            "reconData": recon_data,
            # 审计清单全量返回（前端逐条决策分页展示；差异表仍截断 MAX_DIFFS）
            "auditItems": audit_items,
            "raw": {
                "summary": result.get("summary"),
                "amount_col": result.get("amount_col"),
                "match_keys": result.get("match_keys"),
            },
        }
        from services.workbench import persist_report
        response['report_artifact']=persist_report(get_db(),project_id,run.run_id,report)
        WorkbenchStore(get_db()).record_result('recon',run.run_id,project_id,p,response)
        return success(response)
    except Exception as e:  # noqa: BLE001
        WorkbenchStore(get_db()).fail_result(run.run_id,str(e))
        unregister_run(run.run_id)
        run.cleanup()
        logger.exception("recon/verify 失败")
        if isinstance(e,ServiceError):raise
        return error(f"核对执行失败: {e}")


def _source_version_check(
    store: ProjectDataStore,
    project_id: str,
    file_name: str,
    work_dir: str,
) -> tuple[dict | None, str]:
    from tools.project_data_importer import _resolve_work_path, sha256_file

    file_rec = store.find_file_by_name(project_id, file_name)
    if not file_rec:
        return None, f"项目数据库中未找到数据文件「{file_name}」"
    full_path = _resolve_work_path(work_dir, file_rec["source_path"])
    if not os.path.isfile(full_path):
        return None, f"源文件不存在: {file_rec['source_path']}"
    if file_rec.get("file_hash") and sha256_file(full_path) != file_rec["file_hash"]:
        return None, "源文件已更改，数据版本失效；请重新导入后再执行核对写回"
    return file_rec, ""


def _data_revision_error(db: Any, run_id: str, project_id: str) -> str:
    """Reject a writeback when project data changed after the run snapshot."""
    row = db.execute(
        "SELECT payload_json FROM run_events "
        "WHERE run_id=? AND event_type='app.started' ORDER BY seq LIMIT 1",
        (run_id,),
    ).fetchone()
    if not row:
        return ""
    try:
        expected = json.loads(row["payload_json"] or "{}").get("data_revision") or ""
    except (TypeError, ValueError):
        expected = ""
    if expected and expected != WorkbenchStore(db).data_revision(project_id):
        return "数据版本已变化，请重新校验后再写回"
    return ""


def _writeback_export(store: ProjectDataStore, project_id: str,
                      file_name: str, work_dir: str) -> dict:
    """把临时库中某文件的变更日志精确回写到源 Excel（复用 _export_precise）。

    与 tools.export_project_data 等价，但使用传入的（临时库）store ——
    写回发生在本次执行的临时库快照，变更日志记录在临时库 project_data_changes。
    """
    from tools.project_data_tools import (
        _export_precise, detect_real_format,
        ensure_file_writable, load_writer,
    )
    from tools.project_data_importer import _resolve_work_path, sha256_file

    file_rec, version_error = _source_version_check(
        store, project_id, file_name, work_dir
    )
    if version_error:
        return {"success": False, "error": version_error}
    full_path = _resolve_work_path(work_dir, file_rec["source_path"])
    lock_err = ensure_file_writable(full_path)
    if lock_err:
        return lock_err
    real_fmt = detect_real_format(full_path)
    if real_fmt not in ("xlsx", "xls"):
        return {"success": False,
                "error": f"文件实际为 {real_fmt}（非 Excel 二进制），无法安全原地写回"}
    writer = load_writer(full_path, real_fmt)
    try:
        written = _export_precise(writer, store, file_rec)
        writer.save()
    finally:
        writer.close()
    store.update_file(file_rec["id"], file_hash=sha256_file(full_path))
    return {
        "success": True,
        "file": file_rec["file_name"],
        "written_cells": written,
        "message": f"已精确回写 {written} 个单元格到「{file_rec['file_name']}」",
    }


def _latest_run_event(db: Any, run_id: str, event_type: str) -> dict | None:
    row = db.execute(
        "SELECT payload_json FROM run_events "
        "WHERE run_id=? AND event_type=? ORDER BY seq DESC LIMIT 1",
        (run_id, event_type),
    ).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def recon_writeback(p: dict):
    """Serialize writeback claims so one run can have exactly one effect."""
    with _RECON_WRITEBACK_LOCK:
        return _recon_writeback_locked(p)


def _recon_writeback_locked(p: dict):
    """确定性写回（2026-08-21 修复）：勾选差异 → 临时库 update_rows → 精确回写 Excel。

    背景：写回原走 LLM Agent 编排（update_rows/export_project_data 工具链），
    模型不可用或未按指令执行时写回失败且无落盘。本次改为与校验一致的确定性
    路径：后端直接对勾选差异执行 A1/A2 写回（B/C 跳过），记录变更日志，
    再精确回写 Excel —— 不依赖 LLM，秒级完成。
    """
    run_id = (p.get("run_id") or "").strip()
    project_id = (p.get("project_id") or "").strip()
    params = p.get("params") or {}
    diffs = p.get("diffs") or []
    run = get_run(run_id) if run_id else None
    if not run:
        return error("运行不存在或已清理（请重新校验后再写回）", 404)
    project = _project(project_id)
    if not project:
        return error("项目不存在", 404)
    db = get_db()
    completed = _latest_run_event(db, run_id, "app.writeback")
    if completed:
        return success({**completed, "duplicate": True})
    prepared = _latest_run_event(db, run_id, "app.writeback_prepared")
    revision_error = _data_revision_error(db, run_id, project_id)
    if revision_error:
        return error(revision_error, 409)
    detail_file = (params.get("detailFile") or "").strip()
    _, version_error = _source_version_check(
        ProjectDataStore(get_db()),
        project_id,
        detail_file,
        project["work_dir"] or "",
    )
    if version_error:
        return error(version_error, 409)

    tmp_store = ProjectDataStore(run.db)
    written_rows = 0
    skipped = 0
    missed: list[str] = []
    if prepared:
        tgt_col = str(prepared.get("tgt_col") or "")
        detail_file = str(prepared.get("detail_file") or detail_file)
        tolerance = float(prepared.get("tolerance") or 0)
        written_rows = int(prepared.get("written") or 0)
        skipped = int(prepared.get("skipped") or 0)
        missed = [str(item) for item in (prepared.get("missed") or [])]
    else:
        from tools.project_data_tools import _change_log

        try:
            rp = _resolve_verify_params(params, tmp_store, project_id)
        except ValueError as e:
            return error(str(e))
        tgt_col = rp["amount_col"].split("->")[-1].strip()
        detail_file = rp["detail_file"]
        tolerance = rp["tolerance"]
        k0 = rp["match_keys"][0] if rp["match_keys"] else ""
        if not tgt_col or not k0:
            return error("写回参数不完整（缺少金额列或匹配键）")
        for d in diffs:
            level = (d.get("level") or "").upper()
            # 仅 A 类（漏填/小额误差）确定性自动写回；B/C 需人工，跳过
            if level not in ("A1", "A2"):
                skipped += 1
                continue
            contract = (d.get("contract") or "").strip()
            new_val = d.get("base_amount")
            if not contract or new_val is None:
                missed.append(
                    f"{level}「{contract or '无匹配键值'}」金额 "
                    f"{new_val if new_val is not None else '—'}"
                )
                skipped += 1
                continue
            # merge_sheets2 场景：分表按人员拆分多 sheet，遍历全部 sheet 定位。
            done = False
            for sh in _file_sheets(tmp_store, project_id, detail_file):
                cols = _valid_columns(sh)
                if k0 not in cols or tgt_col not in cols:
                    continue
                table_name = sh.get("table_name") or ""
                if not table_name:
                    continue
                prior = tmp_store.fetch_rows(
                    table_name, [tgt_col], {k0: contract}
                )
                if not prior:
                    continue
                tmp_store.update_rows(
                    table_name,
                    {k0: contract},
                    {tgt_col: new_val},
                    extra_cols=[k0],
                )
                headers = [h["name"] for h in (sh.get("headers") or [])]
                for old in prior:
                    _change_log(
                        tmp_store,
                        sh["file_id"],
                        sh["sheet_name"],
                        row_index=old["id"],
                        op_type="update",
                        columns=[tgt_col],
                        old_values={tgt_col: old.get(tgt_col)},
                        new_values={tgt_col: new_val},
                        sheet_headers=headers,
                        key_value=old.get(k0),
                    )
                written_rows += len(prior)
                done = True
            if not done:
                missed.append(contract)
        run.commit()
        WorkbenchStore(db).append_event(
            run_id,
            "app.writeback_prepared",
            {
                "written": written_rows,
                "skipped": skipped,
                "missed": missed,
                "tgt_col": tgt_col,
                "detail_file": detail_file,
                "tolerance": tolerance,
            },
        )

    export_result = _writeback_export(
        tmp_store, project_id, detail_file, project["work_dir"] or "",
    )
    if not export_result.get("success"):
        message = export_result.get("error") or "Excel 回写失败"
        WorkbenchStore(db).append_event(
            run_id,
            "app.writeback_failed",
            {"error": message, "export": export_result},
        )
        return error(f"Excel 回写失败：{message}；临时修改已保留，可重试", 409)

    mk = params.get("matchKeys") or {}
    keys = [mk.get("k1") or "", mk.get("k2On") and mk.get("k2") or "",
            mk.get("k3On") and mk.get("k3") or ""]
    keys = [k for k in keys if k]
    lines = [
        "## 修改报告",
        "",
        "### 本次写回（确定性引擎 · 直接执行）",
        f"- 已写回：**{written_rows}** 行（A1 漏填/ A2 误差自动填充）",
        f"- 跳过：{skipped} 条（B 待确认 / C 无法匹配需人工，本轮不自动修改）",
    ]
    if missed:
        lines.append(f"- 未定位到分表行（跳过）：{len(missed)} 条（如 {'、'.join(missed[:5])}{'…' if len(missed) > 5 else ''}）")
    lines.append(f"- Excel 回写：✅ {export_result.get('message', '')}")
    lines.append("")
    lines.append(f"- 核对参数：依据表 {params.get('baseFile')} → 分表 {params.get('detailFile')}；")
    lines.append(f"  匹配键 {' > '.join(keys) if keys else '—'}；核对变量 {tgt_col}；容差 {tolerance} 元")
    response={"written":written_rows,"skipped":skipped,"export":export_result,"report":"\n".join(lines)}
    if db.execute('SELECT 1 FROM runs WHERE id=? AND project_id=?',(run_id,project_id)).fetchone():
        from services.workbench import persist_report
        try:
            response['report_artifact']=persist_report(db,project_id,run_id,response['report'],filename='修改报告.md')
        except OSError:
            logger.exception('修改已执行，报告文件保存失败')
            response['report_warning']='修改已执行，报告文件保存失败；可在任务记录查看修改明细。'
        WorkbenchStore(db).append_event(run_id,'app.writeback',response)
    return success(response)


def recon_audit(p: dict):
    run_id = p.get("run_id") or ""
    project_id = p.get("project_id") or ""
    params = p.get("params") or {}
    # 审计决策：[{key, side, decision}] decision ∈
    #   base 侧: "补录到分表" / "忽略(总表误记)" / "挂起"
    #   detail 侧: "补录到总表" / "忽略(分表误记)" / "挂起"
    #   ambiguous 侧: "确认对应关系" / "挂起"
    decisions = p.get("decisions") or []
    if not run_id or not project_id:
        return error("缺少 run_id/project_id")

    run = get_run(run_id)
    if not run:
        return error("临时库不存在或已过期，请重新核对")
    tmp_store = ProjectDataStore(run.db)

    try:
        rp = _resolve_verify_params(params, tmp_store, project_id)

        # 应用审计决策：非「挂起」的决策写入 run.decisions（key = "side:key"），
        # 下一轮核对用 skip_keys 排除已决策项（决策即生效：不再计入未匹配/审计）。
        applied = 0
        for dec in decisions:
            key = (dec.get("key") or "").strip()
            side = (dec.get("side") or "").strip()
            decision = (dec.get("decision") or "").strip()
            if not key or not decision or decision == "挂起":
                continue
            # 校验 side 合法性
            if side not in ("base", "detail", "ambiguous"):
                continue
            run.decisions[f"{side}:{key}"] = decision
            applied += 1

        # 下一轮核对：skip_keys = 全部已决策项（含历史轮次）
        skip_keys = list(run.decisions.keys())
        result = reconcile_variables(
            file1=rp["base_file"],
            file2=rp["detail_file"],
            sheet1=rp["base_sheet"],
            match_keys=rp["match_keys"],
            amount_col=rp["amount_col"],
            tolerance=rp["tolerance"],
            unmatched_action=rp["unmatched_action"],
            project_id=project_id,
            merge_sheets2=True,
            db_override=run.db,
            audit_mode=True,
            skip_keys=skip_keys,
        )
        if not result.get("success"):
            return error(result.get("error") or "核对失败")

        recon_data = _tool_to_recon(result)
        response={
            "run_id": run.run_id,
            "runId": run.run_id,
            "reconData": recon_data,
            "auditItems": result.get("audit_items", []),
            "raw": {"summary": result.get("summary")},
            "applied": applied,
            "remaining": len(result.get("audit_items", [])),
        }
        if get_db().execute('SELECT 1 FROM runs WHERE id=? AND project_id=?',(run_id,project_id)).fetchone():
            WorkbenchStore(get_db()).append_event(run_id,'app.audit',response)
        return success(response)
    except Exception as e:  # noqa: BLE001
        logger.exception("recon/audit 失败")
        return error(f"审计处理失败: {e}")
