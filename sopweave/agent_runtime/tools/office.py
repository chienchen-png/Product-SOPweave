from __future__ import annotations

from typing import Literal
from pydantic import Field

from agent_runtime.workspace import resolve_path
from tools import office_documents as office
from .contracts import Arguments, typed_tool
from .datasets import Scalar


class OfficePath(Arguments):
    path: str = Field(min_length=1, description="授权工作目录内的 Office 文件相对路径")


class SheetRange(OfficePath):
    sheet: str = Field(min_length=1)
    cell_range: str = Field(description="A1 范围，例如 A1:D30")
    formulas: bool = False


class CellChange(Arguments):
    sheet: str = Field(min_length=1, max_length=31)
    cell: str = Field(pattern=r"^[A-Za-z]{1,3}[1-9][0-9]{0,6}$")
    value: Scalar
    kind: Literal["value", "formula"] = "value"


class EditWorkbook(OfficePath):
    expected_version: str
    changes: list[CellChange] = Field(min_length=1, max_length=1000)


class ReadDocument(OfficePath):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=500)
    character_offset: int = Field(default=0, ge=0, description="超长文档块续读，使用上次返回的 next_character_offset")


class TextChange(Arguments):
    block_id: str
    old_text: str = Field(min_length=1, max_length=10000)
    new_text: str = Field(max_length=10000)


class EditDocument(OfficePath):
    expected_version: str
    changes: list[TextChange] = Field(min_length=1, max_length=100)


def inspect_workbook(context, args):
    return office.inspect_workbook(resolve_path(context.work_dir, args.path))


def read_sheet_range(context, args):
    return office.read_sheet_range(
        resolve_path(context.work_dir, args.path),
        args.sheet,
        args.cell_range,
        args.formulas,
    )


def edit_workbook(context, args):
    result = office.edit_workbook(
        resolve_path(context.work_dir, args.path),
        args.expected_version,
        [c.model_dump() for c in args.changes],
    )
    return {**result, "path": args.path, "artifacts": [args.path]}


def read_document(context, args):
    return office.read_document(
        resolve_path(context.work_dir, args.path), args.offset, args.limit, args.character_offset
    )


def edit_document(context, args):
    result = office.edit_document(
        resolve_path(context.work_dir, args.path),
        args.expected_version,
        [c.model_dump() for c in args.changes],
    )
    return {**result, "path": args.path, "artifacts": [args.path]}


def specs():
    return [
        typed_tool(name, description, model, function, group="office", risk=risk)
        for name, description, model, function, risk in [
            (
                "inspect_workbook",
                "检查 Excel 结构、公式缓存和工作表，返回文件版本。",
                OfficePath,
                inspect_workbook,
                "read",
            ),
            (
                "read_sheet_range",
                "按坐标读取 Excel，保留重复表头、值、格式及可选公式。",
                SheetRange,
                read_sheet_range,
                "read",
            ),
            (
                "edit_workbook",
                "按版本精确修改单元格或新增工作表；复杂对象无法保真时拒绝。",
                EditWorkbook,
                edit_workbook,
                "write",
            ),
            (
                "read_document",
                "按块读取 DOCX 正文、表格及页眉页脚，返回块 ID 和版本。",
                ReadDocument,
                read_document,
                "read",
            ),
            (
                "edit_document",
                "按块精确替换 DOCX 文字并保留 run 格式，冲突时不写入。",
                EditDocument,
                edit_document,
                "write",
            ),
        ]
    ]
