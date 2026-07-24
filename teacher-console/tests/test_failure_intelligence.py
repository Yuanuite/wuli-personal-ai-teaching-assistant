import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

from failure_intelligence import (  # noqa: E402
    build_failure_evidence,
    repair_decision,
    run_with_failure_repair,
    task_with_repair_evidence,
)


class FailureIntelligenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        (self.library / "indexes").mkdir(parents=True)
        self.task = {
            "id": "task-1",
            "kind": "answer.revise",
            "prompt": "revise answer",
            "allowed_paths": ["solution.md"],
            "denied_paths": ["record.json"],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_policy_only_retries_candidate_shape_failures(self):
        for code in ("candidate_validation_failed", "output_truncated", "candidate_no_change"):
            self.assertTrue(repair_decision(code)["auto_retry"], code)
        for code in ("unauthorized_change", "canonical_changed", "provider_timeout", "provider_unavailable"):
            self.assertFalse(repair_decision(code)["auto_retry"], code)

    def test_evidence_selects_matching_history_and_removes_identifiers(self):
        events = [
            {
                "entry_id": "20260720-secret-entry",
                "task_type": "answer.revise",
                "status": "failed",
                "summary": "failed at /Users/teacher/private/solution.md",
                "failure_reasons": ["JSON truncated for aabbccddeeff00112233445566778899 with sk-secretvalue123"],
                "result": {"failure_type": "output_truncated", "validation_errors": ["truncated output"]},
            },
            {
                "entry_id": "other",
                "task_type": "visualization.model",
                "status": "failed",
                "result": {"failure_type": "output_truncated"},
            },
        ]
        archive = self.library / "indexes" / "candidate-archive.jsonl"
        archive.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")
        evidence = build_failure_evidence(
            self.library,
            "answer.revise",
            {"failure_type": "output_truncated", "validation_errors": ["current truncated"]},
        )
        serialized = json.dumps(evidence, ensure_ascii=False)
        self.assertEqual(evidence["reference_count"], 1)
        self.assertNotIn("20260720-secret-entry", serialized)
        self.assertNotIn("/Users/teacher", serialized)
        self.assertNotIn("aabbccddeeff00112233445566778899", serialized)
        self.assertNotIn("sk-secretvalue123", serialized)

    def test_repair_task_preserves_boundaries_and_adds_context(self):
        evidence = build_failure_evidence(
            self.library,
            "answer.revise",
            {"failure_type": "candidate_validation_failed", "validation_errors": ["missing student layer"]},
        )
        repaired = task_with_repair_evidence(self.task, evidence)
        self.assertEqual(repaired["allowed_paths"], ["solution.md"])
        self.assertEqual(repaired["denied_paths"], ["record.json"])
        self.assertIn("failure-evidence.json", " ".join(repaired["context_payloads"]))
        self.assertIn("missing student layer", repaired["prompt"])
        self.assertEqual(self.task["prompt"], "revise answer")

    def test_one_retry_can_recover_and_combines_attempts(self):
        calls = []

        def run_once(task, _validator):
            calls.append(task)
            if len(calls) == 1:
                return {
                    "status": "failed",
                    "failure_type": "candidate_validation_failed",
                    "validation_errors": ["missing answer"],
                    "attempts": [{"provider": "first"}],
                }
            self.assertIn("failure-evidence.json", " ".join(task["context_payloads"]))
            return {"status": "completed", "attempts": [{"provider": "second"}]}

        result = run_with_failure_repair(
            self.task,
            None,
            library=self.library,
            run_once=run_once,
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["failure_repair"]["status"], "recovered")
        self.assertEqual(len(result["attempts"]), 2)

    def test_material_provider_spend_disables_full_automatic_retry(self):
        calls = []

        def run_once(_task, _validator):
            calls.append(True)
            return {
                "status": "failed",
                "failure_type": "candidate_validation_failed",
                "validation_errors": ["content still needs review"],
                "attempts": [
                    {
                        "provider": "claude",
                        "duration_seconds": 120,
                        "failure_type": "candidate_validation_failed",
                    }
                ],
            }

        result = run_with_failure_repair(
            self.task,
            None,
            library=self.library,
            run_once=run_once,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["failure_repair"]["status"], "not-retried-budget-protected")
        self.assertEqual(result["failure_repair"]["policy"], "budget-protected-no-retry")
        self.assertIn("120.0 秒", result["failure_repair"]["action"])

    def test_unauthorized_change_is_never_retried(self):
        calls = []

        def run_once(_task, _validator):
            calls.append(True)
            return {"status": "failed", "failure_type": "unauthorized_change"}

        result = run_with_failure_repair(
            self.task,
            None,
            library=self.library,
            run_once=run_once,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["failure_repair"]["status"], "not-retried")
        self.assertEqual(result["failure_repair"]["policy"], "blocked-by-safety-boundary")


if __name__ == "__main__":
    unittest.main()
