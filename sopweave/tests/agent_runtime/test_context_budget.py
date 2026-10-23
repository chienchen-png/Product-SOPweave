import unittest

from agent_runtime.context_budget import ContextBudget, ContextLimitError, estimate_tokens


class ContextBudgetTests(unittest.TestCase):
    def test_no_fixed_character_or_item_cap_below_model_budget(self):
        items = [{"role":"user","content":"x"*70000}]
        result = ContextBudget(200000).select(items)
        self.assertEqual(result.items, items)
        many = [{"role":"user","content":"ok"} for _ in range(300)]
        self.assertEqual(ContextBudget(200000).select(many).items, many)

    def test_trims_complete_old_turns_and_preserves_tool_pairs(self):
        old = [{"role":"user","content":"old"*600}, {"role":"assistant","content":"old"*600}]
        current = [{"role":"user","content":"current"},
            {"type":"function_call","call_id":"one","name":"read_text","arguments":"{}"},
            {"type":"function_call_output","call_id":"one","output":"value"}]
        result = ContextBudget(2000).select(old+current)
        self.assertEqual(result.items, current)
        self.assertEqual(result.omitted_items, 2)
        self.assertLessEqual(result.estimated_tokens, result.effective_budget)

    def test_latest_task_or_tool_result_overflow_is_explicit(self):
        for current in ([{"role":"user","content":"x"*3000}],
                        [{"role":"user","content":"task"},{"type":"function_call_output","call_id":"one","output":"x"*3000}]):
            with self.subTest(current=current), self.assertRaises(ContextLimitError):
                ContextBudget(2000).select(current)

    def test_instructions_tools_and_output_reserve_share_one_budget(self):
        items = [{"role":"user","content":"task"}]
        budget = ContextBudget(2000, output_tokens=500)
        self.assertEqual(budget.effective_budget, 1500)
        result = budget.select(items, instructions="rules", tools=[{"name":"read_text"}])
        self.assertGreater(result.estimated_tokens, estimate_tokens(items))
        with self.assertRaises(ContextLimitError):
            budget.select(items, instructions="rules"*600)

    def test_non_ascii_estimation_uses_utf8_and_is_labelled(self):
        self.assertGreater(estimate_tokens("财务"), estimate_tokens("ab"))
