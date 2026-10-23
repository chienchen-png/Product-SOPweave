"""App data application service."""
from __future__ import annotations
import logging
from typing import Any
from storage.connections import get_connection as get_db
from storage.project_data_store import ProjectDataStore
from storage.operations import check_cancelled
from tools.project_data_tools import _quote
from storage.project_store import ProjectStore
from tools.reconcile_tool import _valid_columns


logger = logging.getLogger(__name__)

def _norm(s: str) -> str:
    return (
        str(s)
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
        .lower()
    )


def _similar(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _match_amount_col(store: ProjectDataStore, project_id: str,
                      base_file: str, detail_file: str, target_col: str) -> str | None:
    """在依据表中找与分表核对变量（target_col）同名或相似的金额列。

    target_col 是分表字段名；依据表列名可能不同（如「兑现销售提成金额_2025年一季度」
    vs「个人提成金额_元」）。优先同名，其次归一化相似。找不到返回 None。
    """
    for sh in _file_sheets(store, project_id, base_file):
        cols1 = _valid_columns(sh)
        if target_col in cols1:
            return target_col
        for c in cols1:
            if _similar(c, target_col):
                return c
    return None


def _file_sheets(store: ProjectDataStore, project_id: str,
                 file: str) -> list[dict]:
    """返回文件的所有 sheet 记录（无则空列表）。"""
    try:
        file_rec = store.find_file_by_name(project_id, file)
        if not file_rec:
            return []
        return store.list_sheets(file_rec["id"]) or []
    except Exception:  # noqa: BLE001
        return []


def _pick_base_sheet(store: ProjectDataStore, project_id: str,
                     base_file: str, match_keys: list[str],
                     src_col: str) -> str:
    """为依据表选择核对用的 sheet。

    多 sheet 文件（如销售提成汇总表：说明/汇总表/2024年/2025年…）：
    - 跳过「说明」类 sheet；
    - 优先选同时含「匹配键 k1」与「金额列」的 sheet；
    - 候选按行数降序，选行数最多的（数据主体 sheet）。
    """
    best = ""
    best_rows = -1
    for sh in _file_sheets(store, project_id, base_file):
        name = sh.get("sheet_name") or ""
        if "说明" in name or "目录" in name or "封面" in name:
            continue
        cols = set(_valid_columns(sh))
        if match_keys and match_keys[0] not in cols:
            continue  # 无匹配键 → 非数据主体 sheet
        # 兼容：金额列可能不在本 sheet（分年度拆分），但匹配键在即视为候选
        rows = int(sh.get("row_count") or sh.get("total_rows") or 0)
        if rows > best_rows:
            best_rows = rows
            best = name
    if best:
        return best
    # 兜底：返回首个非「说明」sheet
    for sh in _file_sheets(store, project_id, base_file):
        name = sh.get("sheet_name") or ""
        if "说明" not in name and "目录" not in name:
            return name
    return ""


def _copy_project_data_to_tmp(main_db: Any, tmp_db: Any, project_id: str) -> None:
    """把主库中某项目的数据平面（文件/sheets/变更日志 + 数据表）复制到临时库。

    应用执行要求：所有核对/审计/修改在独立临时库进行（不污染主库）。
    这里从主库复制已导入的文件记录与数据表到临时库——临时库作为本次执行
    的私有快照；对数据的任何修改只发生在临时库，最后回写 Excel。
    """
    main_db.execute("SAVEPOINT app_snapshot_copy")
    try:
        # 注意：ProjectDataStore.list_sheets 会把 headers_json 解析为 headers 列表，
        # 直接查原始表行（headers_json 保持 JSON 字符串）才能正确复制。
        file_rows = main_db.execute(
            "SELECT * FROM project_data_files WHERE project_id = ?", (project_id,)
        ).fetchall()

        for fr in file_rows:
            fid = fr["id"]
            tmp_db.execute(
                "INSERT OR IGNORE INTO project_data_files (id, project_id, source_path,"
                " file_name, file_hash, format, sheet_count, total_rows, status,"
                " error_message, meta_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fid, fr["project_id"], fr["source_path"] or "",
                    fr["file_name"] or "", fr["file_hash"] or "",
                    fr["format"] or "", fr["sheet_count"] or 0,
                    fr["total_rows"] or 0, fr["status"] or "done",
                    fr["error_message"], fr["meta_json"],
                    fr["created_at"] or "", fr["updated_at"] or "",
                ),
            )
            # 复制 sheets 记录（直接查原始行，保留 headers_json 字符串）
            sheet_rows = main_db.execute(
                "SELECT * FROM project_data_sheets WHERE file_id = ? ORDER BY sheet_index",
                (fid,),
            ).fetchall()
            for sh in sheet_rows:
                tmp_db.execute(
                    "INSERT OR IGNORE INTO project_data_sheets (id, file_id, sheet_name,"
                    " sheet_index, table_name, row_count, col_count, headers_json)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        sh["id"], sh["file_id"], sh["sheet_name"] or "",
                        sh["sheet_index"] or 0, sh["table_name"] or "",
                        sh["row_count"] or 0, sh["col_count"] or 0,
                        sh["headers_json"],
                    ),
                )
                # 复制数据表：从主库读行 → 写入临时库（跨连接无法 CREATE AS SELECT）
                table_name = sh["table_name"]
                if not table_name:
                    continue
                quoted = _quote(table_name)
                definition = main_db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
                if not definition or not definition["sql"]:
                    raise ValueError(f"数据表不存在: {table_name}")
                tmp_db.execute(f'DROP TABLE IF EXISTS {quoted}')
                tmp_db.execute(definition["sql"])
                cursor = main_db.execute(f'SELECT * FROM {quoted}')
                columns = [item[0] for item in cursor.description]
                names = ", ".join(_quote(name) for name in columns)
                placeholders = ", ".join("?" for _ in columns)
                while batch := cursor.fetchmany(500):
                    check_cancelled()
                    tmp_db.executemany(f'INSERT INTO {quoted} ({names}) VALUES ({placeholders})', (tuple(row) for row in batch))
        tmp_db.commit()
    finally:
        main_db.execute("RELEASE SAVEPOINT app_snapshot_copy")


def _project(project_id: str) -> dict | None:
    """查询项目（含 work_dir）。"""
    if not project_id:
        return None
    return ProjectStore(get_db()).get_by_id(project_id)
