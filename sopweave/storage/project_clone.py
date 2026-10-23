"""Transactional copies of project configuration and editable data snapshots."""

from __future__ import annotations

import json
import re
import uuid

from storage._utils import iso_now
from storage.project_data_store import ProjectDataStore


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _insert(db, table: str, values: dict):
    columns = ",".join(_identifier(name) for name in values)
    return db.execute(
        f"INSERT INTO {_identifier(table)} ({columns}) VALUES ({','.join('?' for _ in values)})",
        list(values.values()),
    )


def clone_project(db, project_id: str) -> str:
    """Own one write transaction; never copy or modify physical workspace files."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        source = db.execute(
            "SELECT * FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if source is None:
            raise ValueError("源项目已不存在")
        if db.execute(
            "SELECT 1 FROM runs WHERE project_id=? AND status NOT IN ('completed','failed','cancelled') LIMIT 1",
            (project_id,),
        ).fetchone():
            raise ValueError("源项目有未结束的任务，请先完成或停止任务再创建副本")
        now = iso_now()
        new_id = uuid.uuid4().hex
        _insert(
            db,
            "projects",
            {
                **dict(source),
                "id": new_id,
                "name": f'{source["name"]} 副本',
                "created_at": now,
                "updated_at": now,
            },
        )
        store = ProjectDataStore(db)
        files = db.execute(
            "SELECT * FROM project_data_files WHERE project_id=?", (project_id,)
        ).fetchall()
        for file in files:
            if file["status"] != "done":
                raise ValueError("源项目包含未完成的数据文件，请处理后再创建副本")
            new_file_id = uuid.uuid4().hex
            meta = json.loads(file["meta_json"] or "{}")
            old_checkpoint = store.export_checkpoint(file["id"])
            meta.update(cloned_from_file_id=file["id"], exported_change_id=0)
            _insert(
                db,
                "project_data_files",
                {
                    **dict(file),
                    "id": new_file_id,
                    "project_id": new_id,
                    "meta_json": json.dumps(meta, ensure_ascii=False),
                    "updated_at": now,
                },
            )
            for sheet in db.execute(
                "SELECT * FROM project_data_sheets WHERE file_id=? ORDER BY sheet_index",
                (file["id"],),
            ).fetchall():
                source_table = sheet["table_name"]
                if not re.fullmatch(r"data_[a-f0-9]+_\d+", source_table):
                    raise ValueError("源数据表标识无效，请重新导入源文件")
                definition = db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (source_table,),
                ).fetchone()
                columns = list(
                    db.execute(f"PRAGMA table_info({_identifier(source_table)})")
                )
                if not definition or not any(
                    row["name"] == "id" and row["pk"] == 1 for row in columns
                ):
                    raise ValueError("源数据表缺少完整主键结构，请重新导入源文件")
                new_table = f'data_{new_file_id[:8]}_{sheet["sheet_index"]}'
                ddl = definition["sql"]
                db.execute(
                    f"CREATE TABLE {_identifier(new_table)} {ddl[ddl.index('('):]}"
                )
                db.execute(
                    f"INSERT INTO {_identifier(new_table)} SELECT * FROM {_identifier(source_table)}"
                )
                _insert(
                    db,
                    "project_data_sheets",
                    {
                        **{key: sheet[key] for key in sheet.keys() if key != "id"},
                        "file_id": new_file_id,
                        "table_name": new_table,
                    },
                )
            checkpoint = 0
            for change in db.execute(
                "SELECT * FROM project_data_changes WHERE file_id=? ORDER BY id",
                (file["id"],),
            ).fetchall():
                copied = _insert(
                    db,
                    "project_data_changes",
                    {
                        **{key: change[key] for key in change.keys() if key != "id"},
                        "file_id": new_file_id,
                    },
                )
                if change["id"] <= old_checkpoint:
                    checkpoint = copied.lastrowid
            meta["exported_change_id"] = checkpoint
            db.execute(
                "UPDATE project_data_files SET meta_json=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), new_file_id),
            )
    return new_id
