import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_runtime.tools.core import ListFiles, ReadText, SearchText, list_files, read_text, search_text


class CorePaginationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.context = SimpleNamespace(work_dir=self.root)

    def test_directory_pages_match_disk_without_recursing(self):
        (self.root / "folder").mkdir()
        (self.root / "folder" / "nested.txt").write_text("nested", encoding="utf-8")
        for i in range(305):
            (self.root / f"file-{i:03}.txt").write_text(str(i), encoding="utf-8")
        entries = []
        offset = 0
        while True:
            page = list_files(self.context, ListFiles(limit=70, offset=offset))
            entries.extend(item["path"] for item in page["entries"])
            self.assertGreater(page["next_offset"], offset)
            offset = page["next_offset"]
            if not page["truncated"]:
                break
        self.assertEqual(len(entries), 306)
        self.assertEqual(len(set(entries)), 306)
        self.assertEqual(set(entries), {p.name for p in self.root.iterdir()})
        self.assertFalse(any("nested.txt" in entry for entry in entries))

    def test_listing_and_search_use_the_same_canonical_workspace(self):
        (self.root / "alias").mkdir()
        (self.root / "source.txt").write_text("canonical workspace", encoding="utf-8")
        context = SimpleNamespace(work_dir=self.root / "alias" / "..")
        page = list_files(context, ListFiles())
        self.assertEqual({item["path"] for item in page["entries"]}, {"alias", "source.txt"})
        found = search_text(context, SearchText(text="canonical"))
        self.assertEqual([item["path"] for item in found["matches"]], ["source.txt"])

    def test_line_and_long_line_continuations_reconstruct_exact_source(self):
        source = "首行\n" + "长" * 45000 + "\n第三行\n末行"
        (self.root / "notes.txt").write_text(source, encoding="utf-8")
        line, offset, parts, versions = 1, 0, [], set()
        for _ in range(10):
            page = read_text(self.context, ReadText(path="notes.txt", start_line=line, character_offset=offset, line_count=2))
            self.assertLessEqual(len(page["content"]), 20000)
            parts.append(page["content"])
            versions.add(page["version"])
            if not page["truncated"]:
                break
            line, offset = page["next_line"], page["next_character_offset"]
        else:
            self.fail("continuation did not terminate")
        self.assertEqual("".join(parts), source)
        self.assertEqual(len(versions), 1)

    def test_search_limits_matches_and_preserves_chinese_line_coordinates(self):
        (self.root / "source.txt").write_text("标题\n" + "中文哨兵\n" * 20, encoding="utf-8")
        (self.root / "excluded.csv").write_text("中文哨兵", encoding="utf-8")
        page = search_text(self.context, SearchText(text="中文哨兵", glob="*.txt", limit=3))
        self.assertTrue(page["truncated"])
        self.assertEqual([(m["path"],m["line"]) for m in page["matches"]], [("source.txt",2),("source.txt",3),("source.txt",4)])
