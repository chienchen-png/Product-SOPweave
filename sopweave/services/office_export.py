"""One Pandoc conversion path for file editors and integrated reports."""
from pathlib import Path
import shutil
import subprocess
import tempfile

from services.results import ServiceError

ROOT = Path(__file__).resolve().parents[1]


def find_pandoc() -> str | None:
    found = shutil.which('pandoc')
    bundled = ROOT / 'offline_packages/pandoc/pandoc.exe'
    return found or (str(bundled) if bundled.is_file() else None)


def export_markdown_docx(content: str, resource_root: Path) -> bytes:
    pandoc = find_pandoc()
    if pandoc is None:
        raise ServiceError('Pandoc 未安装，请安装到 offline_packages/pandoc/pandoc.exe 或 PATH', 501)
    if not isinstance(content, str):
        raise ServiceError('报告内容必须是 Markdown 文本')
    with tempfile.TemporaryDirectory(prefix='finance-word-') as directory:
        output = Path(directory) / 'report.docx'
        command = [pandoc, '-f', 'markdown+tex_math_dollars', '-t', 'docx',
                   f'--resource-path={Path(resource_root).resolve()}', '-o', str(output), '-']
        reference = ROOT / 'offline_packages/pandoc/reference-template.docx'
        if reference.is_file():
            command.insert(1, f'--reference-doc={reference}')
        try:
            process = subprocess.run(command, input=content.encode('utf-8'), capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise ServiceError('Word 导出超时（60s）', 500) from exc
        except OSError as exc:
            raise ServiceError(f'Pandoc 执行失败：{exc}', 500) from exc
        if process.returncode:
            raise ServiceError('Word 导出失败：' + process.stderr.decode('utf-8', errors='replace')[:300], 500)
        return output.read_bytes()
