from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml
from flask import Flask

from agent_runtime.definitions.agent_registry import AgentRegistry
from agent_runtime.definitions.skill_registry import (
    SkillNotFoundError,
    SkillPermissionError,
    SkillRegistry,
    SkillResourceNotFoundError,
    SkillValidationError,
    _parse_document_details,
)
from routes.skills import skills_bp


class SkillRegistryTests(unittest.TestCase):
    def test_main_workflow_excludes_parameter_and_constraint_lists(self):
        details = _parse_document_details('# 核对\n\n1. 读取数据\n2. 输出报告\n\n## 参数约定\n\n- filters 是字段条件\n- 禁止无条件写入\n')
        self.assertEqual([step['title'] for step in details['steps']], ['读取数据', '输出报告'])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skills = self.root / "skills"
        self.profiles = self.root / "agent_profiles"
        _write_skill(self.skills, "alpha")
        _write_agent(self.profiles, ["alpha"])
        agents = AgentRegistry(
            self.profiles,
            skill_roots=[self.skills],
            known_toolsets={"workbench"},
        )
        self.registry = SkillRegistry(self.skills, agent_registry=agents)

    def test_frontmatter_is_validated_and_listed_for_an_agent(self) -> None:
        items = self.registry.list_for_agent(["alpha"])

        self.assertEqual([item.name for item in items], ["alpha"])
        self.assertEqual(items[0].description, "Alpha description. 触发词：甲、乙")
        self.assertTrue(items[0].user_invocable)
        self.assertIn("# Alpha", self.registry.read_skill("general-agent", "alpha"))

    def test_frontmatter_name_must_match_the_directory(self) -> None:
        _write_skill(self.skills, "wrong-folder", name="other-name")

        with self.assertRaisesRegex(SkillValidationError, "目录.*name"):
            self.registry.list_all()

    def test_references_scripts_and_assets_are_read_within_the_skill(self) -> None:
        skill = self.skills / "alpha"
        for relative, content in {
            "references/topic.md": "reference",
            "scripts/check.py": "print('ok')",
            "assets/template.md": "template",
        }.items():
            target = skill / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        self.assertEqual(
            self.registry.read_resource("general-agent", "alpha", "references/topic.md"),
            "reference",
        )
        self.assertEqual(
            self.registry.read_resource("general-agent", "alpha", "scripts/check.py"),
            "print('ok')",
        )
        self.assertEqual(
            self.registry.read_resource("general-agent", "alpha", "assets/template.md"),
            "template",
        )
        self.assertEqual(
            self.registry.read_resource("general-agent", "alpha", "topic.md"),
            "reference",
        )

    def test_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(SkillPermissionError):
            self.registry.read_resource("general-agent", "alpha", "../secret.txt")
        with self.assertRaises(SkillPermissionError):
            self.registry.read_resource("general-agent", "alpha", "C:/secret.txt")

    def test_agent_cannot_read_an_unlisted_skill(self) -> None:
        _write_skill(self.skills, "beta")

        with self.assertRaisesRegex(SkillPermissionError, "未授权"):
            self.registry.read_skill("general-agent", "beta")

    def test_missing_skill_and_resource_raise_typed_errors(self) -> None:
        with self.assertRaises(SkillNotFoundError):
            self.registry.list_for_agent(["missing"])
        with self.assertRaises(SkillResourceNotFoundError):
            self.registry.read_resource("general-agent", "alpha", "references/missing.md")

    def test_duplicate_skill_names_are_rejected(self) -> None:
        _write_skill(self.skills, "alias", name="alpha")

        with self.assertRaisesRegex(SkillValidationError, "重复.*alpha"):
            self.registry.list_all()

    def test_skill_api_uses_the_same_registry(self) -> None:
        app = Flask(__name__)
        app.config["SKILL_REGISTRY"] = self.registry
        app.register_blueprint(skills_bp)
        client = app.test_client()

        listing = client.get("/api/skills")
        detail = client.get("/api/skills/alpha")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["data"]["skills"][0]["name"], "alpha")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("# Alpha", detail.get_json()["data"]["content"])


def _write_skill(root: Path, directory: str, *, name: str | None = None) -> None:
    target = root / directory
    target.mkdir(parents=True)
    frontmatter = {
        "name": name or directory,
        "description": f"{(name or directory).title()} description. 触发词：甲、乙",
        "user-invocable": True,
    }
    content = (
        "---\n"
        + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + f"# {(name or directory).title()}\n"
    )
    (target / "SKILL.md").write_text(content, encoding="utf-8")


def _write_agent(root: Path, skills: list[str]) -> None:
    target = root / "general-agent"
    target.mkdir(parents=True)
    manifest = {
        "id": "general-agent",
        "name": "General agent",
        "version": "1.0.0",
        "description": "General",
        "enabled": True,
        "default_model_id": "",
        "toolsets": ["workbench"],
        "tools": ["read_skill"],
        "skills": skills,
        "approval_policy": {},
        "max_turns": 20,
    }
    (target / "agent.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (target / "instructions.md").write_text("Instructions", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
