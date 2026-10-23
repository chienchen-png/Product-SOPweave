from __future__ import annotations

from dataclasses import dataclass, replace, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from agent_runtime.definitions.skill_registry import SkillRegistry
from agent_runtime.context_budget import ContextBudget
from storage.connections import ConnectionFactory


AskUserCallback = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class WorkbenchRunContext:
    project_id: str
    work_dir: Path
    run_id: str
    conversation_id: str
    work_mode: str
    agent_id: str
    skill_registry: SkillRegistry | None = None
    skill_runtime: Any = None
    ask_user_callback: AskUserCallback | None = None
    database_factory: ConnectionFactory | None = None
    event_callback: Callable[[str, dict[str, Any]], None] | None = None
    active_toolsets: set[str] = field(
        default_factory=lambda: {"core", "skills", "interaction"}
    )
    allowed_toolsets: frozenset[str] = frozenset()
    progressive_tools: bool = False
    denied_operations: tuple[dict[str, Any], ...] = ()
    usage_identity: Any = None
    context_budget: ContextBudget = field(default_factory=ContextBudget)
    tool_durations: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_dir", Path(self.work_dir).resolve())
        if self.work_mode not in {"manual", "auto"}:
            raise ValueError("work_mode 必须是 manual 或 auto")

    def with_work_mode(self, work_mode: str) -> "WorkbenchRunContext":
        return replace(self, work_mode=work_mode)
