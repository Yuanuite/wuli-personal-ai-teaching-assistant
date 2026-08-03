import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))
SCRIPT = ROOT / "teacher-console" / "scripts" / "ipho_closed_book_eval.py"
SPEC = importlib.util.spec_from_file_location("ipho_closed_book_eval", SCRIPT)
assert SPEC is not None
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)

from agent_gateway import AgentGateway  # noqa: E402


class IphoClosedBookEvalTest(unittest.TestCase):
    def question(self):
        return {"id": "T1", "subparts": ["A.1", "A.2"]}

    def payload(self):
        return {
            "status": "completed",
            "schema_version": "wuli.ipho.answer-sheet.v1",
            "exam_id": "ipho-2021-theory",
            "question_id": "T1",
            "subanswers": [
                {
                    "subpart_id": subpart,
                    "derivation": ["derive"],
                    "final_answer": "answer",
                    "checks": [],
                    "assumptions": [],
                    "confidence": "high",
                }
                for subpart in ("A.1", "A.2")
            ],
            "unresolved": [],
        }

    def test_contract_fixes_question_and_subpart_count(self):
        schema = module.answer_sheet_contract("T2", 13)["schema"]
        self.assertEqual(schema["properties"]["question_id"]["enum"], ["T2"])
        self.assertEqual(schema["properties"]["subanswers"]["minItems"], 13)
        self.assertEqual(schema["properties"]["subanswers"]["maxItems"], 13)
        self.assertFalse(schema["additionalProperties"])

    def test_payload_requires_exact_official_subpart_order(self):
        self.assertEqual(module.payload_errors(self.payload(), self.question()), [])
        reversed_payload = self.payload()
        reversed_payload["subanswers"].reverse()
        errors = module.payload_errors(reversed_payload, self.question())
        self.assertTrue(any("subpart order mismatch" in error for error in errors))

    def test_payload_rejects_empty_derivation_and_answer(self):
        payload = self.payload()
        payload["subanswers"][0]["derivation"] = []
        payload["subanswers"][0]["final_answer"] = ""
        errors = module.payload_errors(payload, self.question())
        self.assertIn("A.1: empty final answer", errors)
        self.assertIn("A.1: empty derivation", errors)

    def test_usage_is_exact_or_explicitly_unavailable(self):
        self.assertEqual(
            module.aggregate_usage([{"duration_seconds": 1.0}]),
            {
                "status": "unavailable",
                "reason": "provider-did-not-report-token-usage",
            },
        )
        usage = module.aggregate_usage([
            {"token_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
            {"token_usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}},
        ])
        self.assertEqual(usage["input_tokens"], 13)
        self.assertEqual(usage["output_tokens"], 7)
        self.assertEqual(usage["total_tokens"], 20)
        self.assertEqual(usage["reported_attempts"], 2)

    def test_ipho_candidate_output_is_not_denied_by_gateway_policy(self):
        self.assertEqual(
            AgentGateway._path_policy_errors(
                ["candidate-answer.json"],
                ["truth/**", "results/**"],
            ),
            [],
        )
        self.assertIn(
            "allowed path is denied by task policy: candidate-answer.json",
            AgentGateway._path_policy_errors(
                ["candidate-answer.json"],
                ["candidate-answer.json", "truth/**", "results/**"],
            ),
        )

    def test_record_token_totals_do_not_invent_missing_usage(self):
        unavailable = module.token_totals([
            {"token_usage": {"status": "unavailable"}},
            {"token_usage": None},
        ])
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["unavailable_records"], 2)
        usage = module.token_totals([
            {
                "token_usage": {
                    "status": "reported",
                    "input_tokens": 10,
                    "output_tokens": 4,
                }
            },
            {"token_usage": {"status": "unavailable"}},
        ])
        self.assertEqual(usage["input_tokens"], 10)
        self.assertEqual(usage["output_tokens"], 4)
        self.assertEqual(usage["total_tokens"], 14)
        self.assertEqual(usage["unavailable_records"], 1)

    def test_grade_payload_enforces_official_allocation_and_verdict(self):
        question = {
            "id": "T1",
            "subparts": ["A.1", "A.2"],
            "points": [0.8, 0.6],
        }
        payload = {
            "status": "completed",
            "schema_version": "wuli.ipho.answer-sheet-grade.v1",
            "exam_id": "ipho-2021-theory",
            "question_id": "T1",
            "grades": [
                {
                    "subpart_id": "A.1",
                    "awarded_points": 0.8,
                    "max_points": 0.8,
                    "verdict": "full-credit",
                    "candidate_summary": "same",
                    "reference_summary": "same",
                    "rationale": "match",
                    "error_types": [],
                    "confidence": "high",
                    "requires_teacher_review": False,
                },
                {
                    "subpart_id": "A.2",
                    "awarded_points": 0.3,
                    "max_points": 0.6,
                    "verdict": "partial-credit",
                    "candidate_summary": "partial",
                    "reference_summary": "complete",
                    "rationale": "one step missing",
                    "error_types": ["incomplete-derivation"],
                    "confidence": "high",
                    "requires_teacher_review": False,
                },
            ],
        }
        self.assertEqual(module.grade_payload_errors(payload, question), [])
        payload["grades"][1]["awarded_points"] = 0.6
        self.assertTrue(
            any(
                "partial-credit must be strictly partial" in error
                for error in module.grade_payload_errors(payload, question)
            )
        )

    def test_source_validation_checks_truth_digest_without_reading_content(self):
        with tempfile.TemporaryDirectory() as temp_name:
            experiment = Path(temp_name)
            (experiment / "source" / "problems").mkdir(parents=True)
            (experiment / "source" / "figure-facts").mkdir(parents=True)
            (experiment / "truth" / "solutions").mkdir(parents=True)
            questions = []
            for question_id in module.QUESTION_IDS:
                problem_pdf = experiment / "source" / "problems" / f"{question_id}.pdf"
                solution_pdf = experiment / "truth" / "solutions" / f"{question_id}.pdf"
                problem_pdf.write_bytes(f"{question_id}-problem".encode())
                solution_pdf.write_bytes(f"{question_id}-solution".encode())
                (experiment / "source" / "problems" / f"{question_id}-problem-en.txt").write_text(
                    "problem", encoding="utf-8"
                )
                (experiment / "source" / "figure-facts" / f"{question_id}.md").write_text("figures", encoding="utf-8")
                questions.append({
                    "id": question_id,
                    "problem_path": f"source/problems/{question_id}.pdf",
                    "problem_sha256": module.sha256_file(problem_pdf),
                    "solution_path": f"truth/solutions/{question_id}.pdf",
                    "solution_sha256": module.sha256_file(solution_pdf),
                    "subparts": ["A.1"],
                    "points": [10.0],
                })
            manifest = {
                "experiment_id": "test",
                "scope": {
                    "included": list(module.QUESTION_IDS),
                    "subpart_count": 3,
                    "maximum_points": 30.0,
                },
                "questions": questions,
            }
            (experiment / "source-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = module.validate_source(experiment)
            self.assertEqual(result["status"], "passed")

    def test_freeze_requires_one_completed_run_and_stable_candidate_digest(self):
        with tempfile.TemporaryDirectory() as temp_name:
            experiment = Path(temp_name)
            (experiment / "results").mkdir(parents=True)
            (experiment / "source-manifest.json").write_text(json.dumps({"experiment_id": "test"}), encoding="utf-8")
            for question_id in module.QUESTION_IDS:
                candidate = experiment / "work" / question_id / "candidate-answer.json"
                candidate.parent.mkdir(parents=True)
                candidate.write_text('{"status":"completed"}', encoding="utf-8")
                record = {
                    "status": "completed",
                    "candidate_path": f"work/{question_id}/candidate-answer.json",
                    "candidate_sha256": module.sha256_file(candidate),
                    "started_at": "start",
                    "completed_at": "end",
                    "wall_seconds": 1.0,
                    "token_usage": {"status": "unavailable"},
                    "truth_disclosed": False,
                }
                (experiment / "results" / f"{question_id}-run-001.json").write_text(
                    json.dumps(record), encoding="utf-8"
                )
            freeze = module.freeze_candidates(experiment)
            self.assertEqual(freeze["status"], "frozen")
            self.assertEqual(freeze["candidate_count"], 3)
            self.assertFalse(freeze["truth_disclosed_during_solve"])


if __name__ == "__main__":
    unittest.main()
