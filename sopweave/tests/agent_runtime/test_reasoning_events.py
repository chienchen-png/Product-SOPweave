from types import SimpleNamespace
import unittest

from agent_runtime.events import map_sdk_event


class ReasoningEventTests(unittest.TestCase):
    def test_sdk_reasoning_deltas_reach_the_existing_frontend_contract(self):
        for event_type in (
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        ):
            with self.subTest(event_type=event_type):
                event = SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type=event_type, delta="核对金额"),
                )
                self.assertEqual(
                    map_sdk_event(event),
                    [("reasoning.delta", {"delta": "核对金额"})],
                )

    def test_done_events_do_not_duplicate_streamed_reasoning(self):
        event = SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(type="response.reasoning_summary_text.done", text="核对金额"),
        )
        self.assertEqual(map_sdk_event(event), [])
