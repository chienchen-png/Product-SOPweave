from __future__ import annotations
import sqlite3
import tempfile
from pathlib import Path
from contextlib import closing
from services.skills.library import SkillLibrary
from tests.agent_runtime.test_skill_contracts import sample_spec


class SkillTestFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="财务 SOP ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "app.db"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript(Path("storage/schema.sql").read_text(encoding="utf-8"))
        self.library = SkillLibrary(self.db_path, data_dir=self.root)

    def ready(self, *, base=None, title="月度核对"):
        draft = self.library.create_draft(
            base_skill_id=base.skill_id if base else None,
            base_version=base.version if base else None,
        )
        return self.library.update_draft(
            draft["draft_id"],
            expected_revision=draft["draft_revision"],
            spec=sample_spec(title=title).model_dump(),
        )

    def publish(self, draft, key="one"):
        return self.library.publish(
            draft["draft_id"],
            expected_revision=draft["draft_revision"],
            confirmed_digest=draft["generated_digest"],
            idempotency_key=key,
        )
