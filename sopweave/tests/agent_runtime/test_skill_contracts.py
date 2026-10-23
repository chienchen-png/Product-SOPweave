from __future__ import annotations

import unittest
from flask import Flask
from agent_runtime.definitions.skill_registry import SkillRegistry
from agent_runtime.definitions.skill_format import validate_skill_document
from services.skills.contracts import SkillSpec, SkillProblem
from services.skills.rendering import compile_skill
from routes.skills import skills_bp


def sample_spec(**changes):
    values = dict(
        title="月度核对",
        description="核对流水并输出差异",
        required_tools=["read_sheet_range"],
        steps=[
            {
                "id": "read",
                "title": "读取",
                "instruction": "读取指定表范围",
                "tool_ids": ["read_sheet_range"],
            }
        ],
        outputs=["差异报告.md"],
    )
    values.update(changes)
    return SkillSpec.model_validate(values)


class SkillContractTests(unittest.TestCase):
    def test_compile_is_deterministic(self):
        first = compile_skill(sample_spec(), skill_id="user-" + "a" * 32)
        second = compile_skill(sample_spec(), skill_id="user-" + "a" * 32)
        self.assertEqual(first, second)
        self.assertEqual(first.detail["steps"][0]["instruction"], "读取指定表范围")
        self.assertIn("read_sheet_range", first.content)

    def test_unknown_tool_rejected(self):
        with self.assertRaises(SkillProblem):
            compile_skill(
                sample_spec(
                    required_tools=["invented"],
                    steps=[
                        dict(
                            id="one",
                            title="步骤",
                            instruction="执行",
                            tool_ids=["invented"],
                        )
                    ],
                ),
                skill_id="user-" + "a" * 32,
            )

    def test_standard_optional_metadata_preserved(self):
        parsed = validate_skill_document(
            "---\nname: demo\ndescription: demo\nlicense: MIT\nmetadata:\n  author: example\n---\n# Demo"
        )
        self.assertEqual(parsed.manifest.model_extra["license"], "MIT")

    def test_real_builtins_have_nonempty_steps(self):
        registry = SkillRegistry()
        for name in ("data-reconcile", "financial-modeling"):
            self.assertGreater(len(registry.get(name).details["steps"]), 0)

    def test_all_installed_skills_are_readonly(self):
        app = Flask(__name__)
        app.register_blueprint(skills_bp)
        with app.test_client() as client:
            for name in ("data-reconcile", "financial-modeling", "office-files"):
                self.assertEqual(
                    client.put(
                        "/api/skills/" + name, json={"content": "bad"}
                    ).status_code,
                    403,
                )
                self.assertEqual(client.delete("/api/skills/" + name).status_code, 403)
