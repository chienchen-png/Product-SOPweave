"""Skill configuration API. Installed definitions are always read-only."""

from __future__ import annotations
from flask import Blueprint, current_app, request
from pydantic import ValidationError, Field, model_validator
from agent_runtime.definitions.agent_registry import AgentRegistry
from agent_runtime.definitions.skill_registry import (
    SkillNotFoundError,
    SkillRegistry,
    SkillValidationError,
)
from routes._utils import error, success, payload
from services.skills.contracts import (
    SkillProblem,
    SkillReadonly,
    StrictModel,
    SkillSpec,
    DraftAnswer,
)
from pathlib import Path
import uuid

skills_bp = Blueprint("skills_api", __name__, url_prefix="/api/skills")


class CreateDraftPayload(StrictModel):
    text: str = Field(default="", max_length=100000)
    base_skill_id: str | None = None
    base_version: int | None = Field(default=None, ge=1, strict=True)
    project_id: str = ""
    conversation_id: str = ""
    run_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def base_matches_version(self):
        if bool(self.base_skill_id) != bool(self.base_version):
            raise ValueError("编辑 Skill 时必须同时提供标识与基础版本")
        return self


class UpdateDraftPayload(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    spec: SkillSpec | None = None
    answers: list[DraftAnswer] | None = Field(default=None, max_length=5)
    text: str | None = Field(default=None, max_length=100000)


class PublishDraftPayload(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    confirmed_digest: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SetEnabledPayload(StrictModel):
    enabled: bool = Field(strict=True)
    expected_version: int = Field(ge=1, strict=True)
    expected_generation: int = Field(ge=0, strict=True)


def _registry() -> SkillRegistry:
    registry = current_app.config.get("SKILL_REGISTRY")
    if isinstance(registry, SkillRegistry):
        return registry
    registry = SkillRegistry(
        agent_registry=current_app.config.get("AGENT_REGISTRY") or AgentRegistry(),
        library=current_app.config.get("SKILL_LIBRARY"),
    )
    current_app.config["SKILL_REGISTRY"] = registry
    return registry


@skills_bp.get("")
def list_skills():
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 50))
        cursor = request.args.get("cursor", "")
        query = request.args.get("query", "")[:1000]
        include_disabled = request.args.get("include_disabled") == "true"
        builtin = [
            s.to_dict()
            for s in _registry().list_all()
            if s.name != "office-files"
            or request.args.get("include_internal") == "true"
        ]
        builtin = (
            []
            if cursor
            else [
                s
                for s in builtin
                if query.casefold() in (s["name"] + " " + s["description"]).casefold()
            ][:limit]
        )
        library = current_app.config.get("SKILL_LIBRARY")
        user = (
            library.list(
                query=query,
                limit=limit - len(builtin),
                after=cursor,
                include_disabled=include_disabled,
            )
            if library and len(builtin) < limit
            else []
        )
        return success(
            {
                "skills": builtin + user,
                "generation": library.generation() if library else 0,
                "user_skills_enabled": library is not None,
                "next_cursor": user[-1]["name"]
                if user and len(builtin) + len(user) == limit
                else "",
            }
        )
    except ValueError:
        return error("分页参数无效", 422)
    except SkillValidationError as exc:
        return error(f"Skill 定义无效: {exc}", 500)


@skills_bp.get("/<skill_name>")
def get_skill(skill_name):
    try:
        include_disabled = request.args.get("include_disabled") == "true"
        return success(
            _registry()
            .get(skill_name, include_disabled=include_disabled)
            .to_detail_dict()
        )
    except SkillNotFoundError:
        return error("Skill 不存在", 404)


@skills_bp.route("/<skill_name>", methods=["PUT", "DELETE"])
def readonly_skill(skill_name):
    if skill_name.startswith("user-"):
        library = _library()
        if request.method == "DELETE":
            version = payload().get("expected_version")
            if type(version) is not int or version < 1:
                raise SkillProblem("请提供有效的 Skill 版本")
            library.delete(skill_name, expected_version=version)
            return success({"deleted": True, "generation": library.generation()})
        return error(
            "请通过草稿预览和确认发布修改用户 Skill",
            422,
            {"code": "skill_draft_required"},
        )
    try:
        _registry().get(skill_name)
    except SkillNotFoundError:
        return error("Skill 不存在", 404)
    return error("内置 Skill 为只读，请创建用户 SOP", 403, {"code": "skill_readonly"})


@skills_bp.patch("/<skill_name>/enabled")
def set_skill_enabled(skill_name):
    if not skill_name.startswith("user-"):
        try:
            _registry().get(skill_name)
        except SkillNotFoundError:
            return error("Skill 不存在", 404)
        raise SkillReadonly("内置 Skill 为只读")
    body = SetEnabledPayload.model_validate(payload()).model_dump()
    return success(
        _library().set_enabled(
            skill_name,
            enabled=body["enabled"],
            expected_version=body["expected_version"],
            expected_generation=body["expected_generation"],
        )
    )


def _library():
    library = current_app.config.get("SKILL_LIBRARY")
    if library is None:
        raise SkillProblem("用户 Skill 功能尚未启用")
    return library


@skills_bp.errorhandler(SkillProblem)
def skill_error(exc):
    return error(str(exc), exc.status, {"code": exc.code, "details": exc.details})


@skills_bp.errorhandler(ValidationError)
def skill_validation_error(exc):
    return error(
        "Skill 字段校验失败",
        422,
        {
            "code": "skill_invalid",
            "fields": [
                {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                for e in exc.errors(include_url=False)
            ],
        },
    )


@skills_bp.post("/drafts")
def create_draft():
    body = CreateDraftPayload.model_validate(payload()).model_dump()
    base = body.get("base_skill_id")
    if base and not base.startswith("user-"):
        raise SkillReadonly("内置 Skill 为只读，请创建新 SOP")
    text = body["text"]
    sources = [{"kind": "text", "text": text}] if text else []
    if body.get("conversation_id"):
        from services.conversation_history import conversation_source

        with _library().connection() as db:
            sources.append(
                conversation_source(
                    db,
                    str(body.get("project_id", "")),
                    None if body.get("run_ids") else str(body["conversation_id"]),
                    body.get("run_ids"),
                )
            )
    draft = _library().create_draft(
        base_skill_id=base, base_version=body.get("base_version"), source=sources
    )
    return success(draft, 201)


@skills_bp.get("/drafts/<draft_id>")
def get_draft(draft_id):
    return success(_library().get_draft(draft_id))


@skills_bp.get('/drafts')
def list_drafts():
    try:
        limit = int(request.args.get('limit',20))
    except ValueError as exc:
        raise SkillProblem('草稿分页参数无效') from exc
    return success(_library().list_drafts(limit=limit, after=request.args.get('cursor','')))


@skills_bp.patch("/drafts/<draft_id>")
def update_draft(draft_id):
    body = UpdateDraftPayload.model_validate(payload()).model_dump()
    return success(
        _library().update_draft(
            draft_id,
            expected_revision=body["expected_revision"],
            spec=body.get("spec"),
            answers=body.get("answers"),
            text=body.get("text"),
        )
    )


@skills_bp.post("/drafts/<draft_id>/publish")
def publish_draft(draft_id):
    body = PublishDraftPayload.model_validate(payload()).model_dump()
    ref = _library().publish(
        draft_id,
        expected_revision=body.get("expected_revision"),
        confirmed_digest=body.get("confirmed_digest", ""),
        idempotency_key=body.get("idempotency_key", ""),
    )
    return success({"ref": ref.model_dump(), "generation": _library().generation()})


@skills_bp.before_request
def bound_skill_payload():
    if request.content_length and request.content_length > 51 * 1024 * 1024:
        return error(
            "Skill 请求超过51 MiB上限", 413, {"code": "skill_payload_too_large"}
        )
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.is_json
        and not isinstance(request.get_json(silent=True), dict)
    ):
        return error("Skill 请求必须为 JSON 对象", 422, {"code": "skill_invalid"})


@skills_bp.post("/drafts/<draft_id>/sources")
def upload_source(draft_id):
    from dataclasses import asdict
    from services.skills.documents import parse_source

    library = _library()
    draft = library.get_draft(draft_id)
    upload = request.files.get("file")
    if upload is None:
        raise SkillProblem("请选择上传文档")
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".md", ".txt", ".docx", ".pdf"}:
        raise SkillProblem("支持 MD、TXT、DOCX 和文本 PDF")
    directory = library.root / "drafts" / draft_id / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (uuid.uuid4().hex + suffix)
    try:
        size = 0
        with target.open("wb") as output:
            while chunk := upload.stream.read(1024 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise SkillProblem("单附件不能超过20 MiB")
                output.write(chunk)
        parsed = parse_source(target)
        source = dict(
            asdict(parsed),
            source_id=target.stem,
            kind="document",
            display_name=Path(upload.filename).name,
            size=size,
            relative_path=target.relative_to(library.data_dir).as_posix(),
        )
        updated = library.attach_source(
            draft_id, expected_revision=draft["draft_revision"], source=source
        )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return success(updated)


@skills_bp.post("/imports/preview")
def preview_import():
    from services.skills.imports import SkillImporter

    importer = SkillImporter(_library())
    if "file" in request.files:
        upload = request.files["file"]
        data = upload.stream.read(20 * 1024 * 1024 + 1)
        return success(
            importer.preview_zip(data, skill_path=request.form.get("skill_path", ""))
        )
    body = payload()
    return success(
        importer.preview_github(
            str(body.get("url", "")), skill_path=str(body.get("skill_path", ""))
        )
    )
