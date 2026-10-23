"""Run an isolated SOP acceptance app; credentials are read locally, never logged."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18180)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument(
        "--provider", default="deepseek", choices=["deepseek", "zhipu", "siliconflow"]
    )
    args = parser.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["SOPWEAVE_DATA_DIR"] = str(root)
    os.environ["SOPWEAVE_USER_SKILLS_ENABLED"] = "1"
    os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
    from app import create_app
    from storage.db import get_db
    from storage.model_store import ModelStore
    from agent_runtime.api import create_agent_api

    app = create_app()
    if args.key_file:
        from scripts.check_tool_rebuild_providers import read_credentials

        record = next(
            b
            for b in read_credentials(args.key_file)
            if b["provider_kind"] == args.provider
        )
    else:
        raise SystemExit(
            "Provide --key-file for low-frequency real provider acceptance"
        )
    with app.app_context():
        db = get_db()
        store = ModelStore(db)
        provider_data = dict(
            name="SOP 隔离验收",
            provider_kind=record["provider_kind"],
            api_url=record["api_url"],
            api_key=record["api_key"],
            status="available",
            api_mode="chat_completions",
            options_json={"timeout": 90, "max_retries": 0},
        )
        model_data = dict(
            name="SOP 验收模型",
            api_model=record["api_model"],
            type="chat",
            context_window=200000,
            status="available",
            request_options_json={"thinking_enabled": False},
        )
        existing = db.execute(
            "SELECT id,provider_id FROM models WHERE name='SOP 验收模型'"
        ).fetchone()
        if existing:
            store.update_provider(existing["provider_id"], provider_data)
            model = store.update_model(existing["id"], model_data)
        else:
            vendor = store.create_provider(provider_data)
            model = store.create_model(vendor["id"], model_data)
        model_id = model["id"]
    work = root / "workspace"
    work.mkdir(exist_ok=True)
    (work / "input.txt").write_text(
        "SOP_PROVIDER_OK=100.00\n银行流水270.00，账单170.00，差额100.00。",
        encoding="utf-8",
    )
    with app.test_client() as client:
        projects = client.get("/api/projects").get_json()["data"]
        if not any(p["name"] == "SOP 隔离验收项目" for p in projects):
            client.post(
                "/api/projects",
                json={
                    "name": "SOP 隔离验收项目",
                    "work_dir": str(work),
                    "default_model": model_id,
                    "default_agent": "general-agent",
                    "work_mode": "auto",
                },
            )
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{args.port}/sops",
                "model_id": model_id,
                "data_dir": str(root),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    import uvicorn

    uvicorn.run(
        create_agent_api(flask_app=app),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
