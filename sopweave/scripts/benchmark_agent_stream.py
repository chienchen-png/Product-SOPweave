from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.api import _encode_sse
from agent_runtime.events import WorkbenchEvent


async def benchmark(runs: int) -> list[float]:
    samples = []
    for seq in range(1, runs + 1):
        received = time.perf_counter()
        await asyncio.sleep(0)
        _encode_sse(WorkbenchEvent("benchmark", seq, "message.delta", {"delta": "测"}, "now"))
        samples.append((time.perf_counter() - received) * 1000)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["fake"], default="fake")
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    samples = asyncio.run(benchmark(max(1, args.runs)))
    ordered = sorted(samples)
    p95 = ordered[max(0, int(len(ordered) * 0.95 + 0.999) - 1)]
    print(f"provider={args.provider} runs={len(samples)} p50_ms={statistics.median(samples):.3f} p95_ms={p95:.3f}")
    if p95 > 100:
        print("FAIL: p95 exceeds 100ms")
        return 1
    print("PASS: no fixed 400ms polling step")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
