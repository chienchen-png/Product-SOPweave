"""Representative SQLite/DuckDB data-plane benchmark.

Generates deterministic financial rows, verifies both backends return the
same aggregate checksum, and prints machine-readable JSON. DuckDB is optional
because the desktop package must remain runnable from the offline bundle.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from typing import Any, Callable


def rows(count: int):
    for index in range(count):
        yield (
            f"C{index % 5000:05d}",
            f"D{index % 20:02d}",
            2020 + index % 7,
            round((index % 10000) * 1.07, 2),
        )


def timed(call: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = call()
    return result, round(time.perf_counter() - started, 6)


def sqlite_benchmark(count: int) -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE facts(customer TEXT, department TEXT, year INTEGER, amount REAL)"
    )
    _, load_seconds = timed(lambda: connection.executemany(
        "INSERT INTO facts VALUES (?, ?, ?, ?)", rows(count)
    ))
    connection.commit()
    aggregate, aggregate_seconds = timed(lambda: connection.execute(
        "SELECT department, ROUND(SUM(amount), 2) FROM facts GROUP BY department ORDER BY department"
    ).fetchall())
    filtered, filtered_seconds = timed(lambda: connection.execute(
        "SELECT ROUND(SUM(amount), 2) FROM facts WHERE year = 2025"
    ).fetchone()[0])
    connection.close()
    return {
        "available": True,
        "load_seconds": load_seconds,
        "aggregate_seconds": aggregate_seconds,
        "filtered_seconds": filtered_seconds,
        "checksum": aggregate,
        "filtered_checksum": filtered,
    }


def duckdb_benchmark(count: int) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError:
        return {"available": False, "reason": "duckdb package is not installed"}
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE facts(customer VARCHAR, department VARCHAR, year INTEGER, amount DOUBLE)"
    )
    _, load_seconds = timed(lambda: connection.executemany(
        "INSERT INTO facts VALUES (?, ?, ?, ?)", rows(count)
    ))
    aggregate, aggregate_seconds = timed(lambda: connection.execute(
        "SELECT department, ROUND(SUM(amount), 2) FROM facts GROUP BY department ORDER BY department"
    ).fetchall())
    filtered, filtered_seconds = timed(lambda: connection.execute(
        "SELECT ROUND(SUM(amount), 2) FROM facts WHERE year = 2025"
    ).fetchone()[0])
    connection.close()
    return {
        "available": True,
        "load_seconds": load_seconds,
        "aggregate_seconds": aggregate_seconds,
        "filtered_seconds": filtered_seconds,
        "checksum": aggregate,
        "filtered_checksum": filtered,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    args = parser.parse_args()
    sqlite_result = sqlite_benchmark(args.rows)
    duckdb_result = duckdb_benchmark(args.rows)
    checksums_match = None
    if duckdb_result.get("available"):
        checksums_match = (
            sqlite_result["checksum"] == duckdb_result["checksum"]
            and sqlite_result["filtered_checksum"]
            == duckdb_result["filtered_checksum"]
        )
    print(json.dumps({
        "rows": args.rows,
        "sqlite": sqlite_result,
        "duckdb": duckdb_result,
        "checksums_match": checksums_match,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
