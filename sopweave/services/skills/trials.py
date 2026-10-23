"""SOP trials run against generated fixtures in a separate database/workspace."""

from __future__ import annotations
import json
import shutil
import uuid
from contextlib import closing
from pathlib import Path
import sqlite3
from services.skills.contracts import SkillProblem
from services.skills.library import SkillLibrary


async def trial_skill(library, draft_id, *, model_id, model_provider=None):
    import openpyxl

    draft = library.get_draft(draft_id)
    if draft["state"] != "ready":
        raise SkillProblem("请先生成可发布的当前预览再试运行")
    trial_id = "trial-" + uuid.uuid4().hex
    root = library.root / "trials" / trial_id
    work = root / "workspace"
    work.mkdir(parents=True)
    with closing(sqlite3.connect(root / "app.db")) as db:
        db.executescript(
            (Path(__file__).resolve().parents[2] / "storage/schema.sql").read_text(
                encoding="utf-8"
            )
        )
    for name, amount in [("bank.xlsx", 270), ("ledger.xlsx", 170)]:
        book = openpyxl.Workbook()
        book.active.append(["contract", "amount"])
        book.active.append(["A001", amount])
        book.save(work / name)
        book.close()
    (work / "input.txt").write_text(
        "样例金额：270.00、170.00。请用程序核对，差额100.00。", encoding="utf-8"
    )
    isolated = SkillLibrary(root / "app.db", data_dir=root)
    copied = isolated.create_draft()
    for source in draft["source"]:
        if source.get("kind") == "bundle":
            target = isolated.root / "drafts" / copied["draft_id"] / "bundle"
            shutil.copytree(library.data_dir / source["bundle_relative_path"], target)
            source = dict(
                source, bundle_relative_path=target.relative_to(root).as_posix()
            )
            copied = isolated.attach_source(
                copied["draft_id"],
                expected_revision=copied["draft_revision"],
                source=source,
            )
    copied = isolated.update_draft(
        copied["draft_id"],
        expected_revision=copied["draft_revision"],
        spec=draft["spec"],
    )
    ref = isolated.publish(
        copied["draft_id"],
        expected_revision=copied["draft_revision"],
        confirmed_digest=copied["generated_digest"],
        idempotency_key=trial_id,
    )
    info = dict(trial_id=trial_id, model_id=model_id, skill_id=ref.skill_id)
    (root / "trial.json").write_text(json.dumps(info), encoding="utf-8")
    return await _execute(library, root, info, model_provider=model_provider)


async def resume_trial(library, trial_id, command, *, model_provider=None):
    if (
        not trial_id.startswith("trial-")
        or not all(c in "0123456789abcdef" for c in trial_id[6:])
        or len(trial_id) != 38
    ):
        raise SkillProblem("试运行 ID 无效")
    root = library.root / "trials" / trial_id
    if not (root / "trial.json").is_file():
        raise SkillProblem("试运行已过期或不存在")
    return await _execute(
        library,
        root,
        json.loads((root / "trial.json").read_text(encoding="utf-8")),
        command=command,
        model_provider=model_provider,
    )


async def _execute(library, root, info, *, command=None, model_provider=None):
    from agent_runtime.service import RunService, RunRequest
    from agent_runtime.models.factory import ModelRuntimeFactory, resolve_model_config
    from agent_runtime.models.types import RuntimePurpose
    from storage.model_store import ModelStore

    clients = []
    settings_provider = None
    usage_recorder = None
    usage_identity = None
    if model_provider is None:
        try:
            with library.connection() as db:
                record = ModelStore(db).resolve_runtime_model(info["model_id"])
        except ValueError as exc:
            raise SkillProblem("试运行模型不可用，请重新选择可用模型") from exc
        config = resolve_model_config(record)
        from services.model_usage import get_usage_recorder
        from agent_runtime.models.usage import UsageIdentity
        usage_recorder = get_usage_recorder(library.db_path)
        usage_identity = UsageIdentity.from_config(config)
        factory = ModelRuntimeFactory(usage_recorder=usage_recorder)

        def model_provider(_):
            model = factory.build_agents_model(config)
            clients.append(model._client)
            return model

        def settings_provider(_):
            return factory.build_model_settings(config, RuntimePurpose.INTEGRATED_APP)

    isolated = SkillLibrary(root / "app.db", data_dir=root)
    service = RunService(
        db_path=root / "app.db",
        skill_library=isolated,
        model_provider=model_provider,
        model_settings_provider=settings_provider,
        usage_recorder=usage_recorder,
        usage_identity=usage_identity,
    )
    request = RunRequest(
        info["trial_id"],
        "在样例副本中执行已选SOP。可用bank.xlsx、ledger.xlsx、input.txt；合同列contract，金额列amount，币种CNY，容差0.01。仅使用现有工具。",
        "trial-project",
        "trial-conversation",
        "general-agent",
        info["model_id"],
        root / "workspace",
        sop_mode="explicit",
        skill_id=info["skill_id"],
        sop_inputs={
            "currency": "CNY",
            "tolerance": "0.01",
            "base_file": "bank.xlsx",
            "detail_file": "ledger.xlsx",
        },
    )
    try:
        source = (
            service.resume(info["trial_id"], command)
            if command
            else service.start(request)
        )
        events = [event.to_dict() async for event in source]
    finally:
        for client in clients:
            await client.close()
    # Include earlier rounds when an approval resumes the same immutable trial.
    from storage.run_store import RunStore

    with closing(service._connect()) as db:
        events = [
            {
                "run_id": info["trial_id"],
                "seq": row["seq"],
                "type": row["event_type"],
                "payload": row["payload"],
                "created_at": row["created_at"],
            }
            for row in RunStore(db).get_events_after(info["trial_id"], 0)
        ]
    names = {
        e["payload"]["tool_id"]: e["payload"].get("name", "")
        for e in events
        if e["type"] == "tool.started"
    }
    tools = [
        dict(event["payload"], name=names.get(event["payload"].get("tool_id"), ""))
        for event in events
        if event["type"] == "tool.completed"
    ]
    failed = [
        tool
        for tool in tools
        if isinstance(tool.get("output"), dict)
        and tool["output"].get("success") is False
    ]
    status = events[-1]["type"].removeprefix("run.") if events else "failed"
    required = set(isolated.get(info["skill_id"])["required_tools"]) - {
        "read_skill",
        "ask_user",
    }
    observed = {
        tool["name"]
        for tool in tools
        if isinstance(tool.get("output"), dict)
        and tool["output"].get("success") is True
    }
    missing_tools = sorted(required - observed)
    artifacts = [
        path.relative_to(root / "workspace").as_posix()
        for path in (root / "workspace").rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".json"}
    ]
    return dict(
        trial_id=info["trial_id"],
        status=status,
        passed=status == "completed"
        and bool(tools)
        and not failed
        and not missing_tools,
        missing_tools=missing_tools,
        tools=tools,
        artifacts=artifacts,
        events=events,
        interactions=service.interactions.list(info["trial_id"], "pending"),
        message="本次仅使用程序生成的样例副本；waiting表示仍需审批或补充信息，不能视为已通过。",
    )
