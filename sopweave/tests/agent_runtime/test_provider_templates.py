from __future__ import annotations

import unittest

from agent_runtime.models.templates import (
    get_provider_template,
    list_provider_templates,
)


class ProviderTemplateTests(unittest.TestCase):
    def test_registry_exposes_only_the_four_supported_setup_choices(self) -> None:
        templates = list_provider_templates()

        self.assertEqual(
            [template.template_id for template in templates],
            ["deepseek", "zhipu", "siliconflow", "custom"],
        )
        self.assertEqual(
            [template.base_url for template in templates],
            [
                "https://api.deepseek.com",
                "https://open.bigmodel.cn/api/paas/v4",
                "https://api.siliconflow.cn/v1",
                None,
            ],
        )
        self.assertNotIn("dashscope", [template.template_id for template in templates])

    def test_official_templates_lock_runtime_fields_and_custom_does_not(self) -> None:
        for template_id in ("deepseek", "zhipu", "siliconflow"):
            with self.subTest(template_id=template_id):
                public = get_provider_template(template_id).to_dict()
                self.assertFalse(public["is_custom"])
                self.assertEqual(public["api_mode"], "chat_completions")
                self.assertEqual(public["auth_mode"], "bearer")
                self.assertTrue(public["base_url"].startswith("https://"))

        custom = get_provider_template("custom").to_dict()
        self.assertTrue(custom["is_custom"])
        self.assertIsNone(custom["base_url"])
        self.assertEqual(custom["provider_kind"], "openai_compatible")

    def test_unknown_template_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "供应商模板"):
            get_provider_template("dashscope")


if __name__ == "__main__":
    unittest.main()
