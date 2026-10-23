from __future__ import annotations
from pathlib import Path

import fnmatch
from typing import Literal

from pydantic import Field

from agent_runtime.execution import ProcessExecutor
from agent_runtime.workspace import (
    atomic_bytes,
    check_version,
    file_version,
    resolve_path,
)
from .contracts import Arguments, typed_tool


class PathInput(Arguments):
    path: str = Field(description="授权工作目录中的相对路径")


class ListFiles(PathInput):
    path: str = "."
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=300)


class SearchText(PathInput):
    path: str = "."
    text: str = Field(min_length=1)
    glob: str = "*"
    limit: int = Field(default=50, ge=1, le=200)


class ReadText(PathInput):
    start_line: int = Field(default=1, ge=1)
    line_count: int = Field(default=300, ge=1, le=1000)
    encoding: Literal["utf-8", "utf-8-sig", "gb18030"] = "utf-8"
    character_offset: int = Field(
        default=0, ge=0, description="超长行续读时使用上次返回的 next_character_offset"
    )


class WriteText(PathInput):
    content: str = Field(max_length=200000)
    expected_version: str = Field(
        description="读取结果中的 version；新文件使用 missing"
    )


class EditText(PathInput):
    old_text: str = Field(min_length=1, max_length=100000)
    new_text: str = Field(max_length=100000)
    expected_version: str


class RunProcess(Arguments):
    program: Literal["python", "shell"]
    code: str = Field(min_length=1, max_length=100000)
    timeout: int = Field(default=60, ge=1, le=120)
    output_limit: int = Field(default=16000, ge=1024, le=64000)
    reason: str = Field(min_length=1, description="说明执行目的和预期读写范围")
    artifact_paths: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="预期生成的工作目录相对文件路径；返回产物版本和大小",
    )


def list_files(context, args):
    root = resolve_path(context.work_dir, args.path)
    paths = sorted(
        root.iterdir(), key=lambda path: (not path.is_dir(), path.name.casefold())
    )
    selected = paths[args.offset : args.offset + args.limit]
    return {
        "success": True,
        "entries": [
            {
                "path": str(p.relative_to(context.work_dir)),
                "is_dir": p.is_dir(),
                "size": p.stat().st_size,
            }
            for p in selected
            if p.resolve().is_relative_to(context.work_dir)
        ],
        "truncated": len(paths) > args.offset + args.limit,
        "next_offset": args.offset + len(selected),
    }


def search_text(context, args):
    root = resolve_path(context.work_dir, args.path)
    matches = []
    visited = 0
    import os
    ignored = {".git", "node_modules", ".runtime", ".venv", ".sopweave", "__pycache__"}

    def candidates():
        for directory, folders, files in os.walk(root, followlinks=False):
            folders[:] = [name for name in folders if name not in ignored
                          and not (Path(directory) / name).is_symlink()]
            for name in files:
                yield Path(directory) / name

    total_bytes = 0
    for path in candidates():
        if not path.is_file() or not path.resolve().is_relative_to(context.work_dir):
            continue
        visited += 1
        if visited > 5000:
            break
        if (
            not fnmatch.fnmatch(path.name, args.glob)
            or path.stat().st_size > 1024 * 1024
        ):
            continue
        total_bytes += path.stat().st_size
        if total_bytes > 32 * 1024 * 1024:
            return {"success": True, "matches": matches, "truncated": True}
        try:
            with path.open(encoding="utf-8") as stream:
                for index, line in enumerate(stream, 1):
                    if args.text.casefold() in line.casefold():
                        matches.append(
                            {
                                "path": str(path.relative_to(context.work_dir)),
                                "line": index,
                                "text": line[:500].rstrip(),
                            }
                        )
                        if len(matches) >= args.limit:
                            return {
                                "success": True,
                                "matches": matches,
                                "truncated": True,
                            }
        except (UnicodeError, OSError):
            continue
    return {"success": True, "matches": matches, "truncated": visited > 5000}


def read_text(context, args):
    path = resolve_path(context.work_dir, args.path)
    version = file_version(path)
    lines, size, truncated, consumed = [], 0, False, 0
    next_offset = 0
    with path.open(encoding=args.encoding) as stream:
        index = 1
        while index < args.start_line:
            skipped = stream.readline(65536)
            if not skipped:
                break
            if skipped.endswith("\n"):
                index += 1
        remaining = args.character_offset
        while remaining:
            skipped = stream.read(min(remaining, 65536))
            if not skipped:
                break
            remaining -= len(skipped)
        while len(lines) < args.line_count and size < 20000:
            line = stream.readline(20000 - size)
            if not line:
                break
            lines.append(line)
            size += len(line)
            consumed = index
            if line.endswith("\n"):
                index += 1
                next_offset = 0
            else:
                next_offset = (
                    args.character_offset if consumed == args.start_line else 0
                ) + len(line)
        truncated = bool(stream.read(1))
    check_version(path, version)
    return {
        "success": True,
        "content": "".join(lines),
        "version": version,
        "truncated": truncated,
        "next_line": index if lines else args.start_line,
        "next_character_offset": next_offset,
    }


def write_text(context, args):
    path = resolve_path(context.work_dir, args.path)
    version = atomic_bytes(path, args.content.encode("utf-8"), args.expected_version)
    return {
        "success": True,
        "path": args.path,
        "version": version,
        "artifacts": [args.path],
    }


def edit_text(context, args):
    path = resolve_path(context.work_dir, args.path)
    check_version(path, args.expected_version)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("精确文本编辑限制为 2MB；请使用分块处理")
    content = path.read_text(encoding="utf-8")
    if content.count(args.old_text) != 1:
        raise ValueError("替换片段必须唯一匹配，请读取更多上下文")
    return write_text(
        context,
        WriteText(
            path=args.path,
            content=content.replace(args.old_text, args.new_text, 1),
            expected_version=args.expected_version,
        ),
    )


async def run_process(context, args):
    from tools.command_policy import _check_blacklist

    if args.program == "shell" and _check_blacklist(args.code):
        raise ValueError("命令被执行策略拒绝")
    paths = [resolve_path(context.work_dir, path) for path in args.artifact_paths]
    result = await ProcessExecutor(context.work_dir, context.event_callback).run(
        args.program, args.code, timeout=args.timeout, output_limit=args.output_limit
    )
    result["artifacts"] = [
        {
            "path": str(path.relative_to(context.work_dir)),
            "version": file_version(path),
            "size": path.stat().st_size,
        }
        for path in paths
        if path.is_file()
    ]
    return result


def specs():
    return [
        typed_tool(
            name, description, model, function, group="core", risk=risk, timeout=150
        )
        for name, description, model, function, risk in [
            (
                "list_files",
                "分页列出目录内的文件及元信息。",
                ListFiles,
                list_files,
                "read",
            ),
            (
                "search_text",
                "在工作目录文本中搜索，返回文件和行号；跳过依赖及二进制。",
                SearchText,
                search_text,
                "read",
            ),
            (
                "read_text",
                "按行读取文本，返回内容、文件版本和续读位置。",
                ReadText,
                read_text,
                "read",
            ),
            (
                "write_text",
                "创建/整体写入文本，必须匹配源文件版本。",
                WriteText,
                write_text,
                "write",
            ),
            (
                "edit_text",
                "唯一片段替换，版本冲突或匹配不唯一时不修改。",
                EditText,
                edit_text,
                "write",
            ),
            (
                "run_process",
                "执行经授权的命令或 Python，返回受限输出；本地可信或隔离容器后端。",
                RunProcess,
                run_process,
                "command",
            ),
        ]
    ]
