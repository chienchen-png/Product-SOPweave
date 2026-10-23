"""One lifecycle for local trusted execution and isolated Docker workers."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from config.settings import AGENT_EXECUTOR, AGENT_WORKER_IMAGE


class ProcessExecutor:
    def __init__(self, work_dir: Path, callback=None):
        self.work_dir = Path(work_dir).resolve()
        self.callback = callback

    async def run(self, program: str, code: str, *, timeout: float, output_limit: int):
        backend = AGENT_EXECUTOR
        container = f"sopweave-{uuid.uuid4().hex}"
        if backend == "docker":
            image = AGENT_WORKER_IMAGE
            if not image:
                raise ValueError("容器执行需要配置 SOPWEAVE_EXECUTOR_IMAGE")
            command = [
                "docker",
                "run",
                "--rm",
                "--name",
                container,
                "--network=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=64",
                "--memory=1g",
                "--cpus=2",
                "--read-only",
                "--tmpfs=/tmp:rw,size=128m",
                "--mount",
                f"type=bind,source={self.work_dir},target=/work",
                "-w",
                "/work",
                image,
                *(
                    ["python", "-I", "-X", "utf8", "-u", "-c", code]
                    if program == "python"
                    else ["/bin/sh", "-c", code]
                ),
            ]
        elif backend == "local":
            command = (
                [sys.executable, "-I", "-X", "utf8", "-u", "-c", code]
                if program == "python"
                else [
                    os.environ.get("COMSPEC", "cmd.exe"),
                    "/d",
                    "/s",
                    "/c",
                    "chcp 65001>nul & " + code,
                ]
                if os.name == "nt"
                else ["/bin/sh", "-c", code]
            )
        else:
            raise ValueError("未知程序执行后端")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "SystemRoot",
                "TEMP",
                "TMP",
                "APPDATA",
                "LOCALAPPDATA",
                "COMSPEC",
                "PATHEXT",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
            }
        }
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        options = (
            {"creationflags": 0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.work_dir,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **options,
        )
        job = None
        readers = []
        captured = {"stdout": "", "stderr": ""}
        truncated = False

        async def read(stream, name):
            nonlocal truncated
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while chunk := await stream.read(4096):
                text = decoder.decode(chunk)
                remaining = max(
                    0, output_limit - len(captured["stdout"]) - len(captured["stderr"])
                )
                accepted = text[:remaining]
                captured[name] += accepted
                truncated = truncated or len(text) > remaining
                if accepted and self.callback:
                    self.callback("tool.output", {"stream": name, "delta": accepted})
            tail = decoder.decode(b"", final=True)
            remaining = max(
                0, output_limit - len(captured["stdout"]) - len(captured["stderr"])
            )
            captured[name] += tail[:remaining]
            truncated = truncated or len(tail) > remaining

        try:
            if os.name == "nt":
                from agent_runtime.windows_job import WindowsJob

                job = WindowsJob(process.pid)
            readers = [
                asyncio.create_task(read(process.stdout, "stdout")),
                asyncio.create_task(read(process.stderr, "stderr")),
            ]
            await asyncio.wait_for(process.wait(), timeout)
            if job:
                job.close()
                job = None
            await asyncio.gather(*readers)
            return {
                "success": process.returncode == 0,
                **captured,
                "exit_code": process.returncode,
                "truncated": truncated,
                "backend": backend,
            }
        except TimeoutError:
            return {
                "success": False,
                "error_code": "process_timeout",
                **captured,
                "truncated": truncated,
            }
        finally:
            if job:
                job.close()
            elif os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.returncode is None:
                process.kill()
            if backend == "docker":
                # Stop daemon-owned execution even when the docker client dies.
                cleanup = await asyncio.create_subprocess_exec(
                    "docker",
                    "rm",
                    "-f",
                    container,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(cleanup.wait(), 15)
            await process.wait()
            await asyncio.gather(*readers, return_exceptions=True)
