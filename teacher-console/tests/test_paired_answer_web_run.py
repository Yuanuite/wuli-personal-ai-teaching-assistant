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
    def test_failed_retry_can_restore_last_completed_artifact(self):
        with tempfile.TemporaryDirectory() as temp_name:
            artifacts = Path(temp_name)
            (artifacts / "web-candidate.md").write_text("成功候选", encoding="utf-8")
            (artifacts / "web-candidate.meta.json").write_text(
                json.dumps({"status": "completed"}),
                encoding="utf-8",
            )
            (artifacts / "web-candidate.evidence.json").write_text(
                json.dumps({"status": "ready"}),
                encoding="utf-8",
            )

            snapshot = web_run.successful_artifact_snapshot(
                artifacts,
                "web-candidate",
            )
            (artifacts / "web-candidate.md").write_text("失败尝试", encoding="utf-8")
            (artifacts / "web-candidate.meta.json").write_text(
                json.dumps({"status": "failed"}),
                encoding="utf-8",
            )
            web_run.restore_artifact_snapshot(artifacts, snapshot)

            self.assertEqual(
                (artifacts / "web-candidate.md").read_text(encoding="utf-8"),
                "成功候选",
            )
            self.assertEqual(
                json.loads((artifacts / "web-candidate.meta.json").read_text(encoding="utf-8"))["status"],
                "completed",
            )

    def test_cli_accepts_fixed_routing_and_model(self):
        original = web_run.sys.argv
        web_run.sys.argv = [
            "paired_answer_web_run.py",
            "--experiment",
            "/tmp/experiment",
            "--routing-tier",
            "expert",
            "--model-id",
            "fixed-model",
        ]
        try:
            args = web_run.parse_args()
        finally:
            web_run.sys.argv = original
        self.assertEqual(args.routing_tier, "expert")
        self.assertEqual(args.model_id, "fixed-model")

    def test_candidate_snapshot_uses_precision_gated_selection(self):
        original = web_run.teacher_server.agent_evidence_payload
        observed = {}

        def fake(*_args, **kwargs):
            observed.update(kwargs)
            return {"status": "ready", "references": []}

        web_run.teacher_server.agent_evidence_payload = fake
        try:
            snapshot = web_run.fixed_evidence_snapshot(
                Path("/tmp/entries/entry-1"),
                "candidate",
            )
        finally:
            web_run.teacher_server.agent_evidence_payload = original

        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(
            observed["evidence_selection_policy"],
            "evidence-set-v2",
        )

    def test_disabled_evidence_snapshot_contains_no_historical_reference(self):
        original = web_run.teacher_server.agent_evidence_payload
        web_run.teacher_server.agent_evidence_payload = lambda *_args, **_kwargs: {
            "schema_version": 1,
            "kind": "agent-evidence",
            "task_type": "analysis.generate",
            "status": "ready",
            "references": [{"reference": "similar-1", "title": "历史题"}],
            "instructions": ["历史证据只能辅助核对。"],
            "context_budget": {
                "candidate_reference_count": 1,
                "included_reference_count": 1,
                "omitted_reference_count": 0,
                "serialized_chars": 500,
            },
        }
        try:
            snapshot = web_run.fixed_evidence_snapshot(
                Path("/tmp/entries/entry-1"),
                "disabled",
            )
        finally:
            web_run.teacher_server.agent_evidence_payload = original

        self.assertEqual(snapshot["status"], "disabled-for-benchmark")
        self.assertEqual(snapshot["references"], [])
        self.assertEqual(snapshot["context_budget"]["included_reference_count"], 0)
        self.assertEqual(snapshot["context_budget"]["omitted_reference_count"], 1)
        self.assertNotIn("历史题", json.dumps(snapshot, ensure_ascii=False))

    def test_prepare_workspace_isolated_and_keeps_rag_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            source = root / "source"
            entry = source / "entries" / "entry-1"
            entry.mkdir(parents=True)
            (entry / "problem.md").write_text("# 题目\n测试", encoding="utf-8")
            (entry / "student-solution.md").write_text("教师稿", encoding="utf-8")
            (entry / "record.json").write_text(
                json.dumps({
                    "id": "entry-1",
                    "status": "ready",
                    "answer_status": "approved",
                    "source": {"stored_files": []},
                    "answer_review": {"status": "passed"},
                }),
                encoding="utf-8",
            )
            baseline = entry / ".agent-baseline"
            baseline.mkdir()
            (baseline / "student-solution.md").write_text("旧基线", encoding="utf-8")
            (entry / "analysis-request.json").write_text("{}", encoding="utf-8")
            (source / "config").mkdir()
            (source / "config.json").write_text(
                json.dumps({
                    "privacy": {"allow_remote_agent": True},
                }),
                encoding="utf-8",
            )
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
