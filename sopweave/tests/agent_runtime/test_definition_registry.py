from __future__ import annotations

import tempfile
import time
import unittest
import shutil
from pathlib import Path

from flask import Flask

from agent_runtime.definitions.agent_registry import (
    AgentNotFoundError,
    AgentRegistry,
    DefinitionValidationError,
)
from routes.agents import agents_bp


class AgentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profiles = self.root / "agent_profiles"
        self.skills = self.root / "skills"
        (self.skills / "data-reconcile").mkdir(parents=True)
        self.registry = AgentRegistry(
            self.profiles,
            skill_roots=[self.skills],
            known_toolsets={"workbench"},
        )

    def test_valid_definition_has_a_stable_id_and_summary(self) -> None:
        _write_profile(self.profiles, "general-agent")

        definition = self.registry.get("general-agent")
        summaries = self.registry.list_enabled()

        self.assertEqual(definition.manifest.id, "general-agent")
        self.assertEqual(definition.instructions, "You are the general agent.\n")
        self.assertEqual([item.id for item in summaries], ["general-agent"])
        self.assertEqual(summaries[0].name, "General agent")

    def test_unknown_agent_raises_a_typed_error(self) -> None:
        with self.assertRaises(AgentNotFoundError):
            self.registry.get("missing-agent")

    def test_duplicate_ids_are_rejected_before_directory_aliases_can_hide_them(self) -> None:
        _write_profile(self.profiles, "first", agent_id="duplicate")
        _write_profile(self.profiles, "second", agent_id="duplicate")

        _write_profile(self.profiles, "valid-agent")
        self.assertEqual([s.id for s in self.registry.list_enabled()], ["valid-agent"])
        issues = self.registry.diagnostics()
        self.assertEqual({i['agent_id'] for i in issues}, {'first','second'})
        self.assertTrue(all('重复' in i['message'] and 'duplicate' in i['message'] for i in issues))

    def test_directory_and_manifest_id_must_match(self) -> None:
        _write_profile(self.profiles, "folder-name", agent_id="other-name")

        with self.assertRaisesRegex(DefinitionValidationError, "目录.*ID"):
            self.registry.get('folder-name')

    def test_invalid_profile_is_isolated_and_diagnostics_clear_after_repair(self):
        _write_profile(self.profiles, 'valid-agent')
        bad = self.profiles / 'bad-agent'
        bad.mkdir()
        (bad/'agent.yaml').write_text('id: [broken', encoding='utf8')
        (bad/'instructions.md').write_text('instructions', encoding='utf8')
        self.assertEqual(self.registry.get('valid-agent').manifest.id, 'valid-agent')
        self.assertEqual([s.id for s in self.registry.list_enabled()], ['valid-agent'])
        self.assertEqual(self.registry.diagnostics()[0]['path'], 'bad-agent/agent.yaml')
        with self.assertRaises(DefinitionValidationError):
            self.registry.get('bad-agent')
        (bad/'agent.yaml').write_text(
            (self.profiles/'valid-agent'/'agent.yaml').read_text(encoding='utf8').replace('valid-agent', 'bad-agent'),
            encoding='utf8',
        )
        self.assertEqual(len(self.registry.list_enabled()), 2)
        self.assertEqual(self.registry.diagnostics(), [])

    def test_unknown_toolset_and_skill_are_rejected(self) -> None:
        _write_profile(self.profiles, "bad-toolset", toolsets=["missing"])
        with self.assertRaisesRegex(DefinitionValidationError, "toolset.*missing"):
            self.registry.get("bad-toolset")

        shutil.rmtree(self.profiles / "bad-toolset")
        _write_profile(self.profiles, "bad-skill", skills=["missing-skill"])
        with self.assertRaisesRegex(DefinitionValidationError, "Skill.*missing-skill"):
            self.registry.get("bad-skill")

    def test_broken_yaml_is_not_replaced_with_a_default_agent(self) -> None:
        profile = self.profiles / "general-agent"
        profile.mkdir(parents=True)
        (profile / "agent.yaml").write_text("id: [broken", encoding="utf-8")
        (profile / "instructions.md").write_text("valid text", encoding="utf-8")

        with self.assertRaises(DefinitionValidationError):
            self.registry.get("general-agent")

    def test_instruction_changes_are_visible_to_the_next_registry_read(self) -> None:
        _write_profile(self.profiles, "general-agent")
        first = self.registry.get("general-agent")
        instructions = self.profiles / "general-agent" / "instructions.md"
        time.sleep(0.002)
        instructions.write_text("Updated instructions.\n", encoding="utf-8")

        second = self.registry.get("general-agent")

        self.assertEqual(first.instructions, "You are the general agent.\n")
        self.assertEqual(second.instructions, "Updated instructions.\n")

    def test_agent_api_reads_the_file_registry(self) -> None:
        _write_profile(self.profiles, "general-agent")
        app = Flask(__name__)
        app.config["AGENT_REGISTRY"] = self.registry
        app.register_blueprint(agents_bp)
        client = app.test_client()

        listing = client.get("/api/agents")
        detail = client.get("/api/agents/general-agent")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["data"]["agents"][0]["id"], "general-agent")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            detail.get_json()["data"]["agent"]["system_prompt"],
            "You are the general agent.\n",
        )


def _write_profile(
    root: Path,
    directory: str,
    *,
    agent_id: str | None = None,
    toolsets: list[str] | None = None,
    skills: list[str] | None = None,
) -> None:
    profile = root / directory
    profile.mkdir(parents=True)
    values = {
        "id": agent_id or directory,
        "name": "General agent",
        "version": "1.0.0",
        "description": "General finance assistant",
        "enabled": True,
        "default_model_id": "",
        "toolsets": toolsets if toolsets is not None else ["workbench"],
        "tools": ["read_text"],
        "skills": skills if skills is not None else ["data-reconcile"],
        "approval_policy": {"write_text": "always"},
        "max_turns": 20,
    }
    import yaml

    (profile / "agent.yaml").write_text(
        yaml.safe_dump(values, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (profile / "instructions.md").write_text(
        "You are the general agent.\n", encoding="utf-8"
    )


if __name__ == "__main__":
    unittest.main()
