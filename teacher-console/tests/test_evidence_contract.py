import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import evidence_contract  # noqa: E402
import evidence_evaluation  # noqa: E402


def fingerprint(label):
    return evidence_contract.stable_fingerprint("test-fixture-v1", label)


def retrieval_need(*, criticality="required"):
    return {
        "schema": "wuli.retrieval-need.v1",
        "need_id": "N1",
        "purpose": "applicability_check",
        "question": "何时可以使用 r=mv/|q|B？",
        "required_facets": ["受力条件", "速度与磁场方向", "适用公式"],
        "forbidden_conflicts": ["存在未处理的电场做功"],
        "minimum_authority": "B",
        "criticality": criticality,
        "target_ids": ["Q1"],
        "stage_ids": ["P1"],
        "obligation_ids": ["V1"],
    }


def evidence_unit(*, evidence_id="EU1", source_kind="approved_solution", authority="B"):
    return evidence_contract.normalize_evidence_unit({
        "schema": "wuli.evidence-unit.v1",
        "evidence_id": evidence_id,
        "unit_kind": "method_applicability",
        "source_kind": source_kind,
        "source_locator": {
            "path": "student-error-library/entries/example/teacher-solution.md",
            "section": "磁场阶段",
            "start_line": 12,
            "end_line": 16,
        },
        "text": "仅受匀强磁场洛伦兹力且速度垂直磁场时，轨迹为圆。",
        "physics_facets": ["受力条件", "速度与磁场方向", "适用公式"],
        "applicability": ["仅受磁场力", "速度垂直磁场"],
        "exceptions": ["存在电场时需重新检查能量变化"],
        "authority_level": authority,
    })


def sufficient_run():
    need = evidence_contract.normalize_retrieval_need(retrieval_need())
    unit = evidence_unit()
    question_hash = fingerprint("question")
    blueprint_hash = fingerprint("blueprint")
    store_hash = fingerprint("store")
    task_hash = evidence_contract.evidence_build_task_fingerprint(
        question_snapshot_hash=question_hash,
        blueprint_fingerprint=blueprint_hash,
        knowledge_store_fingerprint=store_hash,
        retrieval_needs=[need],
        fusion_policy="single-route-bypass",
    )
    return {
        "schema": "wuli.evidence-agent-run.v1",
        "task_fingerprint": task_hash,
        "question_snapshot_hash": question_hash,
        "blueprint_fingerprint": blueprint_hash,
        "knowledge_store_fingerprint": store_hash,
        "fusion_policy": "single-route-bypass",
        "status": "sufficient",
        "retrieval_needs": [need],
        "evidence_set": [unit],
        "coverage": [
            {
                "need_id": "N1",
                "status": "covered",
                "evidence_ids": ["EU1"],
                "covered_facets": ["受力条件", "速度与磁场方向", "适用公式"],
                "missing_facets": [],
                "hard_conflicts": [],
            }
        ],
        "usage_ledger": {
            "schema": "wuli.evidence-usage-ledger.v1",
            "entries": [
                {
                    "evidence_id": "EU1",
                    "need_ids": ["N1"],
                    "usage": "method_hint",
                    "influenced_claim_ids": [],
                    "verification_ids": [],
                }
            ],
        },
        "retrieval_trace": [
            {
                "round": 1,
                "query_ids": ["query-N1-1"],
                "candidate_evidence_ids": ["EU1"],
                "newly_covered_need_ids": ["N1"],
                "stop_reason": "all-required-needs-covered",
            }
        ],
        "insufficient_evidence": None,
    }


class EvidenceContractTest(unittest.TestCase):
    def test_schema_artifacts_are_present_and_well_formed(self):
        schema_dir = ROOT / "teacher-console" / "schemas"
        expected = {
            "retrieval-need.v1.schema.json": "wuli.retrieval-need.v1",
            "evidence-unit.v1.schema.json": "wuli.evidence-unit.v1",
            "evidence-usage-ledger.v1.schema.json": "wuli.evidence-usage-ledger.v1",
            "evidence-agent-run.v1.schema.json": "wuli.evidence-agent-run.v1",
            "evidence-gold-case.v1.schema.json": "wuli.evidence-gold-case.v1",
            "evidence-reflection.v1.schema.json": "wuli.evidence-reflection.v1",
            "evidence-reflection.v2.schema.json": "wuli.evidence-reflection.v2",
            "evidence-reflection.v3.schema.json": "wuli.evidence-reflection.v3",
            "evidence-overlay.v1.schema.json": "wuli.evidence-overlay.v1",
            "evidence-gold-dataset.v1.schema.json": "wuli.evidence-gold-dataset.v1",
            "evidence-gold-review.v1.schema.json": "wuli.evidence-gold-review.v1",
        }
        for filename, schema_id in expected.items():
            payload = json.loads((schema_dir / filename).read_text(encoding="utf-8"))
            self.assertEqual(payload["$id"], schema_id)

    def test_retrieval_need_requires_typed_purpose_and_facets(self):
        normalized = evidence_contract.normalize_retrieval_need(retrieval_need())
        self.assertEqual(normalized["criticality"], "required")
        invalid = retrieval_need()
        invalid["purpose"] = "solve_current_question"
        with self.assertRaisesRegex(ValueError, "purpose"):
            evidence_contract.normalize_retrieval_need(invalid)
        invalid = retrieval_need()
        invalid["required_facets"] = []
        with self.assertRaisesRegex(ValueError, "required_facets"):
            evidence_contract.normalize_retrieval_need(invalid)

    def test_retrieval_need_optional_diagnostic_targets_preserve_legacy_shape(self):
        legacy = retrieval_need()
        self.assertNotIn(
            "diagnostic_targets",
            evidence_contract.normalize_retrieval_need(legacy),
        )
        declared = retrieval_need()
        declared["diagnostic_targets"] = ["中途改变角度正方向并直接相加"]
        self.assertEqual(
            evidence_contract.normalize_retrieval_need(declared)["diagnostic_targets"],
            ["中途改变角度正方向并直接相加"],
        )

    def test_evidence_unit_binds_authority_path_and_canonical_hash(self):
        normalized = evidence_unit()
        self.assertTrue(normalized["content_hash"].startswith("sha256:"))
        invalid = copy.deepcopy(normalized)
        invalid["authority_level"] = "A"
        with self.assertRaisesRegex(ValueError, "source_kind policy"):
            evidence_contract.normalize_evidence_unit(invalid)
        invalid = copy.deepcopy(normalized)
        invalid["source_locator"]["path"] = "../private.md"
        with self.assertRaisesRegex(ValueError, "safe relative path"):
            evidence_contract.normalize_evidence_unit(invalid)

    def test_usage_ledger_requires_current_problem_verification_for_claim_influence(self):
        ledger = {
            "entries": [
                {
                    "evidence_id": "EU1",
                    "need_ids": ["N1"],
                    "usage": "method_hint",
                    "influenced_claim_ids": ["C1"],
                    "verification_ids": [],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "current-problem verification"):
            evidence_contract.normalize_usage_ledger(ledger)

    def test_sufficient_run_requires_full_coverage_and_valid_task_fingerprint(self):
        normalized = evidence_contract.normalize_evidence_agent_run(sufficient_run())
        self.assertEqual(normalized["status"], "sufficient")

        invalid = sufficient_run()
        invalid["coverage"][0]["covered_facets"].remove("适用公式")
        with self.assertRaisesRegex(ValueError, "every required facet"):
            evidence_contract.normalize_evidence_agent_run(invalid)

        invalid = sufficient_run()
        invalid["task_fingerprint"] = fingerprint("wrong-task")
        with self.assertRaisesRegex(ValueError, "task_fingerprint mismatch"):
            evidence_contract.normalize_evidence_agent_run(invalid)

    def test_routing_summary_cannot_enter_evidence_set(self):
        invalid = sufficient_run()
        invalid["evidence_set"] = [evidence_unit(evidence_id="EU1", source_kind="routing_summary", authority="N")]
        with self.assertRaisesRegex(ValueError, "cannot enter evidence_set"):
            evidence_contract.normalize_evidence_agent_run(invalid)


class EvidenceEvaluationTest(unittest.TestCase):
    def gold_case(self):
        return {
            "schema": "wuli.evidence-gold-case.v1",
            "case_id": "case-1",
            "question_snapshot_hash": fingerprint("question"),
            "retrieval_need": retrieval_need(),
            "required_evidence_ids": ["EU1"],
            "acceptable_evidence_ids": ["EU1", "EU2"],
            "forbidden_evidence_ids": ["BAD1"],
            "expected_status": "sufficient",
            "teacher_rationale": "EU1同时保留公式、条件和例外。",
            "evaluation_split": "holdout",
            "batch_id": "holdout-2026-08-a",
        }

    def prediction(self):
        return {
            "schema": "wuli.evidence-gold-prediction.v1",
            "case_id": "case-1",
            "status": "sufficient",
            "candidate_evidence_ids": ["EU1", "BAD1"],
            "selected_evidence_ids": ["EU1"],
            "traceable_evidence_ids": ["EU1"],
        }

    def test_three_layer_score_separates_candidates_from_selected_evidence(self):
        report = evidence_evaluation.score_gold_cases([self.gold_case()], [self.prediction()])
        self.assertEqual(report["metrics"]["candidate_required_recall"], 1.0)
        self.assertEqual(report["metrics"]["candidate_false_friend_count"], 1)
        self.assertEqual(report["metrics"]["false_friend_admission_count"], 0)
        self.assertTrue(report["gates"]["evidence_set_gate_ready"])

    def test_false_friend_selection_fails_gate_even_when_status_is_sufficient(self):
        prediction = self.prediction()
        prediction["selected_evidence_ids"].append("BAD1")
        prediction["traceable_evidence_ids"].append("BAD1")
        report = evidence_evaluation.score_gold_cases([self.gold_case()], [prediction])
        self.assertEqual(report["metrics"]["false_friend_admission_count"], 1)
        self.assertFalse(report["gates"]["evidence_set_gate_ready"])

    def test_irrelevant_traceable_evidence_fails_precision_gate(self):
        prediction = self.prediction()
        prediction["candidate_evidence_ids"].append("IRRELEVANT")
        prediction["selected_evidence_ids"].append("IRRELEVANT")
        prediction["traceable_evidence_ids"].append("IRRELEVANT")
        report = evidence_evaluation.score_gold_cases([self.gold_case()], [prediction])
        self.assertLess(report["metrics"]["evidence_precision"], 1.0)
        self.assertFalse(report["gates"]["evidence_precision_100"])
        self.assertFalse(report["gates"]["evidence_set_gate_ready"])

    def test_holdout_requires_batch_and_required_evidence_must_be_acceptable(self):
        invalid = self.gold_case()
        invalid["batch_id"] = ""
        with self.assertRaisesRegex(ValueError, "batch_id"):
            evidence_evaluation.normalize_gold_case(invalid)
        invalid = self.gold_case()
        invalid["acceptable_evidence_ids"] = []
        with self.assertRaisesRegex(ValueError, "also be acceptable"):
            evidence_evaluation.normalize_gold_case(invalid)


if __name__ == "__main__":
    unittest.main()
