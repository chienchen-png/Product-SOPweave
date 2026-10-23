"""Repeatable local persistence/snapshot benchmark; never reads user data."""
from __future__ import annotations
import argparse
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from agent_runtime.events import EventWriter
    from services.app_data import _copy_project_data_to_tmp
    from storage.tmp_db import TmpRun
    with tempfile.TemporaryDirectory(prefix='工作台 基准 ') as directory:
        root = Path(directory)
        path = root / 'source.db'
        with closing(sqlite3.connect(path)) as db:
            db.row_factory = sqlite3.Row
            db.executescript((ROOT / 'storage/schema.sql').read_text(encoding='utf-8'))
            db.execute("INSERT INTO runs(id,status,created_at,updated_at) VALUES('r','running','now','now')")
            db.execute("INSERT INTO project_data_files(id,project_id,source_path,file_name,format,created_at,updated_at) VALUES('f','p','sample.xlsx','sample.xlsx','xlsx','now','now')")
            db.execute('CREATE TABLE data_benchmark(_row_index INTEGER PRIMARY KEY, category TEXT, amount REAL)')
            db.executemany('INSERT INTO data_benchmark VALUES(?,?,?)', ((i, f'部门{i%10}', i/10) for i in range(1, 50001)))
            db.execute("INSERT INTO project_data_sheets(file_id,sheet_name,sheet_index,table_name,headers_json) VALUES('f','Sheet',0,'data_benchmark','[]')")
            db.commit()
        writer = EventWriter(path, 'r')
        durations = []
        for index in range(100):
            started = time.perf_counter()
            writer.write('message.delta', {'delta': f'片段{index}'})
            durations.append((time.perf_counter()-started)*1000)
        if hasattr(writer, 'close'):
            writer.close()
        with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(root/'target.db')) as target:
            source.row_factory = sqlite3.Row
            target.row_factory = sqlite3.Row
            target.executescript((ROOT/'storage/schema.sql').read_text(encoding='utf-8'))
            tracemalloc.start()
            started = time.perf_counter()
            _copy_project_data_to_tmp(source,target,'p')
            seconds = time.perf_counter()-started
            _,peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            count,total = target.execute('SELECT COUNT(*), SUM(amount) FROM data_benchmark').fetchone()
        result={'event_write_ms':{'median':statistics.median(durations),'p95':sorted(durations)[94]}, 'snapshot':{'rows':count,'sum':total,'seconds':seconds,'peak_python_bytes':peak}}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
