from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from agent_runtime.definitions.agent_registry import AgentRegistry
from agent_runtime.definitions.skill_registry import SkillRegistry
from routes.agents import agents_bp
from routes.skills import skills_bp


class DefinitionRouteTests(unittest.TestCase):
    def test_listing_keeps_valid_agents_and_reports_invalid_file(self):
        bad = self.profiles/'broken-agent'
        bad.mkdir()
        (bad/'agent.yaml').write_text('id: [broken',encoding='utf8')
        (bad/'instructions.md').write_text('bad',encoding='utf8')
        response = self.client.get('/api/agents')
        self.assertEqual(response.status_code, 200)
        data = response.get_json()['data']
        self.assertEqual([a['id'] for a in data['agents']], ['general-agent'])
        self.assertEqual(data['definition_errors'][0]['path'], 'broken-agent/agent.yaml')
        self.assertEqual(self.client.get('/api/agents/broken-agent').status_code, 422)
        self.assertEqual(self.client.get('/api/agents/broken-agent/tools').status_code, 422)
        # An unrelated invalid file must also not prevent editing a valid Agent.
        saved = self.client.put('/api/agents/general-agent',json={
            'manifest_yaml':self.manifest_path.read_text(encoding='utf8'),
            'instructions':'updated valid instructions',
        })
        self.assertEqual(saved.status_code, 200)
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profiles = self.root / "agent_profiles"
        self.skills = self.root / "skills"
        profile = self.profiles / "general-agent"
        skill = self.skills / "demo-skill"
        profile.mkdir(parents=True)
        skill.mkdir(parents=True)
        self.manifest_path = profile / "agent.yaml"
        self.instructions_path = profile / "instructions.md"
        self.skill_path = skill / "SKILL.md"
        self.manifest_path.write_text(
            "id: general-agent\nname: 通用智能体\nversion: 1.0.0\n"
            "enabled: true\ntoolsets: [workbench]\n"
            "skills: [demo-skill]\nmax_turns: 30\n",
            encoding="utf-8",
        )
        self.instructions_path.write_text("# 指令\n\n请准确工作。\n", encoding="utf-8")
        self.skill_path.write_text(
            "---\nname: demo-skill\ndescription: 演示 Skill\n---\n\n"
            "## 职责\n\n演示。\n\n## 约束\n\n保持准确。\n",
            encoding="utf-8",
        )
        agent_registry = AgentRegistry(
            self.profiles,
            skill_roots=[self.skills],
            known_toolsets={"workbench"},
        )
        skill_registry = SkillRegistry(self.skills, agent_registry=agent_registry)
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            AGENT_REGISTRY=agent_registry,
            SKILL_REGISTRY=skill_registry,
            DEFINITION_AUDIT_PATH=self.root / "definition-audit.jsonl",
        )
        app.register_blueprint(agents_bp)
        app.register_blueprint(skills_bp)
        self.client = app.test_client()

    def test_tool_catalog_respects_agent_allowlist(self) -> None:
        self.manifest_path.write_text(
            self.manifest_path.read_text(encoding="utf-8") + "tools: [read_text]\n",
            encoding="utf-8",
        )
        response = self.client.get("/api/agents/general-agent/tools")
        catalog = response.get_json()["data"]["catalog"]
        self.assertEqual(
            [tool["name"] for group in catalog for tool in group["tools"]],
            ["read_text"],
        )

    def test_save_rejects_unknown_tool_without_changing_definition(self):
        original = self.manifest_path.read_bytes()
        response = self.client.put('/api/agents/general-agent', json={
            'manifest_yaml':self.manifest_path.read_text(encoding='utf8')+'tools: [nonexistent_tool]\n',
            'instructions':'valid instructions',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.manifest_path.read_bytes(), original)

    def test_default_catalog_marks_initial_and_on_demand_tools(self) -> None:
        self.client.application.config["AGENT_REGISTRY"] = AgentRegistry()
        response = self.client.get("/api/agents/general-agent/tools")
        data = response.get_json()["data"]
        tools = {
            tool["name"]: tool for group in data["catalog"] for tool in group["tools"]
        }
        self.assertEqual(len(tools), 22)
        self.assertEqual(
            sum(tool["loading"] == "default" for tool in tools.values()), 9
        )
        self.assertEqual(tools["edit_document"]["loading"], "on_demand")
        self.assertEqual(tools["run_process"]["risk"], "command")

    def test_skill_detail_exposes_the_current_markdown_body_and_toolsets(self) -> None:
        body = "# Office\n\n使用 read_document 读取文档。\n"
        self.skill_path.write_text(
            "---\nname: demo-skill\ndescription: demo\ntoolsets: [office]\n---\n\n"
            + body,
            encoding="utf-8",
        )
        response = self.client.get("/api/skills/demo-skill")
        data = response.get_json()["data"]
        self.assertEqual(data["body"], body.strip())
        self.assertEqual(data["toolsets"], ["office"])

    def test_invalid_agent_yaml_returns_422_without_changing_source(self) -> None:
        original = self.manifest_path.read_bytes()
        response = self.client.put(
            "/api/agents/general-agent",
            json={
                "manifest_yaml": "id: general-agent\nname: [broken\n",
                "instructions": "仍然有效",
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.manifest_path.read_bytes(), original)
        self.assertFalse(self.manifest_path.with_suffix(".yaml.bak").exists())

    def test_agent_save_is_validated_atomic_backed_up_and_audited(self) -> None:
        original = self.manifest_path.read_bytes()
        response = self.client.put(
            "/api/agents/general-agent",
            json={
                "manifest": {
                    "id": "general-agent",
                    "name": "财务助手",
                    "version": "1.1.0",
                    "enabled": True,
                    "toolsets": ["workbench"],
                    "skills": ["demo-skill"],
                    "max_turns": 40,
                },
                "instructions": "# 新指令\n\n先核验再回答。\n",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.manifest_path.with_suffix(".yaml.bak").read_bytes(),
            original,
        )
        self.assertEqual(response.get_json()["data"]["agent"]["name"], "财务助手")
        records = [
            json.loads(line)
            for line in (self.root / "definition-audit.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(records[-1]["definition_id"], "general-agent")

    def test_installed_skill_cannot_be_modified_with_invalid_markdown(self) -> None:
        original = self.skill_path.read_bytes()
        response = self.client.put(
            "/api/skills/demo-skill",
            json={
                "content": "# 缺少 frontmatter",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.skill_path.read_bytes(), original)

    def test_installed_skill_cannot_be_modified_even_with_valid_markdown(self) -> None:
        original = self.skill_path.read_bytes()
        content = (
            "---\nname: demo-skill\ndescription: 更新后的演示 Skill\n---\n\n"
            "## 职责\n\n更新。\n\n## 约束\n\n保持准确。\n"
        )
        response = self.client.put("/api/skills/demo-skill", json={"content": content})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.skill_path.read_bytes(), original)
        self.assertFalse(self.skill_path.with_suffix('.md.bak').exists())


if __name__ == "__main__":
    unittest.main()
