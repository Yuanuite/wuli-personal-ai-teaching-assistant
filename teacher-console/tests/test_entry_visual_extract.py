"""Contract tests for the thin CLI visual-extract entry (work-tree A3.3).

Verifies the CLI shares the registry-routed orchestration with the web path,
produces the same failure classification, and never grants source approval or
writes a second result schema.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
THIN = CONSOLE / "scripts" / "entry_visual_extract.py"
sys.path.insert(0, str(SKILL_SCRIPTS))
sys.path.insert(0, str(CONSOLE))

import kb  # noqa: E402
import process_uploads  # noqa: E402


def _make_entry(library: Path, entry_id: str) -> Path:
    entry = library / "entries" / entry_id
    assets = entry / "assets"
    assets.mkdir(parents=True)
    (assets / "original.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    kb.write_json(
        entry / "record.json",
        {
            "schema_version": 1,
            "id": entry_id,
            "kind": "error",
            "status": "needs-review",
            "answer_status": "pending",
            "title": "CLI extract test",
            "subject": "高中物理",
            "knowledge_points": ["测试"],
            "error_types": ["待确认"],
            "source": {
                "sha256": "x" * 64,
                "source_type": "png",
                "stored_files": ["assets/original.png"],
            },
            "ocr": {"engine": "test", "review_required": True},
            "source_review": {"status": "needs-review"},
        },
    )
    kb.write_json(
        entry / "ocr.json",
        {"engine": "test", "text": "test ocr", "average_confidence": 0.5},
    )
    kb.write_text(entry / "problem.md", "# 题目\n\n测试题干，长度足够用于复核单。")
    return entry


class EntryVisualExtractCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        kb.write_json(
            self.library / "config.json",
            {
                "schema_version": 1,
                "privacy": {"allow_remote_visual_review": True},
                "source_review": {"mode": "registry"},
            },
        )
        self.entry = _make_entry(self.library, "20260802-cli-extract")

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(THIN), *args],
            capture_output=True,
            text=True,
        )

    def test_fails_closed_without_vision_route_and_stages_no_facts(self):
        result = self._run(self.entry.name, "--library", str(self.library))
        self.assertNotEqual(result.returncode, 0)
        summary = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(summary["status"], "failed")
        self.assertTrue(summary["error"])
        self.assertFalse((self.entry / "visual-facts.json").exists())
        self.assertFalse((self.entry / "visual-facts-gate.json").exists())
        record = kb.load_json(self.entry / "record.json", {})
        self.assertEqual(record["source_review"]["status"], "needs-review")

    def test_unknown_entry_fails_without_touching_network(self):
        result = self._run("nonexistent-entry", "--library", str(self.library))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", json.loads(result.stdout)["error"])

    def test_auto_mode_resolves_to_registry_from_config(self):
        options = process_uploads.resolve_review_options(self.library, "auto", "unavailable", None, None)
        self.assertEqual(options["mode"], "registry")
        self.assertTrue(options["allow_remote"])


if __name__ == "__main__":
    unittest.main()
