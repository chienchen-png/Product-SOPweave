import io
import unittest
from unittest.mock import patch
import json
import stat
from zipfile import ZipInfo
from zipfile import ZipFile, ZIP_DEFLATED
from services.skills.imports import SkillImporter
from services.skills.contracts import SkillProblem
from tests.agent_runtime.skill_fixtures import SkillTestFixture


def bundle(files):
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for path, body in files.items():
            archive.writestr(path, body)
    return stream.getvalue()


class SkillImportTests(SkillTestFixture, unittest.TestCase):
    def test_pin_public_github_commit(self):
        commit = "a" * 40
        content = b"---\nname: pinned\ndescription: pinned\nallowed-tools: read_text\n---\nRead input.txt."
        calls = []

        def fetch(url, _deadline, **_kwargs):
            calls.append(url)
            if "/commits/" in url:
                return json.dumps({"sha": commit}).encode()
            if "/git/trees/" in url:
                return json.dumps(
                    {
                        "tree": [
                            {
                                "type": "blob",
                                "path": "sop/SKILL.md",
                                "size": len(content),
                            }
                        ]
                    }
                ).encode()
            return content

        with patch.object(SkillImporter, "_fetch", side_effect=fetch):
            result = SkillImporter(self.library).preview_github(
                "https://github.com/example/skills/tree/main/sop"
            )
        self.assertEqual(result["commit"], commit)
        self.assertTrue(any("/" + commit + "/sop/SKILL.md" in url for url in calls))
        source = next(s for s in result["draft"]["source"] if s["kind"] == "github")
        self.assertEqual(source["commit"], commit)

    def test_bad_skill_document_and_symlink_are_explained(self):
        for content in [b"not a skill", b"\xff\xfe\x00"]:
            with self.assertRaises(SkillProblem):
                SkillImporter(self.library).preview_zip(bundle({"SKILL.md": content}))
        stream = io.BytesIO()
        with ZipFile(stream, "w") as archive:
            link = ZipInfo("references/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../../outside")
        with self.assertRaises(SkillProblem):
            SkillImporter(self.library).preview_zip(stream.getvalue())

    def test_preserve_original_markdown_and_show_adaptation_diff(self):
        content = "---\nname: simple-sop\ndescription: 读取文本\nallowed-tools: read_text\n---\n读取输入文件，说明差异。\n"
        preview = SkillImporter(self.library).preview_zip(
            bundle(
                {
                    "simple/SKILL.md": content,
                    "simple/references/rule.md": "金额必须准确",
                }
            )
        )
        self.assertEqual(preview["original_content"], content)
        self.assertIn("simple-sop", preview["diff"])
        draft = preview["draft"]
        ref = self.publish(draft)
        detail = self.library.get(ref.skill_id)
        path = self.root / detail["package_relative_path"] / "references/rule.md"
        self.assertEqual(path.read_text(encoding="utf-8"), "金额必须准确")
        edit = self.library.create_draft(base_skill_id=ref.skill_id, base_version=1)
        edit = self.library.update_draft(
            edit["draft_id"],
            expected_revision=edit["draft_revision"],
            spec=edit["spec"],
        )
        revised = self.publish(edit, key="edit")
        self.assertEqual(revised.version, 2)
        updated = self.library.get(ref.skill_id)
        self.assertEqual(
            (
                self.root / updated["package_relative_path"] / "references/rule.md"
            ).read_text(encoding="utf-8"),
            "金额必须准确",
        )

    def test_unknown_tool_is_not_granted(self):
        content = "---\nname: foreign\ndescription: foreign\nallowed-tools: Bash\n---\nRun external scripts."
        preview = SkillImporter(self.library).preview_zip(bundle({"SKILL.md": content}))
        self.assertIn("Bash", preview["unsupported_tools"])
        self.assertTrue(preview["draft"]["spec"]["blockers"])
        with self.assertRaises(SkillProblem):
            self.publish(preview["draft"])
        compatible = "---\nname: local\ndescription: local\nallowed-tools: read_text\n---\nRead text."
        nested = SkillImporter(self.library).preview_zip(
            bundle(
                {
                    "SKILL.md": compatible,
                    "references/helper.py": 'print("must not run")',
                }
            )
        )
        self.assertTrue(nested["requires_adaptation"])
        with self.assertRaises(SkillProblem):
            self.publish(nested["draft"], key="nested")

    def test_choose_one_skill_from_multi_skill_repo(self):
        content = "---\nname: simple\ndescription: simple\n---\n流程"
        preview = SkillImporter(self.library).preview_zip(
            bundle({"a/SKILL.md": content, "b/SKILL.md": content})
        )
        self.assertEqual(preview["choices"], ["a", "b"])
        self.assertNotIn("draft", preview)
        multi = bundle({"SKILL.md": content, "a/SKILL.md": content})
        self.assertEqual(
            SkillImporter(self.library).preview_zip(multi)["choices"], [".", "a"]
        )
        self.assertIn(
            "draft", SkillImporter(self.library).preview_zip(multi, skill_path=".")
        )

    def test_zip_slip_ads_collision_and_bomb_rejected(self):
        for files in [
            {"../x": "bad"},
            {"a/./x": "bad"},
            {"a//x": "bad"},
            {"a:secret": "bad"},
            {"CON.txt": "bad"},
            {"a/x": "1", "A/X": "2"},
            {"big.txt": "x" * 100000},
        ]:
            with self.subTest(files=list(files)), self.assertRaises(SkillProblem):
                SkillImporter(self.library).preview_zip(bundle(files))
