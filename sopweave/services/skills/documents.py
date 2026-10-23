"""Bounded document extraction in short-lived, credential-free subprocesses."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zipfile import ZipFile

from services.skills.contracts import SkillProblem

_slots = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class ParseLimits:
    max_file_bytes: int = 20 * 1024 * 1024
    max_text_characters: int = 100000
    max_pages: int = 100
    timeout_seconds: float = 30
    max_rss_bytes: int = 256 * 1024 * 1024


@dataclass(frozen=True)
class ParsedSource:
    text: str
    locations: list[dict]
    content_digest: str
    complete: bool = True
    warnings: list[str] = field(default_factory=list)


def parse_source(path: Path, *, limits: ParseLimits = ParseLimits()) -> ParsedSource:
    import psutil

    path = Path(path).resolve()
    if not path.is_file() or path.stat().st_size > limits.max_file_bytes:
        raise SkillProblem("附件不存在或超过单文件 20 MiB 限制")
    if path.suffix.lower() not in {".md", ".txt", ".docx", ".pdf"}:
        raise SkillProblem(
            "当前支持 MD、TXT、DOCX 和文本 PDF；旧 DOC 或扫描件需要先转换"
        )
    if not _slots.acquire(timeout=limits.timeout_seconds):
        raise SkillProblem("文档解析正忙，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="sopweave-sop-parser-") as directory:
            output = Path(directory) / "result.json"
            environment = {
                k: v
                for k, v in os.environ.items()
                if not any(
                    word in k.upper()
                    for word in ("API_KEY", "TOKEN", "SECRET", "SOPWEAVE_", "FINANCE_")
                )
            }
            command = [
                sys.executable,
                "-m",
                "services.skills.documents",
                "--worker",
                str(path),
                str(output),
                json.dumps(asdict(limits)),
            ]
            process = subprocess.Popen(
                command,
                cwd=Path(__file__).resolve().parents[2],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            started = time.monotonic()
            try:
                monitor = psutil.Process(process.pid)
                while process.poll() is None:
                    if time.monotonic() - started > limits.timeout_seconds:
                        raise SkillProblem("文档解析超时，请拆分文档后重试")
                    try:
                        if monitor.memory_info().rss > limits.max_rss_bytes:
                            raise SkillProblem("文档解析超过 256 MiB 内存限制")
                    except psutil.NoSuchProcess:
                        break
                    time.sleep(0.02)
                if not output.exists():
                    raise SkillProblem("文档解析进程异常，请检查文档是否损坏")
                result = json.loads(output.read_text(encoding="utf-8"))
                if "error" in result:
                    raise SkillProblem(result["error"])
                return ParsedSource(**result)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    finally:
        _slots.release()


def _extract(path: Path, limits: ParseLimits):
    chunks = []
    locations = []
    length = 0

    def add(text, location):
        nonlocal length
        text = text.strip()
        if not text:
            return
        length += len(text) + 2
        if length > limits.max_text_characters:
            raise SkillProblem(
                "提取文字超过 100000 字符，不能截断后当作完整 SOP，请拆分文档"
            )
        locations.append(dict(location, text=text))
        chunks.append(text)

    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        raw = path.read_bytes()
        for encoding in (
            "utf-8-sig",
            "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "gb18030",
        ):
            try:
                add(raw.decode(encoding), {"paragraph": 1})
                break
            except UnicodeError:
                continue
        else:
            raise SkillProblem("文本编码无法识别，请保存为 UTF-8")
    elif suffix == ".docx":
        from docx import Document
        from docx.table import Table
        from services.skills.archives import checked_entries

        with ZipFile(path) as archive:
            checked_entries(archive)
        for number, block in enumerate(Document(path).iter_inner_content(), 1):
            text = (
                "\n".join(
                    " | ".join(cell.text for cell in row.cells) for row in block.rows
                )
                if isinstance(block, Table)
                else block.text
            )
            add(
                text,
                {
                    "block": number,
                    "kind": "table" if isinstance(block, Table) else "paragraph",
                },
            )
    else:
        from pypdf import PdfReader

        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise SkillProblem("加密 PDF 暂不支持，请提供解密后的文本 PDF")
        if len(reader.pages) > limits.max_pages:
            raise SkillProblem("PDF 超过 100 页，请拆分文档")
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if not text.strip():
                raise SkillProblem(
                    f"PDF 第 {number} 页没有可提取文本，可能为扫描页；请先 OCR，不能忽略该页"
                )
            add(text, {"page": number})
    if not chunks:
        raise SkillProblem("文档没有可识别文字")
    return ParsedSource(
        "\n\n".join(chunks), locations, hashlib.sha256(path.read_bytes()).hexdigest()
    )


def _main():
    path, output, limits = (
        Path(sys.argv[2]),
        Path(sys.argv[3]),
        ParseLimits(**json.loads(sys.argv[4])),
    )
    try:
        result = asdict(_extract(path, limits))
    except SkillProblem as exc:
        result = {"error": str(exc)}
    except Exception:
        result = {"error": "文档格式损坏或无法完整解析，请重新导出后上传"}
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    _main()
