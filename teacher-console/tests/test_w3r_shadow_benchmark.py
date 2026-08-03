import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SCRIPTS = CONSOLE / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SCRIPTS))

import w3r_contract  # noqa: E402
import w3r_shadow_benchmark  # noqa: E402

FIXTURE = CONSOLE / "tests" / "fixtures" / "w3r" / "verified-multi-target.json"
CONDITION_FIXTURE = CONSOLE / "tests" / "fixtures" / "w3r" / "verified-condition-matrix.json"


class W3RShadowBenchmarkTest(unittest.TestCase):
    def cases(self):
        cases = []
        for path in (FIXTURE, CONDITION_FIXTURE):
            payload = json.loads(path.read_text(encoding="utf-8"))
            built = w3r_contract.build_w3r_brief(payload["problem"], payload["blueprint"], payload["proof_package"])
            cases.append((path.stem, built["brief"]))
        return cases

    def test_same_brief_pair_passes_all_hard_shadow_gates(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        built = w3r_contract.build_w3r_brief(payload["problem"], payload["blueprint"], payload["proof_package"])
        report = w3r_shadow_benchmark.benchmark([("verified-multi-target", built["brief"])])
        self.assertTrue(report["gates"]["shadow_candidate_eligible"])
        self.assertFalse(report["gates"]["production_default_eligible"])
        self.assertTrue(report["cases"][0]["paired_input_identical"])
        self.assertFalse(report["cases"][0]["production_replaced"])

    def test_markdown_report_exposes_each_fidelity_metric(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        built = w3r_contract.build_w3r_brief(payload["problem"], payload["blueprint"], payload["proof_package"])
        report = w3r_shadow_benchmark.benchmark([("case", built["brief"])])
        text = w3r_shadow_benchmark.markdown_report(report)
        for heading in ("Final", "Claim", "Condition", "Target", "LaTeX", "Unsupported"):
            self.assertIn(heading, text)

    def test_condition_matrix_is_a_second_independent_shadow_case(self):
        report = w3r_shadow_benchmark.benchmark(self.cases())
        self.assertEqual(report["case_count"], 2)
        self.assertTrue(report["gates"]["shadow_candidate_eligible"])

    def test_blind_packet_hides_renderer_identity_and_binds_private_key(self):
        packet, key = w3r_shadow_benchmark.build_blind_packet(self.cases())
        public_text = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("candidate_label", public_text)
        self.assertNotIn("baseline_label", public_text)
        self.assertTrue(packet["blinded"])
        self.assertEqual(
            key["packet_fingerprint"],
            w3r_contract.stable_fingerprint(packet),
        )

    def test_incomplete_teacher_review_fails_closed(self):
        packet, key = w3r_shadow_benchmark.build_blind_packet(self.cases())
        score = w3r_shadow_benchmark.score_blind_reviews(packet, key, {"cases": []})
        self.assertEqual(score["status"], "incomplete")
        self.assertTrue(score["errors"])

    def test_complete_teacher_review_unblinds_preferences_and_edit_rate(self):
        packet, key = w3r_shadow_benchmark.build_blind_packet(self.cases())
        key_by_id = {item["case_id"]: item for item in key["cases"]}
        reviews = {"cases": []}
        for case in packet["cases"]:
            identity = key_by_id[case["case_id"]]
            reviews["cases"].append({
                "case_id": case["case_id"],
                "approved": True,
                "preferred_version": identity["candidate_label"],
                "target_fidelity": {target_id: True for target_id in case["target_ids"]},
                "conditions_checked": True,
                "claim_spans_checked": True,
                "edit_required_versions": [identity["baseline_label"]],
            })
        score = w3r_shadow_benchmark.score_blind_reviews(packet, key, reviews)
        self.assertEqual(score["status"], "completed")
        self.assertEqual(score["teacher_readability_preference"], 1.0)
        self.assertTrue(score["teacher_edit_rate_non_regression"])

        report = w3r_shadow_benchmark.benchmark(self.cases())
        evidence = w3r_shadow_benchmark.rollout_evidence(report, score)
        self.assertEqual(evidence["teacher_reviewed_case_count"], 2)
        self.assertEqual(evidence["final_answer_fidelity"], 1.0)
        self.assertEqual(evidence["fresh_holdout_case_count"], 0)
        self.assertTrue(evidence["report_digest"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
