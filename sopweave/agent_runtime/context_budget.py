"""One model-input budget for runtime selection and occupancy reporting.

Providers use different tokenizers. UTF-8 bytes plus framing overhead are a
conservative estimate, not billable usage; actual usage stays in the Token ledger.
"""
from dataclasses import dataclass
import json
from typing import Any

DEFAULT_CONTEXT_WINDOW = 200000


class ContextLimitError(ValueError):
    pass


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-8"))


def without_skill_transcripts(items: list[dict]) -> list[dict]:
    calls = {item.get('call_id') for item in items
             if item.get('type')=='function_call' and item.get('name')=='read_skill' and item.get('call_id')}
    return [item for item in items if item.get('call_id') not in calls]


@dataclass(frozen=True)
class BudgetSelection:
    items: list[dict]
    estimated_tokens: int
    effective_budget: int
    omitted_items: int


@dataclass(frozen=True)
class ContextBudget:
    context_window: int = DEFAULT_CONTEXT_WINDOW
    output_tokens: int = 0
    window_source: str = "default"

    @classmethod
    def from_record(cls, record: dict):
        try:
            window = int(record.get("context_window") or 0)
        except (TypeError, ValueError):
            window = 0
        return cls(window, window_source="configured") if window > 0 else cls()

    @property
    def response_reserve(self) -> int:
        return max(int(self.context_window * .15), self.output_tokens)

    @property
    def effective_budget(self) -> int:
        return max(0, self.context_window - self.response_reserve)

    def select(self, items: list[dict], instructions: str = "", tools: Any = ()) -> BudgetSelection:
        overhead = estimate_tokens(instructions or "") + estimate_tokens(tools) + 256
        costs = [estimate_tokens(item) + 12 for item in items]
        total = overhead + sum(costs)
        start = 0
        # Drop complete user turns only; the newest task and its tool chain stay intact.
        boundaries = [index for index, item in enumerate(items) if item.get("role") == "user"]
        for boundary in boundaries[1:]:
            if total <= self.effective_budget:
                break
            total -= sum(costs[start:boundary])
            start = boundary
        if total > self.effective_budget:
            raise ContextLimitError("当前任务、系统指令或工具结果超过模型有效输入预算，请缩短任务或分批读取数据")
        return BudgetSelection(items[start:], total, self.effective_budget, start)
