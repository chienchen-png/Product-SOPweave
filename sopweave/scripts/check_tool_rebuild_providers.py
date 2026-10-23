"""Opt-in real-provider acceptance; secrets are read only into process memory."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.models.factory import ModelRuntimeFactory, resolve_model_config  # noqa: E402
from agent_runtime.models.contract import contract_runner_for, ProviderContractRunner  # noqa: E402
from agent_runtime.tools.catalog import ToolCatalog  # noqa: E402
from agent_runtime.tools.interaction import AskUser  # noqa: E402


def read_credentials(path):
    blocks = []
    current = None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text:
            continue
        if text.lower().rstrip(":：") == "deepseek":
            kind = "deepseek"
        elif text.upper().rstrip(":：") == "GLM":
            kind = "zhipu"
        elif "硅基流动" in text:
            kind = "siliconflow"
        else:
            kind = None
        if kind:
            current = {"provider_kind": kind}
            blocks.append(current)
            continue
        if current is None:
            continue
        url = re.search(r"https://[^\s]+", text)
        if url:
            current["api_url"] = url.group()
            continue
        if "模型" in text:
            model = re.split("[:：]", text, maxsplit=1)[-1].strip()
            current["api_model"] = re.split(r"[,，、\s]", model)[0]
            continue
        token = re.search(r"sk-[A-Za-z0-9_-]+|[a-fA-F0-9]{20,}\.[A-Za-z0-9_-]+", text)
        if token:
            current["api_key"] = token.group()
    return blocks


async def check(block, *, timeout=30, interval_seconds=None):
    kind = block["provider_kind"]
    if any(not block.get(key) for key in ("api_url", "api_model", "api_key")):
        return {
            "provider": kind,
            "passed": False,
            "error_code": "credentials_incomplete",
        }
    config = resolve_model_config(
        {
            **block,
            "id": "acceptance",
            "name": block["api_model"],
            "api_mode": "chat_completions",
            "capabilities_json": {"chat": True, "stream": True, "tools": True},
            "request_options_json": {"thinking_enabled": False},
            "provider_options_json": {"timeout": timeout, "max_retries": 0},
        }
    )
    client = ModelRuntimeFactory(usage_recorder=None).build_direct_client(config)
    result = None
    try:
        runner = (
            ProviderContractRunner(
                client,
                interval_seconds=15 if interval_seconds is None else interval_seconds,
                max_rate_limit_retries=1,
                default_retry_seconds=15,
            )
            if kind == "zhipu"
            else contract_runner_for(config, client)
        )
        result = (await runner.run()).to_dict()
        if kind == "zhipu":
            await asyncio.sleep(15 if interval_seconds is None else interval_seconds)
        catalog = ToolCatalog.default()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.params_json_schema,
                },
            }
            for spec in catalog._specs.values()
        ]
        # Submit the actual nested public schemas. No tool side effects occur.
        response = await client.complete(
            [
                {
                    "role": "user",
                    "content": "Ask the user which period to analyze. Use one single-choice question q with two options current and previous, labels 本期 and 上期. Do not answer the question.",
                }
            ],
            tools=tools,
            tool_choice=client.named_tool_choice("ask_user"),
            max_tokens=1000,
        )
        calls = response.choices[0].message.tool_calls or []
        if not calls:
            raise ValueError("no_tool_call")
        AskUser.model_validate_json(calls[0].function.arguments)
        return {
            "provider": kind,
            "model": block["api_model"],
            "passed": result["passed"],
            "checks": result["checks"],
            "nested_tool_schema": "passed",
        }
    except Exception as exc:
        return {
            "provider": kind,
            "model": block["api_model"],
            "passed": False,
            "error_code": type(exc).__name__,
            "checks": result["checks"] if result else [],
            "nested_tool_schema": "not_completed",
        }
    finally:
        await client.aclose()


async def main(path, provider=None, *, timeout=30, interval_seconds=None):
    blocks = read_credentials(path)
    if provider:
        blocks = [block for block in blocks if block["provider_kind"] == provider]
    results = []
    for block in blocks:
        result = await check(block, timeout=timeout, interval_seconds=interval_seconds)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return (
        0
        if len(results) == (1 if provider else 3)
        and all(result["passed"] for result in results)
        else 1
    )


async def check_skills(path, provider=None, *, timeout=90, interval_seconds=None):
    import httpx2 as httpx
    from unittest.mock import patch
    import sqlite3
    import tempfile
    import time
    from contextlib import closing
    from dataclasses import replace
    from storage.db import _ensure_model_schema
    from storage.model_store import ModelStore
    from services.skills.library import SkillLibrary
    from services.skills.authoring import SkillAuthoringService
    from agent_runtime.service import RunService, RunRequest

    blocks = read_credentials(path)
    if provider:
        blocks = [b for b in blocks if b["provider_kind"] == provider]
    results = []
    for block in blocks:
        kind = block["provider_kind"]
        started = time.monotonic()
        attempts = 0
        result = {
            "provider": kind,
            "model": block.get("api_model"),
            "passed": False,
            "scenarios": [],
        }
        with tempfile.TemporaryDirectory(prefix="finance-sop-provider-") as folder:
            root = Path(folder)
            database = root / "app.db"
            with closing(sqlite3.connect(database)) as db:
                db.row_factory = sqlite3.Row
                db.executescript(
                    (ROOT / "storage/schema.sql").read_text(encoding="utf-8")
                )
                _ensure_model_schema(db)
                store = ModelStore(db)
                vendor = store.create_provider(
                    {
                        "name": kind,
                        "provider_kind": kind,
                        "api_url": block["api_url"],
                        "api_key": block["api_key"],
                        "api_mode": "chat_completions",
                        "status": "available",
                        "options_json": {"timeout": timeout, "max_retries": 0},
                    }
                )
                model = store.create_model(
                    vendor["id"],
                    {
                        "name": block["api_model"],
                        "api_model": block["api_model"],
                        "type": "chat",
                        "context_window": 200000,
                        "status": "available",
                        "request_options_json": {"thinking_enabled": False},
                    },
                )
            diagnostics = []
            validations = []
            library = SkillLibrary(database, data_dir=root)
            author = SkillAuthoringService(
                library,
                error_observer=lambda exc: diagnostics.append(
                    str(exc).replace(block["api_key"], "[redacted]")
                ),
            )
            text = "创建纯读取的财务SOP。固定读取工作目录input.txt，然后在对话中原文汇报并注明文件名。只使用read_text；不写文件、不执行脚本、不做金额计算。业务币种CNY，小数2位，参考容差0.01。输入文件已明确，没有需要追问的信息。"
            draft = library.create_draft(source=[{"kind": "text", "text": text}])
            phase = "create"
            http_requests = []
            original_send = httpx.AsyncClient.send

            async def counted_send(client, request, *args, **kwargs):
                entry = {"phase": phase, "status": None}
                http_requests.append(entry)
                response = await original_send(client, request, *args, **kwargs)
                entry["status"] = response.status_code
                return response

            transport_patch = patch.object(httpx.AsyncClient, "send", counted_send)
            transport_patch.start()
            try:
                for phase in ("create", "revise"):
                    validation_offset = len(validations)
                    diagnostic_offset = len(diagnostics)
                    if phase == "revise":
                        delay = (
                            interval_seconds
                            if interval_seconds is not None
                            else (45 if kind == "zhipu" else 1)
                        )
                        print(
                            json.dumps(
                                {
                                    "provider": kind,
                                    "phase": "cooldown",
                                    "seconds": delay,
                                }
                            ),
                            flush=True,
                        )
                        await asyncio.sleep(delay)
                        draft = library.update_draft(
                            draft["draft_id"],
                            expected_revision=draft["draft_revision"],
                            text="保持现有工具和只读范围，在汇报时再明确说明来源为input.txt。不增加其它需求。",
                        )
                    for retry in range(2):
                        attempts += 1
                        print(
                            json.dumps(
                                {"provider": kind, "phase": phase, "attempt": retry + 1}
                            ),
                            flush=True,
                        )
                        job = await author.begin(
                            draft["draft_id"],
                            expected_revision=draft["draft_revision"],
                            model_id=model["id"],
                        )
                        async for event in author.events(job["job_id"]):
                            if event["type"] == "validation":
                                validations.append(event["data"])
                        draft = library.get_draft(draft["draft_id"])
                        if (
                            draft["state"] == "failed"
                            and draft["job_status"].get("upstream_status") == 429
                            and retry == 0
                        ):
                            await asyncio.sleep(60)
                            continue
                        break
                    result["scenarios"].append(
                        {
                            "name": phase,
                            "state": draft["state"],
                            "error_type": draft["job_status"].get("error_type"),
                            "http_status": draft["job_status"].get("upstream_status"),
                            "questions": draft["questions"],
                            "blockers": draft["spec"].get("blockers")
                            if draft["spec"]
                            else [],
                            "diagnostics": diagnostics[diagnostic_offset:],
                            "validations": validations[validation_offset:],
                        }
                    )
                    if draft["state"] != "ready":
                        raise ValueError("draft_not_ready")
                ref = library.publish(
                    draft["draft_id"],
                    expected_revision=draft["draft_revision"],
                    confirmed_digest=draft["generated_digest"],
                    idempotency_key="provider",
                )
                (root / "input.txt").write_text(
                    "SOP_PROVIDER_OK=100.00", encoding="utf-8"
                )
                await asyncio.sleep(
                    interval_seconds
                    if interval_seconds is not None
                    else (45 if kind == "zhipu" else 1)
                )
                phase = "execute"
                runtime = RunService(db_path=database, skill_library=library)
                request = RunRequest(
                    "provider-run",
                    "执行指定SOP。必须实际调用 read_text 读取工作目录 input.txt，然后原文汇报读取结果。不能只加载 Skill 就视为完成，不能猜测文件内容。",
                    "",
                    "conversation",
                    "general-agent",
                    model["id"],
                    root,
                    sop_mode="explicit",
                    skill_id=ref.skill_id,
                )
                for execution_attempt in range(2):
                    current = replace(
                        request, run_id=f"provider-run-{execution_attempt + 1}"
                    )
                    events = [event async for event in runtime.start(current)]
                    if (
                        events[-1].type == "run.failed"
                        and events[-1].payload.get("error_code") == "rate_limit_error"
                        and execution_attempt == 0
                    ):
                        print(
                            json.dumps(
                                {
                                    "provider": kind,
                                    "phase": "execution_rate_limit_wait",
                                    "seconds": 60,
                                }
                            ),
                            flush=True,
                        )
                        await asyncio.sleep(60)
                        continue
                    break
                successful = [
                    e
                    for e in events
                    if e.type == "tool.completed"
                    and isinstance(e.payload.get("output"), dict)
                    and e.payload["output"].get("success")
                    and "SOP_PROVIDER_OK" in str(e.payload["output"])
                ]
                names = {
                    e.payload["tool_id"]: e.payload.get("name", "")
                    for e in events
                    if e.type == "tool.started"
                }
                execution_tools = [
                    {
                        "name": names.get(e.payload.get("tool_id"), ""),
                        "success": e.payload.get("output", {}).get("success"),
                        "error_code": e.payload.get("output", {}).get("error_code"),
                        "message": str(e.payload.get("output", {}).get("message", ""))[
                            :300
                        ],
                    }
                    for e in events
                    if e.type == "tool.completed"
                    and isinstance(e.payload.get("output"), dict)
                ]
                result["scenarios"].append(
                    {
                        "name": "execute",
                        "status": events[-1].type,
                        "error_code": events[-1].payload.get("error_code"),
                        "exception_type": events[-1].payload.get("exception_type"),
                        "verified_tool_outputs": len(successful),
                        "execution_attempts": execution_attempt + 1,
                        "tools": execution_tools,
                    }
                )
                result["passed"] = events[-1].type == "run.completed" and bool(
                    successful
                )
            except Exception as exc:
                result["error_type"] = type(exc).__name__
            finally:
                await author.shutdown()
                transport_patch.stop()
            result.update(
                elapsed_seconds=round(time.monotonic() - started, 2),
                scenario_attempts=attempts,
                http_requests=http_requests,
                http_request_count=len(http_requests),
                format_validation_failures=len(validations),
            )
            print(json.dumps(result, ensure_ascii=False), flush=True)
            results.append(result)
    return (
        0
        if len(results) == (1 if provider else 3) and all(r["passed"] for r in results)
        else 1
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--scenario", choices=["tools", "skills"], default="tools")
    parser.add_argument("--provider", choices=["deepseek", "zhipu", "siliconflow"])
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--interval-seconds", type=float)
    arguments = parser.parse_args()
    sys.exit(
        asyncio.run(
            (check_skills if arguments.scenario == "skills" else main)(
                arguments.key_file,
                arguments.provider,
                timeout=arguments.timeout,
                interval_seconds=arguments.interval_seconds,
            )
        )
    )
