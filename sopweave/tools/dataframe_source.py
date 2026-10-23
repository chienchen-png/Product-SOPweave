"""Read registered cells without guessing away identifiers or placeholder text."""
from __future__ import annotations

import pandas as pd
from config.settings import ANALYSIS_MAX_CELLS
from tools.project_data_tools import _quote

MAX_ANALYSIS_CELLS = ANALYSIS_MAX_CELLS


def ensure_analysis_budget(rows: int, columns: int) -> None:
    if MAX_ANALYSIS_CELLS and rows * columns > MAX_ANALYSIS_CELLS:
        raise ValueError(f'分析数据量 {rows} 行 × {columns} 列超过当前 {MAX_ANALYSIS_CELLS} 单元格预算；请先筛选/聚合，或调整 SOPWEAVE_ANALYSIS_MAX_CELLS。数据未截断。')


def load_registered_frame(store, project_id, file_name, sheet_name='', columns=None):
    record = store.find_file_by_name(project_id, file_name)
    if not record:
        raise ValueError(f'未找到数据文件「{file_name}」')
    sheets = store.list_sheets(record['id']) or []
    if not sheets:
        raise ValueError(f'文件「{file_name}」没有工作表')
    sheet = next((item for item in sheets if item.get('sheet_name') == sheet_name), None) if sheet_name else next((item for item in sheets if not any(word in item.get('sheet_name','') for word in ('说明','目录','封面'))), sheets[0])
    if sheet is None:
        raise ValueError(f'未找到工作表「{sheet_name}」')
    valid = [header['name'] for header in sheet.get('headers',[]) if header.get('name')]
    selected = columns or valid
    if not selected or any(name not in valid for name in selected):
        raise ValueError('分析字段为空或包含未注册的列')
    table = _quote(sheet['table_name'])
    count = store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    ensure_analysis_budget(count,len(selected))
    return pd.read_sql_query(f'SELECT {", ".join(_quote(name) for name in selected)} FROM {table}', store.db)
