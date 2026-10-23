"""Typed request boundary for integrated applications."""
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from routes._utils import error, payload, success
from services.results import ServiceError


class AppCommand(BaseModel):
    model_config = ConfigDict(extra="allow")


class ProjectCommand(AppCommand):
    project_id: str = Field(min_length=1)


class ReconciliationCommand(ProjectCommand):
    model_id: str = ""
    params: dict = Field(default_factory=dict)


class ModelRunCommand(ProjectCommand):
    model_ids: list[str] = Field(min_length=1, max_length=64)
    chart_manifest: list[dict] = Field(default_factory=list, max_length=100)


def invoke(operation, command=AppCommand, **path):
    try:
        data = command.model_validate(payload()).model_dump()
        outcome = operation(data, **path)
        return success(outcome.data, outcome.status_code)
    except ValidationError as exc:
        details = "; ".join(".".join(map(str, item["loc"])) + ": " + item["msg"] for item in exc.errors(include_input=False))
        return error(details, 400)
    except ServiceError as exc:
        return error(str(exc), exc.status_code)
