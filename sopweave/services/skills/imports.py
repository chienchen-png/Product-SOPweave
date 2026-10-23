"""Public GitHub/ZIP ingestion with explicit compatibility and adaptation review."""

from __future__ import annotations
import difflib
import hashlib
import io
import json
import re
import time
from pathlib import PurePosixPath
from urllib.parse import urlsplit, quote, unquote
from zipfile import ZipFile, BadZipFile
import requests
import yaml
from agent_runtime.definitions.skill_format import validate_skill_document
from services.skills.archives import checked_entries
from services.skills.contracts import SkillProblem, SkillSpec
from services.skills.rendering import tool_index


class SkillImporter:
    def __init__(self, library):
        self.library = library

    def preview_zip(self, data: bytes, *, skill_path=""):
        if len(data) > 20 * 1024 * 1024:
            raise SkillProblem("导入包超过20 MiB")
        try:
            with ZipFile(io.BytesIO(data)) as archive:
                entries = checked_entries(archive)
                files = {
                    e.filename.replace("\\", "/"): archive.read(e)
                    for e in entries
                    if not e.is_dir()
                }
        except (BadZipFile, RuntimeError, UnicodeError) as exc:
            raise SkillProblem("ZIP 文件损坏、加密或编码无效") from exc
        return self._preview(
            files,
            skill_path,
            {"kind": "zip", "checksum": hashlib.sha256(data).hexdigest()},
        )

    @staticmethod
    def _fetch(url, deadline, *, limit=20 * 1024 * 1024, redirects=0):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc not in {
            "api.github.com",
            "raw.githubusercontent.com",
            "github.com",
            "codeload.github.com",
        }:
            raise SkillProblem("仅支持公开 GitHub HTTPS 来源")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SkillProblem("GitHub 导入超过30秒，请缩小目录范围后重试")
        if redirects > 5:
            raise SkillProblem("GitHub 来源重定向次数超过限制")
        try:
            with requests.get(
                url,
                timeout=min(remaining, 15),
                stream=True,
                allow_redirects=False,
                headers={"Accept": "application/vnd.github+json"},
            ) as response:
                if 300 <= response.status_code < 400:
                    from urllib.parse import urljoin

                    return SkillImporter._fetch(
                        urljoin(url, response.headers.get("Location", "")),
                        deadline,
                        limit=limit,
                        redirects=redirects + 1,
                    )
                if response.status_code != 200:
                    raise SkillProblem(
                        f"GitHub 读取失败（HTTP {response.status_code}），请检查公开链接或稍后重试"
                    )
                result = bytearray()
                for chunk in response.iter_content(65536):
                    result.extend(chunk)
                    if len(result) > limit or time.monotonic() > deadline:
                        raise SkillProblem("GitHub 来源超过大小或时间限制")
                return bytes(result)
        except requests.RequestException as exc:
            raise SkillProblem("GitHub 网络请求失败，请稍后重试") from exc

    def preview_github(self, url, *, skill_path=""):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.query
            or parsed.fragment
        ):
            raise SkillProblem("请输入公开 GitHub 仓库或 Skill 目录的 HTTPS 链接")
        parts = [unquote(p) for p in parsed.path.strip("/").split("/")]
        if len(parts) < 2 or not all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts[:2]
        ):
            raise SkillProblem("GitHub 仓库路径无效")
        owner, repo = parts[:2]
        repo = repo.removesuffix(".git")
        ref = "HEAD"
        folder = ""
        if len(parts) > 2:
            if len(parts) < 4 or parts[2] not in {"tree", "blob"}:
                raise SkillProblem("请选择仓库或 tree 目录链接")
            ref = parts[3]
            folder = "/".join(parts[4:])
            if folder == "SKILL.md":
                folder = ""
            if folder.endswith("/SKILL.md"):
                folder = folder[:-9]
        deadline = time.monotonic() + 30
        api = f"https://api.github.com/repos/{owner}/{repo}"
        commit = json.loads(
            self._fetch(
                api + "/commits/" + quote(ref, safe=""), deadline, limit=3 * 1024 * 1024
            )
        )["sha"]
        if not re.fullmatch("[0-9a-f]{40}", commit):
            raise SkillProblem("GitHub 未返回可固定的提交")
        tree = json.loads(
            self._fetch(
                api + f"/git/trees/{commit}?recursive=1",
                deadline,
                limit=5 * 1024 * 1024,
            )
        )
        if tree.get("truncated"):
            raise SkillProblem("GitHub 文件树不完整，请选择较小仓库")
        paths = [
            item
            for item in tree.get("tree", [])
            if item["type"] == "blob"
            and (not folder or item["path"].startswith(folder.rstrip("/") + "/"))
        ]
        roots = sorted(
            {
                str(PurePosixPath(item["path"]).parent)
                for item in paths
                if PurePosixPath(item["path"]).name == "SKILL.md"
            }
        )
        if not roots:
            raise SkillProblem("所选目录没有 SKILL.md")
        if not skill_path and len(roots) > 1:
            return {
                "choices": roots,
                "resolved_url": f"https://github.com/{owner}/{repo}/tree/{commit}",
                "commit": commit,
            }
        selected = skill_path or roots[0]
        if selected not in roots:
            raise SkillProblem("请选择列表中的 Skill 目录")
        paths = [
            p for p in paths if selected == "." or p["path"].startswith(selected + "/")
        ]
        if len(paths) > 256 or sum(p.get("size", 0) for p in paths) > 20 * 1024 * 1024:
            raise SkillProblem("所选 Skill 超过256个文件或20 MiB")
        files = {}
        downloaded = 0
        for item in paths:
            if item.get("mode") == "120000":
                raise SkillProblem("GitHub Skill 包含符号链接")
            relative = (
                item["path"][len(selected) + 1 :] if selected != "." else item["path"]
            )
            if (
                ":" in relative
                or ".." in PurePosixPath(relative).parts
                or relative.startswith("/")
            ):
                raise SkillProblem("GitHub Skill 文件路径无效")
            files[relative] = self._fetch(
                f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/"
                + quote(item["path"], safe="/"),
                deadline,
            )
            downloaded += len(files[relative])
            if downloaded > 20 * 1024 * 1024:
                raise SkillProblem("GitHub 来源合计超过20 MiB")
        # Reuse the same archive path/collision checks for both sources.
        encoded = io.BytesIO()
        with ZipFile(encoded, "w") as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        preview = self.preview_zip(encoded.getvalue())
        if "draft" in preview:
            draft = preview["draft"]
            source = {
                "kind": "github",
                "url": url,
                "commit": commit,
                "checksum": hashlib.sha256(encoded.getvalue()).hexdigest(),
            }
            updated = self.library.attach_source(
                draft["draft_id"],
                expected_revision=draft["draft_revision"],
                source=source,
            )
            preview["draft"] = self.library.update_draft(
                updated["draft_id"],
                expected_revision=updated["draft_revision"],
                spec=updated["spec"],
            )
        preview["commit"] = commit
        return preview

    def _preview(self, files, skill_path, origin):
        roots = sorted(
            {
                str(PurePosixPath(name).parent)
                for name in files
                if PurePosixPath(name).name == "SKILL.md"
            }
        )
        if not roots:
            raise SkillProblem("导入包没有 SKILL.md")
        if not skill_path and len(roots) > 1:
            return {"choices": roots}
        chosen = skill_path or roots[0]
        if chosen not in roots:
            raise SkillProblem("请选择有效的 Skill 目录")
        prefix = chosen + "/" if chosen != "." else ""
        selected = {
            name[len(prefix) :]: body
            for name, body in files.items()
            if name.startswith(prefix)
        }
        try:
            original = selected["SKILL.md"].decode("utf-8-sig")
            parsed = validate_skill_document(original)
        except (UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise SkillProblem(
                "SKILL.md 必须为 UTF-8 编码且包含有效的 name、description 和正文"
            ) from exc
        index = tool_index()
        requested = parsed.manifest.model_extra.get("allowed-tools", [])
        if isinstance(requested, str):
            requested = (
                re.split(r"[,\s]+", requested.strip()) if requested.strip() else []
            )
        if not isinstance(requested, list) or any(
            not isinstance(v, str) for v in requested
        ):
            raise SkillProblem("allowed-tools 必须是工具名文本或列表")
        requested = list(
            dict.fromkeys(
                requested
                + [
                    name
                    for name, group in index.items()
                    if group in parsed.manifest.toolsets
                ]
            )
        )
        unknown = sorted(set(requested) - index.keys())
        supported = [name for name in requested if name in index]
        blockers = []
        if unknown:
            blockers.append("尚未适配的工具：" + "、".join(unknown))
        if any(
            "scripts" in PurePosixPath(p).parts
            or PurePosixPath(p).suffix.lower()
            in {
                ".py",
                ".sh",
                ".ps1",
                ".bat",
                ".cmd",
                ".exe",
                ".dll",
                ".js",
                ".mjs",
                ".ipynb",
            }
            or PurePosixPath(p).name
            in {"requirements.txt", "pyproject.toml", "package.json"}
            for p in selected
        ):
            blockers.append("包中包含脚本或环境依赖，需要改为本项目现有工具后使用")
        if re.search(
            r"\b(?:bash|pip|npm|python|powershell|uv)\b|\$CODEX_HOME|/mnt/",
            parsed.body,
            re.I,
        ):
            blockers.append("原文包含命令或其它运行环境约定，需要审阅并适配")
        if len(parsed.body) > 4000:
            blockers.append(
                "原始流程较长，需要 AI 提炼为文字步骤；完整原文已保留在引用资源中"
            )
        original_ref = "references/__imported_original_SKILL.md"
        if original_ref in selected:
            raise SkillProblem("导入包含项目保留的原文引用名称")
        draft = self.library.create_draft(source=[dict(origin, text=original)])
        folder = self.library.root / "drafts" / draft["draft_id"] / "bundle"
        for name, body in selected.items():
            if name == "SKILL.md":
                continue
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        target = folder / original_ref
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.encode("utf-8"))
        resource_hashes = {
            p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*")
            if p.is_file()
        }
        source = dict(
            kind="bundle",
            bundle_relative_path=folder.relative_to(self.library.data_dir).as_posix(),
            original_ref=original_ref,
            resource_hashes=resource_hashes,
        )
        draft = self.library.attach_source(
            draft["draft_id"], expected_revision=draft["draft_revision"], source=source
        )
        body = (
            parsed.body.strip()
            if len(parsed.body) <= 4000
            else "先读取 " + original_ref + "，完成适配并解决阻断项后执行。"
        )
        spec = SkillSpec(
            title=parsed.manifest.name,
            description=parsed.manifest.description[:1000],
            required_tools=supported,
            steps=[
                dict(
                    id="imported-flow",
                    title="原始业务流程",
                    instruction=body or "请补充业务流程",
                    tool_ids=supported,
                )
            ],
            blockers=blockers,
        )
        draft = self.library.update_draft(
            draft["draft_id"],
            expected_revision=draft["draft_revision"],
            spec=spec.model_dump(),
        )
        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                draft["preview"]["content"].splitlines(),
                fromfile="原始 SKILL.md",
                tofile="项目适配预览",
                lineterm="",
            )
        )
        return dict(
            draft=draft,
            original_content=original,
            diff=diff,
            unsupported_tools=unknown,
            supported_tools=supported,
            warnings=blockers,
            requires_adaptation=bool(blockers),
        )
