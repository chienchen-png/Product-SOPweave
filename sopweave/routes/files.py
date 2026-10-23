from __future__ import annotations

import ntpath
import os
import shutil
import tempfile
from pathlib import Path

from flask import Blueprint, request, send_file

from config.paths import resolve_work_dir
from routes._utils import error, payload, success
from storage.db import get_db
from storage.project_store import ProjectStore
from agent_runtime.workspace import (
    atomic_bytes,
    file_version,
    path_lock,
    read_text_snapshot,
    resolve_path,
)

files_bp = Blueprint("files_api", __name__, url_prefix="/api/files")

TEXT_EXTENSIONS = {
    ".txt",
    ".csv",
    ".log",
    ".md",
    ".markdown",
    ".py",
    ".sql",
    ".json",
    ".yml",
    ".yaml",
    ".xml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".bat",
    ".ps1",
    ".ini",
}


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}


def _viewer_type(target: Path) -> str:
    suffix = target.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix in {
        ".py",
        ".sql",
        ".json",
        ".js",
        ".ts",
        ".css",
        ".html",
        ".xml",
        ".yml",
        ".yaml",
        ".bat",
        ".ps1",
    }:
        return "code"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix == ".docx":
        return "docx"
    if suffix in {".xls", ".xlsx"}:
        return "xlsx"
    if suffix == ".ppt":
        return "ppt"
    if suffix == ".pptx":
        return "pptx"
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return "unsupported"


def _is_text_readable(target: Path) -> bool:
    return _viewer_type(target) in {"text", "markdown", "code"}


def _meta_payload(root: Path, target: Path) -> dict:
    entry = _entry(root, target)
    viewer_type = _viewer_type(target)
    return {
        **entry,
        "viewer_type": viewer_type,
        "can_edit": viewer_type in {"text", "markdown", "code"},
        # D1（2026-08-24）：xls 归一化到 xlsx viewer_type；白名单补 xls（防御）。
        # pdf 保留 external 能力（右键场景），双击走浏览器预览由前端分发。
        "can_open_external": viewer_type
        in {"docx", "xlsx", "xls", "ppt", "pptx", "pdf"},
    }


def _project_root(project_id: str) -> tuple[dict | None, Path | None, tuple | None]:
    if not project_id:
        return None, None, error("项目ID不能为空")
    project = ProjectStore(get_db()).get_by_id(project_id)
    if not project:
        return None, None, error("项目不存在", 404)
    root = resolve_work_dir(project["work_dir"])
    # 2026-08-20：集成应用专属项目（work_dir 指向应用固定目录）可能尚未创建——
    # 目录不存在时自动创建（仅影响新目录，现有项目 work_dir 均已存在不受影响）
    if not root.exists():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return project, root, error(f"工作目录创建失败：{exc}", 500)
    if not root.is_dir():
        return project, root, error("项目工作目录不是文件夹", 400)
    return project, root, None


def _resolve_inside(
    root: Path, relative_path: str | None = None, *, destination: bool = False
) -> Path:
    # Enforce Windows-compatible names on every host before resolving device
    # names or creating parents. CPython 3.14 is the supported runtime.
    if destination and ntpath.isreserved(relative_path or ""):
        raise ValueError('文件或文件夹名称无效：不能包含 <>:"|?*、控制字符、末尾空格或句点，也不能使用 CON、NUL 等系统保留名称')
    resolved = resolve_path(root, relative_path or "")
    current = root / (relative_path or "")
    while current != root and current != current.parent:
        if _is_link(current):
            raise ValueError("文件操作不支持符号链接或目录联接")
        current = current.parent
    return resolved


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _visible_children(root: Path, directory: Path, warnings: list[str]):
    for child in directory.iterdir():
        if child.name.startswith("."):
            continue
        if _is_link(child):
            warnings.append(f"已跳过符号链接或目录联接：{_relative(root, child)}")
            continue
        yield child


def _reject_copy_links(directory: str, names: list[str]) -> list[str]:
    for name in names:
        if _is_link(Path(directory) / name):
            raise ValueError("复制的文件夹包含符号链接或目录联接，请先移除链接")
    return []


def _relative(root: Path, target: Path) -> str:
    return target.relative_to(root).as_posix()


def _entry(root: Path, target: Path) -> dict:
    stat = target.stat()
    return {
        "name": target.name,
        "path": _relative(root, target),
        "type": "directory" if target.is_dir() else "file",
        "size": stat.st_size if target.is_file() else None,
        "updated_at": stat.st_mtime,
    }


def _tree_entry(root: Path, target: Path, warnings: list[str]) -> dict:
    entry = _entry(root, target)
    if not target.is_dir():
        return entry
    children = []
    for child in _visible_children(root, target, warnings):
        children.append(_tree_entry(root, child, warnings))
    children.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    entry["children"] = children
    return entry


def _safe_upload_name(filename: str) -> str:
    return Path(filename.replace("\\", "/")).name.strip()


def _safe_upload_relative_path(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        raise ValueError("上传路径必须是项目内的相对路径")
    parts = []
    for part in normalized.split("/"):
        cleaned = part.strip()
        if cleaned in {".", ".."}:
            raise ValueError("上传路径不能包含 . 或 ..")
        if not cleaned:
            continue
        parts.append(cleaned)
    return "/".join(parts)


@files_bp.get("/tree")
def list_tree():
    project_id = request.args.get("project_id", "").strip()
    relative_path = request.args.get("path", "").strip()
    recursive = request.args.get("recursive") == "1"
    try:
        limit = max(1, min(500, int(request.args.get("limit", 500))))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return error("limit/offset 必须是整数", 422)
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        current = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not current.exists():
        return error("目录不存在", 404)
    if not current.is_dir():
        return error("目标不是目录", 400)
    entries = []
    warnings = []
    try:
        for child in _visible_children(root, current, warnings):
            entries.append(
                _tree_entry(root, child, warnings) if recursive else _entry(root, child)
            )
    except OSError as exc:
        return error(f"读取目录失败：{exc}", 500)
    entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    total = len(entries)
    if not recursive:
        entries = entries[offset:offset + limit]
    next_cursor = str(offset + limit) if not recursive and offset + limit < total else ""
    return success(
        {
            "path": _relative(root, current) if current != root else "",
            "entries": entries,
            "warnings": warnings,
            "total": total,
            "next_cursor": next_cursor,
        }
    )


@files_bp.get("/read")
def read_file():
    project_id = request.args.get("project_id", "").strip()
    relative_path = request.args.get("path", "").strip()
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not target.exists():
        return error("文件不存在", 404)
    if not target.is_file():
        return error("目标不是文件", 400)
    try:
        content, version = read_text_snapshot(target)
    except UnicodeDecodeError:
        return error("当前文件不是 UTF-8 文本文件", 415)
    except OSError as exc:
        return error(f"读取文件失败：{exc}", 500)
    return success(
        {"file": _meta_payload(root, target), "content": content, "version": version}
    )


@files_bp.get("/meta")
def file_meta():
    project_id = request.args.get("project_id", "").strip()
    relative_path = request.args.get("path", "").strip()
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not target.exists():
        return error("文件不存在", 404)
    if not target.is_file():
        return error("目标不是文件", 400)
    return success({"file": _meta_payload(root, target)})


@files_bp.get("/raw")
def read_raw_file():
    project_id = request.args.get("project_id", "").strip()
    relative_path = request.args.get("path", "").strip()
    encoding = (request.args.get("encoding", "utf-8") or "utf-8").strip()
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not target.exists():
        return error("文件不存在", 404)
    if not target.is_file():
        return error("目标不是文件", 400)
    if request.args.get("binary") == "1" and _viewer_type(target) in {
        "image",
        "pdf",
        "docx",
        "xlsx",
        "ppt",
        "pptx",
    }:
        try:
            return send_file(target)
        except OSError as exc:
            return error(f"读取文件失败：{exc}", 500)
    if not _is_text_readable(target):
        return error("当前文件类型不支持文本读取", 415)
    try:
        content, version = read_text_snapshot(target, encoding)
    except UnicodeDecodeError:
        return error(f"文件无法按 {encoding} 解码", 415)
    except OSError as exc:
        return error(f"读取文件失败：{exc}", 500)
    return success(
        {
            "file": _meta_payload(root, target),
            "encoding": encoding,
            "content": content,
            "version": version,
        }
    )


@files_bp.get("/resource/<project_id>/<path:relative_path>")
def read_image_resource(project_id: str, relative_path: str):
    """Image base for Markdown renderers, keeping document source relative."""
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not target.is_file():
        return error("图片不存在", 404)
    if target.suffix.lower() not in IMAGE_EXTENSIONS:
        return error("此资源接口只提供图片", 415)
    return send_file(target)


@files_bp.post("/create")
def create_item():
    data = payload()
    project_id = str(data.get("project_id", "")).strip()
    relative_path = str(data.get("path", "")).strip()
    item_type = str(data.get("type", "file")).strip()
    if item_type not in {"file", "directory"}:
        return error("类型必须是 file 或 directory")
    if not relative_path:
        return error("路径不能为空")
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path, destination=True)
    except ValueError as exc:
        return error(str(exc), 400)
    if target.exists():
        return error("目标已存在", 409)
    if not target.parent.is_dir():
        return error("父目录不存在，请刷新后重新选择目录", 404)
    try:
        if item_type == "directory":
            target.mkdir()
        else:
            atomic_bytes(
                target, str(data.get("content", "")).encode("utf-8"), "missing"
            )
    except ValueError as exc:
        return error(str(exc), 409)
    except OSError as exc:
        return error(f"创建失败：{exc}", 500)
    return success(_entry(root, target), 201)


@files_bp.put("/write")
def write_file():
    data = payload()
    expected = data.get("expected_version")
    if not isinstance(expected, str) or not expected:
        return error("缺少 expected_version，请先读取文件", 400)
    project_id = str(data.get("project_id", "")).strip()
    relative_path = str(data.get("path", "")).strip()
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path, destination=True)
    except ValueError as exc:
        return error(str(exc), 400)
    if target.exists() and not target.is_file():
        return error("目标不是文件", 400)
    try:
        version = atomic_bytes(
            target, str(data.get("content", "")).encode("utf-8"), expected
        )
    except ValueError as exc:
        return error(str(exc), 409)
    except OSError as exc:
        return error(f"写入文件失败：{exc}", 500)
    return success({**_entry(root, target), "version": version})


@files_bp.put("/rename")
def rename_item():
    data = payload()
    project_id = str(data.get("project_id", "")).strip()
    source_path = str(data.get("path", "")).strip()
    target_path = str(data.get("new_path", "")).strip()
    if not source_path or not target_path:
        return error("原路径和新路径不能为空")
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        source = _resolve_inside(root, source_path)
        destination = _resolve_inside(root, target_path, destination=True)
    except ValueError as exc:
        return error(str(exc), 400)
    if source == root:
        return error("不能重命名项目根目录", 400)
    if not source.exists():
        return error("目标不存在", 404)
    if source.is_dir() and destination.is_relative_to(source):
        return error("不能将文件夹移动到自身或其子目录", 400)
    if destination.exists():
        return error("新路径已存在", 409)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
    except OSError as exc:
        return error(f"重命名失败：{exc}", 500)
    return success(_entry(root, destination))


@files_bp.post("/copy")
def copy_item():
    data = payload()
    project_id = str(data.get("project_id", "")).strip()
    source_path = str(data.get("path", "")).strip()
    target_path = str(data.get("target_path", "")).strip()
    if not source_path or not target_path:
        return error("源路径和目标路径不能为空")
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        source = _resolve_inside(root, source_path)
        destination = _resolve_inside(root, target_path, destination=True)
    except ValueError as exc:
        return error(str(exc), 400)
    if not source.exists():
        return error("源路径不存在", 404)
    if source == root or (source.is_dir() and destination.is_relative_to(source)):
        return error("不能复制项目根目录或将文件夹复制到自身及其子目录", 400)
    if destination.exists():
        return error("目标路径已存在", 409)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with path_lock(destination), tempfile.TemporaryDirectory(
            prefix=".copy-", dir=destination.parent
        ) as staging:
            temporary = Path(staging) / "payload"
            if source.is_dir():
                shutil.copytree(source, temporary, ignore=_reject_copy_links)
            else:
                shutil.copy2(source, temporary)
            if destination.exists():
                return error("目标路径已存在", 409)
            temporary.rename(destination)
    except ValueError as exc:
        return error(str(exc), 400)
    except OSError as exc:
        return error(f"复制失败：{exc}", 500)
    return success(_entry(root, destination), 201)


@files_bp.post("/upload")
def upload_file():
    project_id = request.form.get("project_id", "").strip()
    directory_path = request.form.get("path", "").strip()
    reuse_identical = request.form.get("reuse_if_identical") == "1"
    try:
        relative_upload_path = _safe_upload_relative_path(
            request.form.get("relative_path", "").strip()
        )
    except ValueError as exc:
        return error(str(exc), 400)
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return error("请选择要上传的文件")
    filename = relative_upload_path or _safe_upload_name(uploaded.filename)
    if not filename:
        return error("文件名无效")
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        directory = _resolve_inside(root, directory_path)
        destination_path = (
            f"{directory_path}/{filename}" if directory_path else filename
        )
        destination = _resolve_inside(root, destination_path, destination=True)
    except ValueError as exc:
        return error(str(exc), 400)
    if not directory.exists() or not directory.is_dir():
        return error("上传目录不存在", 404)
    if destination.exists() and not reuse_identical:
        return error("同名文件已存在", 409)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with path_lock(destination), tempfile.TemporaryDirectory(
            prefix=".upload-", dir=destination.parent
        ) as staging:
            temporary = Path(staging) / "payload"
            uploaded.save(temporary)
            if destination.exists():
                if (
                    reuse_identical
                    and destination.is_file()
                    and file_version(destination) == file_version(temporary)
                ):
                    return success({**_entry(root, destination), "reused": True})
                return error("同名文件已存在", 409)
            temporary.rename(destination)
    except OSError as exc:
        return error(f"上传失败：{exc}", 500)
    return success(_entry(root, destination), 201)


@files_bp.delete("")
def delete_item():
    data = payload()
    project_id = str(data.get("project_id", "")).strip()
    relative_path = str(data.get("path", "")).strip()
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not target.exists():
        return error("目标不存在", 404)
    if target == root:
        return error("不能删除项目根目录", 400)
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as exc:
        return error(f"删除失败：{exc}", 500)
    return success({"path": relative_path})


@files_bp.post("/open-external")
def open_external():
    data = payload()
    project_id = str(data.get("project_id", "")).strip()
    relative_path = str(data.get("path", "")).strip()
    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if not target.exists() or not target.is_file():
        return error("目标文件不存在", 404)

    viewer_type = _viewer_type(target)
    if viewer_type not in {"docx", "xlsx", "xls", "ppt", "pptx", "pdf"}:
        return error("当前文件类型不支持外部打开", 400)

    try:
        os.startfile(str(target))
    except OSError as exc:
        return error(f"外部打开失败：{exc}", 500)

    return success(
        {
            "file": _meta_payload(root, target),
            "opened": True,
            "message": "已调用本机程序打开文件",
        }
    )


@files_bp.post("/export-docx")
def export_docx():
    """D4：Markdown → Word (docx)。

    入参 {project_id, path, content?}：
      - content 传入时优先（未保存的编辑内容直接导出）；否则读 path 对应文件。
    转换：pandoc -f markdown+tex_math_dollars -t docx。
    Pandoc 缺失 → 501 明确错误（设计文档：不降级，保证质量一致）。
    """
    data = payload()
    project_id = str(data.get("project_id", "")).strip()
    relative_path = str(data.get("path", "")).strip()
    content = data.get("content")
    if not project_id or not relative_path:
        return error("project_id 与 path 不能为空")

    _project, root, failure = _project_root(project_id)
    if failure:
        return failure
    try:
        target = _resolve_inside(root, relative_path)
    except ValueError as exc:
        return error(str(exc), 400)
    if (
        target.exists()
        and target.is_file()
        and _viewer_type(target) not in {"markdown", "text"}
    ):
        return error("仅支持 Markdown/文本文件导出 Word", 415)

    if content is None:
        if not target.exists():
            return error("文件不存在", 404)
        if not target.is_file():
            return error("目标不是文件", 400)
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return error("当前文件不是 UTF-8 文本文件", 415)
        except OSError as exc:
            return error(f"读取文件失败：{exc}", 500)

    from io import BytesIO
    from services.office_export import export_markdown_docx
    from services.results import ServiceError

    try:
        raw = export_markdown_docx(content, root)
    except ServiceError as exc:
        return error(str(exc), exc.status_code)
    return send_file(
        BytesIO(raw),
        as_attachment=True,
        download_name=f"{Path(relative_path).stem or 'document'}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
