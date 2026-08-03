import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import claim_ledger  # noqa: E402
import claim_validation  # noqa: E402
import correctness_policy  # noqa: E402

FINGERPRINT = "c" * 64


def claim(kind="derived", check_spec=None):
    return {
        "id": "C1",
        "version": 1,
        "kind": kind,
        "statement": "一个待验证的物理断言",
        "target_ids": ["Q1"],
        "stage_ids": [],
        "depends_on": [],
        "conditions": [],
        "obligation_ids": [],
        "check_spec": check_spec,
        "status": "candidate",
        "source": {
            "task_id": "build-C1",
            "input_fingerprint": FINGERPRINT,
            "policy_version": "claim-ledger-v1",
        },
    }


class ClaimValidationRoutingTest(unittest.TestCase):
    def test_every_claim_kind_has_an_explicit_base_route(self):
        for kind in correctness_policy.CLAIM_KINDS:
            routed = claim_validation.route_claim_verification(claim(kind))
            self.assertEqual(routed["claim_kind"], kind)
            self.assertEqual(routed["required_groups"][0]["id"], "base")
            self.assertTrue(routed["required_groups"][0]["one_of"])

    def test_structured_check_prefers_deterministic_then_semantic_fallback(self):
        routed = claim_validation.route_claim_verification(
            claim("numerical", {"type": "arithmetic", "expression": "1+1"})
        )
        routes = routed["required_groups"][0]["one_of"]
        self.assertEqual(
            (routes[0]["verifier_kind"], routes[0]["check_type"]),
            ("deterministic", "arithmetic"),
        )
        self.assertIn(
            ("independent-agent", "semantic"),
            {(item["verifier_kind"], item["check_type"]) for item in routes},
        )

    def test_legacy_semantic_required_never_becomes_deterministic_pass(self):
        routed = claim_validation.route_claim_verification(claim("derived", {"type": "semantic-required"}))
        self.assertTrue(
            all(route["verifier_kind"] != "deterministic" for route in routed["required_groups"][0]["one_of"])
        )

    def test_high_risk_adds_independent_confirmation(self):
        routed = claim_validation.route_claim_verification(
            claim("boundary", {"type": "event-order"}),
            risk="critical",
        )
        self.assertEqual(
            [group["id"] for group in routed["required_groups"]],
            ["base", "independent-confirmation"],
        )
        self.assertTrue(routed["required_groups"][1]["distinct_verifier_required"])

    def test_unknown_claim_or_check_type_fails_closed(self):
        bad = claim()
        bad["kind"] = "intuition"
        with self.assertRaisesRegex(ValueError, "kind is invalid"):
            claim_validation.route_claim_verification(bad)

        with self.assertRaisesRegex(ValueError, "unsupported claim check_spec"):
            claim_validation.route_claim_verification(claim("derived", {"type": "python-eval"}))

    def test_route_match_enforces_context_isolation(self):
        certificate = {
            "claim_id": "C1",
            "claim_version": 1,
            "verifier_kind": "independent-agent",
            "check_type": "semantic",
            "verdict": "pass",
            "normalized_result": "一致",
            "decisive_checks": ["独立复算"],
            "input_fingerprint": FINGERPRINT,
            "verifier_identity": {
                "model_id": "verifier",
                "provider": "test",
                "context_isolated": True,
            },
        }
        route = {
            "verifier_kind": "independent-agent",
            "check_type": "semantic",
            "context_isolated": True,
        }
        self.assertTrue(claim_validation.certificate_matches_route(certificate, route))
        shared_context = copy.deepcopy(certificate)
        shared_context["verifier_identity"]["context_isolated"] = False
        self.assertFalse(claim_validation.certificate_matches_route(shared_context, route))


class SafeArithmeticTest(unittest.TestCase):
    def test_exact_fraction_and_symbol_substitution_pass(self):
        self.assertEqual(
            claim_validation.safe_evaluate_arithmetic(
                "m * v**2 / (2 * r)",
                {"m": "3/2", "v": 4, "r": 3},
            ),
            4,
        )
        payload = claim(
            "numerical",
            {
                "type": "arithmetic",
                "variables": {"m": "3/2", "v": 4, "r": 3},
                "relations": [
                    {
                        "left": "m * v**2 / (2 * r)",
                        "operator": "==",
                        "right": "4",
                    }
                ],
            },
        )
        certificate = claim_validation.verify_arithmetic_claim(payload, [])
        self.assertEqual(certificate["verdict"], "pass")
        self.assertTrue(certificate["decisive_checks"])

    def test_false_relation_returns_conflict(self):
        payload = claim(
            "numerical",
            {
                "type": "arithmetic",
                "variables": {"v": 3},
                "relations": [
                    {
                        "left": "v**2",
                        "operator": "==",
                        "right": "8",
                    }
                ],
            },
        )
        certificate = claim_validation.verify_arithmetic_claim(payload, [])
        self.assertEqual(certificate["verdict"], "conflict")

    def test_code_execution_and_unsafe_exponents_are_unsupported(self):
        for expression in (
            "__import__('os').system('false')",
            "(1).__class__",
            "[1][0]",
            "2**1000",
        ):
            payload = claim(
                "numerical",
                {
                    "type": "arithmetic",
                    "variables": {},
                    "relations": [
                        {
                            "left": expression,
                            "operator": "==",
                            "right": "1",
                        }
                    ],
                },
            )
            certificate = claim_validation.verify_arithmetic_claim(payload, [])
            self.assertEqual(certificate["verdict"], "unsupported")
            self.assertFalse(certificate["decisive_checks"])

    def test_tolerance_is_explicit_not_implicit(self):
        payload = claim(
            "numerical",
            {
                "type": "arithmetic",
                "variables": {},
                "relations": [
                    {
                        "left": "1 / 3",
                        "operator": "==",
                        "right": "0.333",
                        "absolute_tolerance": "1/1000",
                    }
                ],
            },
        )
        certificate = claim_validation.verify_arithmetic_claim(payload, [])
        self.assertEqual(certificate["verdict"], "pass")


class DimensionCheckTest(unittest.TestCase):
    def magnetic_force_claim(self):
        return claim(
            "derived",
            {
                "type": "dimension",
                "symbols": {
                    "F": {"M": 1, "L": 1, "T": -2},
                    "q": {"I": 1, "T": 1},
                    "v": {"L": 1, "T": -1},
                    "B": {"M": 1, "T": -2, "I": -1},
                },
                "relations": [
                    {
                        "label": "F=qvB",
                        "left": {"symbol": "F"},
                        "right": {
                            "op": "mul",
                            "args": [
                                {"symbol": "q"},
                                {"symbol": "v"},
                                {"symbol": "B"},
                            ],
                        },
                    }
                ],
            },
        )

    def test_mechanics_and_electromagnetic_dimensions_pass(self):
        certificate = claim_validation.verify_dimension_claim(self.magnetic_force_claim(), [])
        self.assertEqual(certificate["verdict"], "pass")
        self.assertIn("F=qvB", certificate["decisive_checks"][0])

        energy_dimension = claim_validation.evaluate_dimension({
            "op": "mul",
            "args": [
                {"unit": "kg"},
                {"op": "pow", "base": {"unit": "m"}, "exponent": 2},
                {"op": "pow", "base": {"unit": "s"}, "exponent": -2},
            ],
        })
        self.assertEqual(
            energy_dimension,
            claim_validation.evaluate_dimension({"unit": "joule"}),
        )

    def test_dimension_mismatch_returns_conflict(self):
        payload = self.magnetic_force_claim()
        payload["check_spec"]["symbols"]["B"] = {"M": 1, "T": -1, "I": -1}
        certificate = claim_validation.verify_dimension_claim(payload, [])
        self.assertEqual(certificate["verdict"], "conflict")

    def test_unknown_unit_returns_unsupported(self):
        payload = claim(
            "derived",
            {
                "type": "dimension",
                "symbols": {},
                "relations": [
                    {
                        "left": {"unit": "banana"},
                        "right": {"unit": "newton"},
                    }
                ],
            },
        )
        certificate = claim_validation.verify_dimension_claim(payload, [])
        self.assertEqual(certificate["verdict"], "unsupported")
        self.assertIn("unknown dimension unit", certificate["normalized_result"])
        self.assertFalse(certificate["decisive_checks"])


class IntervalAndEventOrderTest(unittest.TestCase):
    def interval_claim(self, segments):
        return claim(
            "boundary",
            {
                "type": "interval",
                "domain": {
                    "lower": 0,
                    "upper": 2,
                    "lower_closed": True,
                    "upper_closed": True,
                },
                "segments": segments,
            },
        )

    def test_interval_partition_requires_exact_single_boundary_ownership(self):
        passing = self.interval_claim([
            {
                "label": "left",
                "lower": 0,
                "upper": 1,
                "lower_closed": True,
                "upper_closed": False,
            },
            {
                "label": "right",
                "lower": 1,
                "upper": 2,
                "lower_closed": True,
                "upper_closed": True,
            },
        ])
        self.assertEqual(
            claim_validation.verify_interval_claim(passing, [])["verdict"],
            "pass",
        )

        gap = copy.deepcopy(passing)
        gap["check_spec"]["segments"][1]["lower_closed"] = False
        self.assertEqual(
            claim_validation.verify_interval_claim(gap, [])["verdict"],
            "conflict",
        )

        overlap = copy.deepcopy(passing)
        overlap["check_spec"]["segments"][0]["upper_closed"] = True
        self.assertEqual(
            claim_validation.verify_interval_claim(overlap, [])["verdict"],
            "conflict",
        )

    def event_claim(self, selection, selected_event_ids):
        return claim(
            "boundary",
            {
                "type": "event-order",
                "events": [
                    {"id": "negative", "time": -1, "admissible": True},
                    {"id": "first", "time": "1/2", "admissible": True},
                    {"id": "later", "time": 2, "admissible": True},
                ],
                "selected_event_ids": selected_event_ids,
                "selection": selection,
                "time_lower_bound": {"value": 0, "inclusive": False},
            },
        )

    def test_first_event_filters_domain_and_rejects_later_root(self):
        passing = claim_validation.verify_event_order_claim(self.event_claim("first", ["first"]), [])
        self.assertEqual(passing["verdict"], "pass")

        omitted_earlier = claim_validation.verify_event_order_claim(self.event_claim("first", ["later"]), [])
        self.assertEqual(omitted_earlier["verdict"], "conflict")
        self.assertIn("first", omitted_earlier["normalized_result"])

    def test_unique_and_all_obligations_detect_missing_branches(self):
        unique = claim_validation.verify_event_order_claim(self.event_claim("unique", ["first"]), [])
        self.assertEqual(unique["verdict"], "conflict")

        all_events = claim_validation.verify_event_order_claim(self.event_claim("all", ["first"]), [])
        self.assertEqual(all_events["verdict"], "conflict")


class CertificatePromotionTest(unittest.TestCase):
    def arithmetic_claim(self):
        return claim(
            "numerical",
            {
                "type": "arithmetic",
                "variables": {},
                "relations": [
                    {
                        "left": "1 + 1",
                        "operator": "==",
                        "right": "2",
                    }
                ],
            },
        )

    def semantic_certificate(
        self,
        payload,
        *,
        verdict="pass",
        model_id="independent",
        provider="test",
        context_isolated=True,
    ):
        fingerprint = claim_ledger.claim_verification_input_fingerprint(payload, [])
        return {
            "claim_id": payload["id"],
            "claim_version": payload["version"],
            "verifier_kind": "independent-agent",
            "check_type": "semantic",
            "verdict": verdict,
            "normalized_result": verdict,
            "decisive_checks": ["独立复算"] if verdict == "pass" else [],
            "input_fingerprint": fingerprint,
            "verifier_identity": {
                "model_id": model_id,
                "provider": provider,
                "context_isolated": context_isolated,
            },
        }

    def test_only_current_bound_certificate_promotes_candidate(self):
        payload = self.arithmetic_claim()
        certificate = claim_validation.verify_arithmetic_claim(payload, [])
        result = claim_validation.apply_certificate_decision(payload, [], [certificate])
        self.assertEqual(result["claim"]["status"], "verified")

        stale = copy.deepcopy(certificate)
        stale["claim_version"] = 2
        result = claim_validation.apply_certificate_decision(payload, [], [stale])
        self.assertEqual(result["claim"]["status"], "candidate")
        self.assertEqual(result["assessment"]["stale_certificate_count"], 1)

    def test_missing_or_generator_self_certificate_never_promotes(self):
        payload = claim("model", {"type": "semantic-required"})
        missing = claim_validation.apply_certificate_decision(payload, [], [])
        self.assertEqual(missing["claim"]["status"], "candidate")
        self.assertEqual(missing["assessment"]["decision"], "provisional")

        self_certificate = self.semantic_certificate(payload, model_id="solver", provider="same")
        rejected = claim_validation.apply_certificate_decision(
            payload,
            [],
            [self_certificate],
            generator_identity={"model_id": "solver", "provider": "same"},
        )
        self.assertEqual(rejected["claim"]["status"], "candidate")
        self.assertEqual(rejected["assessment"]["self_certificate_count"], 1)

    def test_conflict_disputes_claim(self):
        payload = self.arithmetic_claim()
        payload["check_spec"]["relations"][0]["right"] = "3"
        conflict = claim_validation.verify_arithmetic_claim(payload, [])
        result = claim_validation.apply_certificate_decision(payload, [], [conflict])
        self.assertEqual(result["claim"]["status"], "disputed")

    def test_high_risk_requires_distinct_independent_confirmation(self):
        payload = self.arithmetic_claim()
        deterministic = claim_validation.verify_arithmetic_claim(payload, [])
        insufficient = self.semantic_certificate(payload, verdict="insufficient")
        provisional = claim_validation.apply_certificate_decision(
            payload,
            [],
            [deterministic, insufficient],
            risk="critical",
        )
        self.assertEqual(provisional["claim"]["status"], "candidate")

        independent = self.semantic_certificate(payload)
        verified = claim_validation.apply_certificate_decision(
            payload,
            [],
            [deterministic, independent],
            risk="critical",
        )
        self.assertEqual(verified["claim"]["status"], "verified")

    def test_unverified_dependency_blocks_downstream_promotion(self):
        upstream = claim("premise", None)
        upstream["id"] = "C0"
        upstream["source"]["task_id"] = "build-C0"
        downstream = self.arithmetic_claim()
        downstream["depends_on"] = ["C0"]
        certificate = claim_validation.verify_arithmetic_claim(downstream, [upstream])
        result = claim_validation.apply_certificate_decision(downstream, [upstream], [certificate])
        self.assertEqual(result["claim"]["status"], "candidate")
        self.assertEqual(result["assessment"]["unverified_dependency_ids"], ["C0"])


class GraphEvidenceGateTest(unittest.TestCase):
    def graph_and_certificates(self):
        premise = claim("premise", None)
        premise["id"] = "C0"
        premise["source"]["task_id"] = "build-C0"

        numerical = claim(
            "numerical",
            {
                "type": "arithmetic",
                "variables": {},
                "relations": [
                    {
                        "left": "2 * 3",
                        "operator": "==",
                        "right": "6",
                    }
                ],
            },
        )
        numerical["depends_on"] = ["C0"]

        final = claim("final", {"type": "aggregation"})
        final["id"] = "C2"
        final["depends_on"] = ["C1"]
        final["obligation_ids"] = ["V1"]
        final["source"]["task_id"] = "build-C2"

        premise_fp = claim_ledger.claim_verification_input_fingerprint(premise, [])
        source_certificate = {
            "claim_id": "C0",
            "claim_version": 1,
            "verifier_kind": "source",
            "check_type": "source-match",
            "verdict": "pass",
            "normalized_result": "approved source fact S1",
            "decisive_checks": ["statement equals approved source fact S1"],
            "input_fingerprint": premise_fp,
            "verifier_identity": {
                "model_id": "source-review",
                "provider": "local",
                "context_isolated": True,
            },
        }
        arithmetic_certificate = claim_validation.verify_arithmetic_claim(numerical, [premise])
        final_fp = claim_ledger.claim_verification_input_fingerprint(final, [numerical])
        final_certificate = {
            "claim_id": "C2",
            "claim_version": 1,
            "verifier_kind": "deterministic",
            "check_type": "aggregation",
            "verdict": "pass",
            "normalized_result": "all dependencies verified",
            "decisive_checks": ["C1 supports C2 and covers V1"],
            "input_fingerprint": final_fp,
            "verifier_identity": {
                "model_id": "proof-aggregation-v1",
                "provider": "local",
                "context_isolated": True,
            },
        }
        return (
            [premise, numerical, final],
            [source_certificate, arithmetic_certificate, final_certificate],
        )

    def test_full_current_certificate_coverage_is_verified(self):
        claims, certificates = self.graph_and_certificates()
        report = claim_validation.evaluate_claim_graph_evidence(
            claims,
            certificates,
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
        )
        self.assertEqual(report["result_status"], "VERIFIED")
        self.assertTrue(report["all_claims_verified"])
        self.assertEqual(report["critical_certificate_coverage"], 1.0)

    def test_missing_final_certificate_can_only_be_provisional(self):
        claims, certificates = self.graph_and_certificates()
        report = claim_validation.evaluate_claim_graph_evidence(
            claims,
            certificates[:-1],
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
        )
        self.assertEqual(report["result_status"], "PROVISIONAL")
        self.assertFalse(report["all_claims_verified"])
        self.assertEqual(report["critical_certificate_coverage"], 0.0)

    def test_stored_verified_status_without_current_certificate_is_not_trusted(self):
        claims, certificates = self.graph_and_certificates()
        claims[-1]["status"] = "verified"
        report = claim_validation.evaluate_claim_graph_evidence(
            claims,
            certificates[:-1],
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
        )
        self.assertEqual(report["result_status"], "PROVISIONAL")
        final = next(item for item in report["claims"] if item["id"] == "C2")
        self.assertEqual(final["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
