import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import correctness_faults  # noqa: E402
import correctness_replay  # noqa: E402

FAULTS = ROOT / "teacher-console" / "tests" / "fixtures" / "correctness_faults.v1.json"


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class CorrectnessReplayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.library = root / "library"
        self.experiment = self.library / "evals" / "replay"
        self.entry_id = "entry-1"
        entry = self.library / "entries" / self.entry_id
        entry.mkdir(parents=True)
        answer = entry / "student-solution.md"
        answer.write_text("# reviewed answer\n", encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(answer.read_bytes()).hexdigest()
        blueprint = {
            "status": "completed",
            "question_targets": [{"id": "q1", "prompt": "求结果", "answer_type": "value"}],
            "physical_stages": [{"id": "p1"}],
            "verification_obligations": [
                {
                    "id": "v1",
                    "target_id": "q1",
                    "check": "核对结果",
                    "risk": "medium",
                }
            ],
        }
        solver = {
            "status": "completed",
            "message": "ok",
            "targets": [
                {
                    "id": "q1",
                    "final_answer": "6",
                    "supporting_relations": ["2×3=6"],
                    "conditions": [],
                    "covered_obligation_ids": ["v1"],
                }
            ],
            "stage_results": [],
            "option_verdicts": [],
            "blueprint_audit": {
                "status": "followed",
                "covered_target_ids": ["q1"],
                "covered_obligation_ids": ["v1"],
                "revisions": [],
            },
        }
        write_json(
            entry / "w3-shadow-report.json",
            {
                "status": "completed",
                "report": {"blueprint": blueprint, "solver_a": solver},
            },
        )
        write_json(
            self.experiment / "manifest.json",
            {
                "schema_version": 2,
                "experiment_id": "replay",
                "cases": [
                    {
                        "entry_id": self.entry_id,
                        "evaluation_split": "replay",
                        "reference_digest": digest,
                    }
                ],
            },
        )
        write_json(
            self.experiment / "truth-lock.json",
            {
                "case_count": 1,
                "target_count": 1,
                "cases": [{"entry_id": self.entry_id, "target_count": 1}],
            },
        )
        write_json(self.experiment / "result.json", {"gates": {"production_eligible": False}})
        write_json(
            self.experiment / "paired-result.json",
            {
                "production_evidence": False,
                "errors": [],
                "pairs": [
                    {
                        "entry_id": self.entry_id,
                        "w2_correct_target_count": 0,
                        "w3_correct_target_count": 1,
                    }
                ],
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_replay_projects_but_never_invents_certificates(self):
        report = correctness_replay.diagnose_replay(
            self.library,
            self.experiment,
            correctness_faults.load_fault_cases(FAULTS),
            generated_at="2026-07-29T00:00:00+00:00",
        )
        self.assertEqual(report["metrics"]["projected_case_count"], 1)
        self.assertEqual(report["metrics"]["provisional_without_certificate_count"], 1)
        self.assertEqual(report["metrics"]["fault_detection_rate"], 1.0)
        self.assertEqual(report["metrics"]["false_promotion_count"], 0)
        self.assertTrue(report["gates"]["canonical_unchanged"])
        self.assertFalse(report["gates"]["production_authorized"])

    def test_changed_reference_digest_is_reported_not_silently_scored(self):
        answer = self.library / "entries" / self.entry_id / "student-solution.md"
        answer.write_text("# changed\n", encoding="utf-8")
        report = correctness_replay.diagnose_replay(
            self.library,
            self.experiment,
            correctness_faults.load_fault_cases(FAULTS),
        )
        self.assertEqual(report["metrics"]["reference_digest_mismatch_count"], 1)
        self.assertTrue(report["readiness"]["reference_refresh_required"])
        self.assertFalse(report["readiness"]["historical_scores_reusable_without_refresh"])

    def test_markdown_states_replay_limit(self):
        report = correctness_replay.diagnose_replay(
            self.library,
            self.experiment,
            correctness_faults.load_fault_cases(FAULTS),
        )
        markdown = correctness_replay.render_markdown(report)
        self.assertIn("无证书时 PROVISIONAL", markdown)
        self.assertIn("不能替代 fresh holdout", markdown)


if __name__ == "__main__":
    unittest.main()
