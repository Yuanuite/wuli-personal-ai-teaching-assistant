import importlib.util
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
    "slow_loop_report", ROOT / "teacher-console" / "scripts" / "slow_loop_report.py"
)
slow_loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(slow_loop)


class SlowLoopReportTest(unittest.TestCase):
    def minimal(self):
        return slow_loop.analyze(
            {"operational": {}, "teaching_outcomes": {}},
            {
                "fixed_set_ready": False,
                "upgrade_recommended": False,
                "validation": {"status_counts": {}},
                "overall": {"recall": {"@5": None}},
                "by_category": {},
            },
            {"kinds": {}},
        )

    def test_insufficient_evidence_never_changes_policy(self):
        report = self.minimal()
        self.assertFalse(report["readiness"]["weekly_report_ready"])
        self.assertFalse(report["readiness"]["policy_change_ready"])
        self.assertFalse(report["readiness"]["auto_apply_ready"])
        self.assertFalse(report["safety"]["mutates_policy"])
        self.assertTrue(report["blockers"])

    def test_ready_evidence_produces_recommendations_but_not_apply(self):
        teaching = {}
        for cohort, approval in (("retrieved", 0.8), ("legacy-no-rag", 0.5)):
            teaching[f"answer.revise:{cohort}"] = {
                "task_type": "answer.revise",
                "cohort": cohort,
                "count": 10,
                "pending": 0,
                "teacher_closed": 10,
                "teaching_batch_count": 2,
                "teacher_approval_rate": approval,
            }
        report = slow_loop.analyze(
            {
                "operational": {"answer.revise:retrieved": {"cohort": "retrieved", "completed": 20}},
                "teaching_outcomes": teaching,
            },
            {
                "fixed_set_ready": True,
                "upgrade_recommended": True,
                "validation": {"status_counts": {"approved": 30}},
                "overall": {"recall": {"@5": 0.8}},
                "by_category": {"teacher_phrase": {"hit_rate": {"@5": 0.7}}},
            },
            {"kinds": {"source.clean": {"count": 10, "run": {"p90_seconds": 90}, "failure_types": {}}}},
        )
        self.assertTrue(report["readiness"]["weekly_report_ready"])
        self.assertTrue(report["readiness"]["strategy_recommendation_ready"])
        self.assertIn("retrieval.tag-weighting-review", report["recommendation_codes"])
        self.assertTrue(all(not item["apply"] for item in report["recommendations"]))
        self.assertFalse(report["readiness"]["policy_change_ready"])

    def test_record_requires_weekly_gate_and_enters_knowledge_store(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / "library"
            kb.init_library(library)
            with self.assertRaises(ValueError):
                slow_loop.record_report(library, self.minimal())
            report = self.minimal()
            report["readiness"]["weekly_report_ready"] = True
            event = slow_loop.record_report(library, report)
            self.assertEqual(event["task_type"], "evolve.observation.slow-loop")
            rebuilt = knowledge_store.rebuild(library)
            self.assertEqual(rebuilt["evolve_observations"], 1)

    def test_failure_samples_open_separate_reliability_observation_gate(self):
        report = slow_loop.analyze(
            {"operational": {}, "teaching_outcomes": {}},
            {
                "fixed_set_ready": False,
                "upgrade_recommended": False,
                "validation": {"status_counts": {}},
                "overall": {"recall": {"@5": None}},
                "by_category": {},
            },
            {
                "kinds": {
                    "visualization.model": {
                        "count": 5,
                        "completed": 1,
                        "failed": 4,
                        "failure_types": {"candidate_validation_failed": 4},
                    }
                }
            },
        )
        self.assertFalse(report["readiness"]["weekly_report_ready"])
        self.assertTrue(report["readiness"]["reliability_observation_ready"])
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / "library"
            kb.init_library(library)
            event = slow_loop.record_report(library, report)
            self.assertEqual(event["task_type"], "evolve.observation.slow-loop")

    def test_teacher_confirmation_is_bound_to_latest_recorded_report(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / "library"
            kb.init_library(library)
            with self.assertRaises(ValueError):
                slow_loop.confirm_strategy(library, reviewer="teacher")
            report = self.minimal()
            report["readiness"]["weekly_report_ready"] = True
            report["strategy_recommendation_codes"] = ["retrieval.tag-weighting-review"]
            slow_loop.record_report(library, report)
            confirmation = slow_loop.confirm_strategy(library, reviewer="李老师", note="同意离线试验")
            self.assertEqual(confirmation["task_type"], "evolve.strategy.confirm")
            self.assertTrue(slow_loop._teacher_strategy_confirmed(library))
            second = dict(report)
            second["generated_at"] = "later"
            slow_loop.record_report(library, second)
            self.assertFalse(slow_loop._teacher_strategy_confirmed(library))


if __name__ == "__main__":
    unittest.main()
