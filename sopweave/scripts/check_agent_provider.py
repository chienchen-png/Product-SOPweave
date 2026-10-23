from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.models.contract import contract_runner_for  # noqa: E402
from agent_runtime.models.factory import ModelRuntimeFactory, resolve_model_config  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Probe an OpenAI-compatible provider")
    result.add_argument("--provider", required=True, choices=[
        "deepseek", "zhipu", "siliconflow", "dashscope", "openai_compatible",
    ])
    result.add_argument("--base-url", required=True)
    result.add_argument("--model", required=True)
    result.add_argument("--api-key-env", required=True)
    return result


async def run(args: argparse.Namespace, api_key: str) -> dict:
    config = resolve_model_config({
        "id": "provider-smoke",
        "name": args.model,
        "api_model": args.model,
        "api_url": args.base_url,
        "api_key": api_key,
        "provider_kind": args.provider,
        "api_mode": "chat_completions",
        "capabilities_json": {"chat": True, "stream": True, "tools": True},
        "request_options_json": {},
        "provider_options_json": {"timeout": 30, "max_retries": 0},
    })
    client = ModelRuntimeFactory(usage_recorder=None).build_direct_client(config)
    try:
        return (await contract_runner_for(config, client).run()).to_dict()
    finally:
        await client.aclose()


def main() -> int:
    args = parser().parse_args()
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        print(json.dumps({
            "status": "skipped",
            "reason": f"environment variable {args.api_key_env} is not set",
        }, ensure_ascii=False))
        return 2
    result = asyncio.run(run(args, api_key))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
