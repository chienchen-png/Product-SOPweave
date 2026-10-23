"""ASGI stream adapter for durable, review-first skill drafts."""

import json
from fastapi.responses import StreamingResponse, JSONResponse
from services.skills.contracts import SkillProblem, StrictModel
from pydantic import Field


class GenerateDraft(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    model_id: str = Field(min_length=1, max_length=128)


def register_skill_authoring_api(app, service):
    from agent_runtime.api import ResumeRunPayload

    @app.exception_handler(SkillProblem)
    async def skill_problem_handler(_request, exc):
        return JSONResponse(
            {
                "success": False,
                "message": str(exc),
                "code": exc.code,
                "details": exc.details,
            },
            status_code=exc.status,
            headers={"Retry-After": "3"} if exc.status == 429 else {},
        )

    class TrialPayload(StrictModel):
        model_id: str = Field(min_length=1, max_length=128)

    @app.post("/api/skills/drafts/{draft_id}/trial")
    async def trial(draft_id: str, payload: TrialPayload):
        from services.skills.trials import trial_skill

        result = await trial_skill(service.library, draft_id, model_id=payload.model_id)
        return {"success": True, "data": result}

    @app.post("/api/skills/trials/{trial_id}/resume")
    async def trial_resume(trial_id: str, payload: ResumeRunPayload):
        from services.skills.trials import resume_trial
        from agent_runtime.service import ResumeCommand

        return {
            "success": True,
            "data": await resume_trial(
                service.library, trial_id, ResumeCommand(**payload.model_dump())
            ),
        }

    @app.post("/api/skills/drafts/{draft_id}/generate")
    async def generate(draft_id: str, payload: GenerateDraft):
        try:
            job = await service.begin(
                draft_id,
                expected_revision=payload.expected_revision,
                model_id=payload.model_id,
            )
        except SkillProblem as exc:
            return JSONResponse(
                {"success": False, "message": str(exc), "code": exc.code},
                status_code=exc.status,
                headers={"Retry-After": "3"} if exc.status == 429 else {},
            )

        async def events():
            async for event in service.events(job["job_id"]):
                yield f"id: {event['event_id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/skills/drafts/{draft_id}/cancel")
    async def cancel(draft_id: str):
        draft = service.library.get_draft(draft_id)
        await service.cancel(draft_id, job_id=draft["job_id"])
        return {"success": True, "data": service.library.get_draft(draft_id)}
