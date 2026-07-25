import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "teacher-console" / "scripts" / "paired_answer_web_run.py"
SPEC = importlib.util.spec_from_file_location("paired_answer_web_run_test", SCRIPT)
assert SPEC is not None
web_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(web_run)


class PairedAnswerWebRunTest(unittest.TestCase):
    def test_prepare_workspace_isolated_and_keeps_rag_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "source"
            entry = source / "entries" / "entry-1"
            entry.mkdir(parents=True)
            (entry / "problem.md").write_text("# 题目\n测试", encoding="utf-8")
            (entry / "student-solution.md").write_text("教师稿", encoding="utf-8")
            (entry / "record.json").write_text(json.dumps({
                "id": "entry-1",
                "status": "ready",
                "answer_status": "approved",
                "source": {"stored_files": []},
                "answer_review": {"status": "passed"},
            }), encoding="utf-8")
            baseline = entry / ".agent-baseline"
            baseline.mkdir()
            (baseline / "student-solution.md").write_text("旧基线", encoding="utf-8")
            (entry / "analysis-request.json").write_text("{}", encoding="utf-8")
            (source / "config").mkdir()
            (source / "config.json").write_text(json.dumps({
                "privacy": {"allow_remote_agent": True},
            }), encoding="utf-8")
            (source / "config" / "model-registry.json").write_text(
                json.dumps({"schema_version": 1, "models": [], "defaults": {}}),
                encoding="utf-8",
            )
            database = source / "indexes" / "wuli-memory.db"
            database.parent.mkdir()
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE marker(value TEXT)")
                connection.execute("INSERT INTO marker VALUES ('current')")

            workspace = root / "workspace"
            library = web_run.prepare_workspace(source, workspace, "entry-1")
            isolated = library / "entries" / "entry-1"

            self.assertFalse((isolated / "student-solution.md").exists())
            self.assertFalse((isolated / ".agent-baseline").exists())
            self.assertFalse((isolated / "analysis-request.json").exists())
            self.assertEqual(
                json.loads((isolated / "record.json").read_text())["answer_status"],
                "pending",
            )
            with sqlite3.connect(library / "indexes" / "wuli-memory.db") as connection:
                self.assertEqual(connection.execute("SELECT value FROM marker").fetchone()[0], "current")

            (isolated / "student-solution.md").write_text("网页候选", encoding="utf-8")
            self.assertEqual((entry / "student-solution.md").read_text(), "教师稿")


if __name__ == "__main__":
    unittest.main()
