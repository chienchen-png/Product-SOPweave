from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import closing, asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.wsgi import WSGIMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_runtime.events import WorkbenchEvent
from agent_runtime.service import ResumeCommand, RunRequest, RunService, stream_owner
from agent_runtime.interactions import Answer, InteractionError
from config.settings import APP_NAME, DB_PATH
from storage.run_store import RunStore
from agent_runtime.background import BackgroundRuns


class StartRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: f"run-{uuid.uuid4().hex}")
    background: bool = False
    prompt: str = Field(min_length=1)
    project_id: str = ""
    conversation_id: str = ""
    agent_id: str = "general-agent"
    model_id: str
    work_dir: str
    work_mode: Literal["manual", "auto"] = "manual"
    user_message_id: str = ""
    sop_mode: Literal['auto','none','explicit'] = 'auto'
    skill_id: str = ''
    sop_inputs: dict[str,str] = Field(default_factory=dict,max_length=40)

    @model_validator(mode='after')
    def valid_sop_selection(self):
        from services.skills.contracts import SopSelection
        SopSelection(mode=self.sop_mode,skill_id=self.skill_id)
        if any(len(k)>80 or len(v)>4000 for k,v in self.sop_inputs.items()):
            raise ValueError('SOP 输入超过长度限制')
        return self
    tool_groups: list[
        Literal["core", "office", "project-data", "analysis", "skills", "interaction"]
    ] = Field(default_factory=list)


class ResumeRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "respond"]
    background: bool = False
    after_seq: int = Field(default=0,ge=0)
    tool_call_id: str = ""
    interaction_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    answers: list[Answer] = Field(default_factory=list, max_length=5)
    disposition: Literal["accept", "skip", "cancel"] = "accept"
    note: str = Field(default="", max_length=4000)


def create_agent_api(
    *,
    run_service: RunService | None = None,
    flask_app=None,
    db_path: str | Path | None = None,
) -> FastAPI:
    resolved_db_path = Path(db_path or DB_PATH)
    service = run_service or RunService(db_path=resolved_db_path)
    background=BackgroundRuns(service,resolved_db_path)
    from services.skills.authoring import SkillAuthoringService
    authoring=SkillAuthoringService(service.skill_library) if getattr(service,'skill_library',None) else None
    @asynccontextmanager
    async def lifespan(_app):
        recovery = asyncio.create_task(service.recover_pending()) if hasattr(service, "recover_pending") else None
        try:
            yield
        finally:
            await background.shutdown()
            if authoring is not None:
                await authoring.shutdown()
            if recovery is not None:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.post("/api/agent/runs")
    async def start_run(payload: StartRunPayload, request: Request):
        run_request = RunRequest(**payload.model_dump(exclude={'background'}))
        if payload.background:
            try:await background.start(run_request.run_id,service.start(run_request))
            except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc)) from exc
            return _background_stream(background,request,run_request.run_id)
        return _event_stream(
            service.start(run_request),
            request=request,
            service=service,
            run_id=run_request.run_id,
        )

    @app.post("/api/agent/runs/{run_id}/resume")
    async def resume_run(run_id: str, payload: ResumeRunPayload, request: Request):
        command = ResumeCommand(**payload.model_dump(exclude={'background','after_seq'}))
        if hasattr(service, "interactions"):
            try:
                service.validate_resume(run_id, command)
            except InteractionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        if payload.background:
            try:await background.start(run_id,service.resume(run_id,command))
            except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc)) from exc
            return _background_stream(background,request,run_id,payload.after_seq)
        return _event_stream(
            service.resume(run_id, command),
            request=request,
            service=service,
            run_id=run_id,
        )

    @app.post("/api/agent/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        service.cancel(run_id)
        return {"success": True, "data": {"run_id": run_id, "status": "stopped"}}

    @app.get("/api/agent/runs/{run_id}")
    async def get_run(run_id: str):
        with closing(_connect(resolved_db_path)) as connection:
            store = RunStore(connection)
            run = store.get_run(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="运行不存在")
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last_seq FROM run_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
        pending = (
            service.interactions.list(run_id, "pending")
            if hasattr(service, "interactions")
            else []
        )
        return {
            "success": True,
            "data": {
                "run": run,
                "last_seq": int(row["last_seq"]),
                "interactions": pending,
            },
        }

    @app.get("/api/agent/interactions")
    async def pending_interactions(project_id: str):
        if not hasattr(service, "interactions"):
            return {"success": True, "data": {"interactions": []}}
        with closing(_connect(resolved_db_path)) as db:
            runs = db.execute(
                "SELECT id FROM runs WHERE project_id=? AND status='waiting' ORDER BY created_at",
                (project_id,),
            ).fetchall()
        pending = [
            item
            for run in runs
            for item in service.interactions.list(run["id"], "pending")
        ]
        return {"success": True, "data": {"interactions": pending}}

    @app.get("/api/agent/runs/{run_id}/events")
    async def get_events(run_id: str, after_seq: int = Query(default=0, ge=0)):
        with closing(_connect(resolved_db_path)) as connection:
            store = RunStore(connection)
            if store.get_run(run_id) is None:
                raise HTTPException(status_code=404, detail="运行不存在")
            rows = store.get_events_after(run_id, after_seq)
        events = [
            {
                "run_id": row["run_id"],
                "seq": row["seq"],
                "type": row["event_type"],
                "payload": row.get("payload") or {},
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        return {"success": True, "data": {"events": events}}

    @app.get('/api/agent/runs/{run_id}/stream')
    async def subscribe_run(run_id:str,request:Request,after_seq:int=Query(default=0,ge=0)):
        with closing(_connect(resolved_db_path)) as db:
            if RunStore(db).get_run(run_id) is None:raise HTTPException(status_code=404,detail='运行不存在')
        return _background_stream(background,request,run_id,after_seq)

    if authoring is not None:
        from agent_runtime.skill_authoring_api import register_skill_authoring_api
        register_skill_authoring_api(app,authoring)
    if flask_app is not None:
        app.mount("/", WSGIMiddleware(flask_app))
    return app


def _event_stream(
    source: AsyncIterator[WorkbenchEvent],
    *,
    request: Request,
    service: RunService,
    run_id: str,
) -> StreamingResponse:
    return StreamingResponse(
        _stream_events(source, request=request, service=service, run_id=run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _background_stream(background,request,run_id,after_seq=0):
    async def encoded():
        async for event in background.events(run_id,request,after_seq):
            yield _encode_sse(event) if event is not None else ': heartbeat\n\n'
    return StreamingResponse(encoded(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})


async def _stream_events(
    source: AsyncIterator[WorkbenchEvent],
    *,
    request: Request,
    service: RunService,
    run_id: str,
) -> AsyncIterator[str]:
    owner = object()
    iterator = source.__aiter__()
    pending: asyncio.Task | None = None
    try:
        while True:
            if await request.is_disconnected():
                break
            if pending is None:
                token = stream_owner.set(owner)
                try:
                    pending = asyncio.create_task(anext(iterator))
                finally:
                    stream_owner.reset(token)
            done, _ = await asyncio.wait({pending}, timeout=15)
            if not done:
                yield ": heartbeat\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None
            yield _encode_sse(event)
    finally:
        if hasattr(service, "cancel_stream"):
            service.cancel_stream(run_id, owner)
        elif service.is_active(run_id):
            service.cancel(run_id)
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


def _encode_sse(event: WorkbenchEvent) -> str:
    data = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection
