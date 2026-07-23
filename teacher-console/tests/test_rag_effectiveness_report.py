import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_archive  # noqa: E402
import kb  # noqa: E402
import knowledge_store  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "rag_effectiveness_report", ROOT / "teacher-console" / "scripts" / "rag_effectiveness_report.py"
)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


class RagEffectivenessReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        self.jobs = self.library / ".cache" / "agent-jobs"
        self.jobs.mkdir(parents=True)
        self.entry = self.library / "entries" / "entry-1"
        self.entry.mkdir(parents=True)
        kb.write_json(self.entry / "record.json", {"schema_version": 1, "id": "entry-1", "title": "test"})

    def tearDown(self):
        self.temp.cleanup()

    def append(self, task_type, actor, event_type, status, *, result=None, evaluation=None):
        return candidate_archive.append_event(
            self.library,
            self.entry,
            task_type=task_type,
            actor=actor,
            event_type=event_type,
            status=status,
            result=result or {},
            evaluation=evaluation or {},
        )

    def test_observational_report_groups_rag_and_legacy_outcomes(self):
        self.append(
            "answer.revise",
            "agent",
            "agent-result",
            "completed",
            result={"evidence_context": {"status": "ready", "reference_count": 2}},
            evaluation={"scores": {"correctness": 80}},
        )
        self.append("answer.approve", "teacher", "review", "approved")
        self.append(
            "answer.revise",
            "agent",
            "agent-result",
            "failed",
            evaluation={"scores": {"correctness": 60}},
        )
        for job_id, context, status in (
            ("rag", {"status": "ready", "reference_count": 2}, "completed"),
            ("legacy", None, "failed"),
        ):
            result = {"status": status, "usage": {"total_tokens": 100}}
            if context:
                result["evidence_context"] = context
            kb.write_json(
                self.jobs / f"{job_id}.json",
                {
                    "schema_version": 1,
                    "id": job_id,
                    "kind": "answer.revise",
                    "entry_id": "entry-1",
                    "status": status,
                    "created_at": "2026-07-22T10:00:00+08:00",
                    "started_at": "2026-07-22T10:00:10+08:00",
                    "completed_at": "2026-07-22T10:01:10+08:00",
                    "result": result,
                },
            )
        report = reporter.build_report(self.library, self.jobs, min_samples=1)
        self.assertIn("retrieved", report["cohorts"])
        self.assertIn("legacy-no-rag", report["cohorts"])
        self.assertTrue(report["comparison_ready"])
        self.assertEqual(report["comparable_tasks"], ["answer.revise"])
        rag = report["teaching_outcomes"]["answer.revise:retrieved"]
        self.assertEqual(rag["teacher_approval_rate"], 1.0)
        self.assertEqual(rag["avg_evaluator_scores"]["correctness"], 80.0)

    def test_recorded_report_becomes_knowledge_store_evolve_observation(self):
        report = reporter.build_report(self.library, self.jobs, min_samples=2)
        event = reporter.record_report(self.library, report, {"min_samples": 2})
        self.assertEqual(event["task_type"], "evolve.observation.rag")
        rebuilt = knowledge_store.rebuild(self.library)
        self.assertEqual(rebuilt["evolve_observations"], 1)
        evidence = knowledge_store.query(self.library, "test", mode="audit", top_k=2)
        self.assertEqual(evidence["evolve_observations"][0]["event_id"], event["event_id"])


if __name__ == "__main__":
    unittest.main()
