from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from storage.connections import get_connection
from storage.operations import check_cancelled
from agent_runtime.workspace import file_version, resolve_path
from storage.project_data_store import ProjectDataStore
from tools import project_data_tools as data
from .contracts import Arguments, typed_tool

Scalar = str | int | float | bool | None


class FieldValue(Arguments):
    field: str = Field(min_length=1, description="导入字段的唯一名称，来自数据目录")
    value: Scalar


class Dataset(Arguments):
    file: str = Field(
        min_length=1,
        description="数据目录返回的 file（完整相对路径）或 file_id；同名文件不能只填名称",
    )
    sheet: str = Field(default="", description="工作表名，空值使用第一张表")


class ListDatasets(Arguments):
    file: str = Field(
        default="",
        description="数据目录返回的完整相对路径或 file_id；留空只返回紧凑目录，不读取字段",
    )
    sheet: str = Field(
        default="",
        description="仅在已指定 file 时使用；填写后只返回该分表及字段",
    )
    include_fields: bool = Field(
        default=False,
        description="指定 file 后读取字段；未指定 file 时不生效",
    )


class ImportDataset(Arguments):
    path: str = Field(min_length=1, description="授权工作目录中的 Excel 相对路径")
    header_row: int | None = Field(
        default=None, ge=1, description="仅在导入请求人工定位时填写，1 起算"
    )


class Metric(Arguments):
    function: Literal["sum", "avg", "count", "min", "max"]
    field: str = Field(description="字段名；count 可以使用 *")


class Join(Arguments):
    right_file: str
    right_sheet: str = ""
    left_key: str
    right_key: str
    how: Literal["inner", "left"] = "inner"


class QueryDataset(Dataset):
    columns: list[str] = Field(default_factory=list, max_length=100)
    filters: list[FieldValue] = Field(
        default_factory=list, max_length=30, description="精确匹配条件，条件之间为 AND"
    )
    limit: int = Field(default=100, ge=1, le=1000)
    order_by: str = ""
    group_by: list[str] = Field(default_factory=list, max_length=20)
    metrics: list[Metric] = Field(default_factory=list, max_length=20)
    join: Join | None = None


class DataRow(Arguments):
    values: list[FieldValue] = Field(min_length=1, max_length=100)


class ChangeDataset(Dataset):
    operation: Literal["update", "insert", "delete"]
    expected_revision: str = Field(
        min_length=1, description="最近查询得到的 revision，冲突时重新查询"
    )
    filters: list[FieldValue] = Field(default_factory=list, max_length=30)
    values: list[FieldValue] = Field(default_factory=list, max_length=100)
    rows: list[DataRow] = Field(default_factory=list, max_length=200)


class ExportDataset(Arguments):
    file: str = Field(
        min_length=1,
        description="数据目录返回的完整相对路径或 file_id；导出包含所有工作表的变更",
    )
    mode: Literal["precise", "template"] = "precise"


def _mapping(values):
    result = {value.field: value.value for value in values}
    if len(result) != len(values):
        raise ValueError("字段条件不能重复")
    return result


def _revision(db, file_id, sheet):
    row = db.execute(
        "SELECT COALESCE(MAX(id),0), COUNT(*) FROM project_data_changes WHERE file_id=? AND sheet_name=?",
        (file_id, sheet),
    ).fetchone()
    source = db.execute(
        "SELECT file_hash, updated_at FROM project_data_files WHERE id=?", (file_id,)
    ).fetchone()
    return hashlib.sha256(
        json.dumps([list(row), list(source)], ensure_ascii=False).encode()
    ).hexdigest()


def validate_dataset_source(context, file_name):
    record = ProjectDataStore(get_connection()).find_file_by_name(
        context.project_id, file_name
    )
    if not record:
        raise ValueError("项目中没有此数据文件，请先导入")
    source = resolve_path(context.work_dir, record["source_path"])
    if record.get("file_hash") and file_version(source) != record["file_hash"]:
        raise ValueError(
            "源文件已更改或移走，当前导入数据版本失效；请重新导入后分析或写回"
        )
    return record


def list_datasets(context, args):
    """Return a bounded catalog; field metadata is read only for one target file."""
    store = ProjectDataStore(get_connection())
    files = [
        item
        for item in store.list_files(context.project_id)
        if item.get("status") == "done"
    ]
    if args.file:
        target = store.find_file_by_name(context.project_id, args.file)
        if not target:
            return {
                "success": False,
                "error": f"项目数据库中未找到数据文件「{args.file}」；请先导入",
            }
        files = [item for item in files if item["id"] == target["id"]]

    items = []
    for file in files:
        sheets = file.get("sheets") or []
        if args.sheet:
            sheets = [
                sheet for sheet in sheets if sheet.get("sheet_name") == args.sheet
            ]
            if not sheets:
                return {
                    "success": False,
                    "error": (
                        f"工作表「{args.sheet}」不存在；可用: "
                        + ", ".join(
                            sheet.get("sheet_name", "")
                            for sheet in file.get("sheets") or []
                        )
                    ),
                }
        with_fields = bool(args.file and (args.include_fields or args.sheet))
        sheet_items = []
        for sheet in sheets:
            item = {
                "sheet": sheet.get("sheet_name"),
                "rows": sheet.get("row_count", 0),
                "column_count": sheet.get("col_count", 0),
            }
            if with_fields:
                item["fields"] = [
                    {"name": header["name"], "type": header.get("type", "text")}
                    for header in sheet.get("headers") or []
                ]
            sheet_items.append(item)
        items.append(
            {
                "file": file.get("source_path"),
                "file_id": file.get("id"),
                "file_name": file.get("file_name"),
                "source_path": file.get("source_path"),
                "sheet_count": file.get("sheet_count", 0),
                "total_rows": file.get("total_rows", 0),
                "sheets": sheet_items,
            }
        )

    scope = "指定文件及分表" if args.file else "全部已导入数据文件"
    return {
        "success": True,
        "count": len(items),
        "files": items,
        "message": (
            f"已返回{scope}；记录数直接来自目录。"
            "需要字段时指定 file 并将 include_fields 设为 true，"
            "或指定 file 与 sheet 读取单表字段。"
        ),
    }


def import_dataset(context, args):
    from agent_runtime.workspace import resolve_path

    resolve_path(context.work_dir, args.path)
    return data.import_excel_to_db(
        args.path,
        header_row=args.header_row,
        project_id=context.project_id,
        work_dir=str(context.work_dir),
    )


def query_dataset(context, args):
    validate_dataset_source(context, args.file)
    if args.join:
        validate_dataset_source(context, args.join.right_file)
        if args.filters or args.metrics or args.group_by:
            raise ValueError("连接查询不同时接受筛选或聚合，请拆分为明确的查询")
        result = data.join_tables(
            args.file,
            args.join.right_file,
            args.join.left_key,
            args.join.right_key,
            left_sheet=args.sheet,
            right_sheet=args.join.right_sheet,
            columns=args.columns or None,
            how=args.join.how,
            limit=args.limit,
            project_id=context.project_id,
        )
    else:
        aggregate = (
            {
                "group_by": args.group_by,
                "metrics": [f"{m.function}({m.field})" for m in args.metrics],
            }
            if args.metrics
            else None
        )
        result = data.query_table(
            args.file,
            args.sheet,
            columns=args.columns or None,
            filters=_mapping(args.filters),
            order_by=args.order_by,
            limit=args.limit,
            aggregate=aggregate,
            project_id=context.project_id,
        )
    if result.get("success"):
        db = get_connection()
        file, sheet = data._file_and_sheet(
            ProjectDataStore(db), context.project_id, args.file, args.sheet
        )
        result["revision"] = _revision(db, file["id"], sheet["sheet_name"])
    return result


def apply_data_changes(context, args):
    validate_dataset_source(context, args.file)
    db = get_connection()
    db.execute("BEGIN IMMEDIATE")
    try:
        store = ProjectDataStore(db)
        file, sheet = data._file_and_sheet(
            store, context.project_id, args.file, args.sheet
        )
        if _revision(db, file["id"], sheet["sheet_name"]) != args.expected_revision:
            raise ValueError("数据版本冲突，请重新查询后修改")
        columns = [h["name"] for h in sheet["headers"]]
        filters, values = _mapping(args.filters), _mapping(args.values)
        rows = [_mapping(row.values) for row in args.rows]
        for name in [*filters, *values, *(key for row in rows for key in row)]:
            if name not in columns:
                raise ValueError(f"未知字段: {name}")
        table = sheet["table_name"]
        if args.operation == "insert":
            if not rows or filters or values:
                raise ValueError("插入只接受非空 rows")
            affected = []
            for row in rows:
                names = list(row)
                sql_names = ",".join(data._quote(name) for name in names)
                cursor = db.execute(
                    f"INSERT INTO {data._quote(table)} ({sql_names}) VALUES ({','.join('?' for _ in names)})",
                    list(row.values()),
                )
                affected.append({"id": cursor.lastrowid, **row})
        else:
            if not filters or rows or (args.operation == "update" and not values):
                raise ValueError("更新/删除必须提供定位条件，更新还需非空 values")
            if args.operation == "delete" and values:
                raise ValueError("删除不接受 values")
            affected = (
                store.update_rows(table, filters, values, commit=False)
                if args.operation == "update"
                else store.delete_rows(table, filters, commit=False)
            )
        for row in affected:
            changed = (
                values
                if args.operation == "update"
                else {name: row.get(name) for name in columns}
            )
            for name, value in changed.items():
                store.log_change(
                    file["id"],
                    sheet["sheet_name"],
                    row["id"],
                    args.operation,
                    col_index=columns.index(name),
                    col_name=name,
                    old_value=None if args.operation == "insert" else row.get(name),
                    new_value=None if args.operation == "delete" else value,
                    run_id=context.run_id,
                    key_value=(
                        json.dumps(filters, ensure_ascii=False, sort_keys=True)
                        if filters
                        else (
                            str(row[columns[0]])
                            if columns and row.get(columns[0]) is not None
                            else ""
                        )
                    ),
                )
        db.execute(
            f"UPDATE project_data_sheets SET row_count=(SELECT COUNT(*) FROM {data._quote(table)}) WHERE file_id=? AND sheet_name=?",
            (file["id"], sheet["sheet_name"]),
        )
        db.execute(
            "UPDATE project_data_files SET total_rows=(SELECT COALESCE(SUM(row_count),0) FROM project_data_sheets WHERE file_id=?) WHERE id=?",
            (file["id"], file["id"]),
        )
        check_cancelled()
        db.commit()
        return {
            "success": True,
            "matched": len(affected),
            "revision": _revision(db, file["id"], sheet["sheet_name"]),
        }
    except BaseException:
        db.rollback()
        raise


def export_dataset(context, args):
    return data.export_project_data(
        args.file,
        args.mode,
        project_id=context.project_id,
        work_dir=str(context.work_dir),
        progress=(
            (
                lambda percent, message: context.event_callback(
                    "tool.progress", {"percent": percent, "message": message}
                )
            )
            if context.event_callback
            else None
        ),
    )


def specs():
    return [
        typed_tool(
            name,
            description,
            model,
            function,
            group="project-data",
            risk=risk,
            timeout=180,
        )
        for name, description, model, function, risk in [
            (
                "list_datasets",
                "查看项目数据库目录与行数；默认不读取字段，指定 file 后可按需读取字段。",
                ListDatasets,
                list_datasets,
                "read",
            ),
            (
                "import_dataset",
                "从授权工作目录导入 Excel；复用公式与布局复核。",
                ImportDataset,
                import_dataset,
                "write",
            ),
            (
                "query_dataset",
                "查询、分组聚合或连接项目数据，返回有限结果和 revision。",
                QueryDataset,
                query_dataset,
                "read",
            ),
            (
                "apply_data_changes",
                "按 revision 和精确条件事务修改数据，记录运行及单元格变更。",
                ChangeDataset,
                apply_data_changes,
                "data_change",
            ),
            (
                "export_dataset",
                "将已审核的数据库变更精确写回 Excel 或重建模板。",
                ExportDataset,
                export_dataset,
                "write",
            ),
        ]
    ]
