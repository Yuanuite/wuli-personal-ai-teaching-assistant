import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import claim_ledger  # noqa: E402


FINGERPRINT = "a" * 64


def valid_claim():
    return {
        "id": "C17",
        "version": 1,
        "kind": "boundary",
        "statement": "第一次进入区域的时刻为 t1。",
        "target_ids": ["Q1"],
        "stage_ids": ["P2"],
        "depends_on": ["C11"],
        "conditions": ["t > 0"],
        "obligation_ids": ["V3"],
        "check_spec": {
            "type": "event-order",
            "candidate_roots": ["t1", "t2"],
        },
        "status": "candidate",
        "source": {
            "task_id": "derive-C17-v1",
            "input_fingerprint": FINGERPRINT,
            "policy_version": "claim-ledger-v1",
        },
    }


def valid_certificate():
    return {
        "claim_id": "C17",
        "claim_version": 1,
        "verifier_kind": "deterministic",
        "check_type": "event-order",
        "verdict": "pass",
        "normalized_result": "t1 < t2",
        "decisive_checks": ["枚举全部正根后 t1 最小"],
        "input_fingerprint": FINGERPRINT,
        "verifier_identity": {
            "model_id": "deterministic-event-order-v1",
            "provider": "local",
            "context_isolated": True,
        },
    }


def valid_task():
    return {
        "task_id": "verify-C17-v1",
        "action": "verify_claim",
        "target_ids": ["C17"],
        "input_snapshot": 4,
        "input_fingerprint": FINGERPRINT,
        "output_contract": "wuli.claim-verify.v1",
        "strategy": "event-order",
        "random_seed": None,
        "status": "pending",
    }


def graph_claim(
    claim_id,
    kind,
    *,
    depends_on=None,
    target_ids=None,
    obligation_ids=None,
    version=1,
    status="candidate",
):
    return {
        "id": claim_id,
        "version": version,
        "kind": kind,
        "statement": f"{claim_id} 的可审计陈述",
        "target_ids": target_ids or ["Q1"],
        "stage_ids": [],
        "depends_on": depends_on or [],
        "conditions": [],
        "obligation_ids": obligation_ids or [],
        "check_spec": None,
        "status": status,
        "source": {
            "task_id": f"build-{claim_id}-v{version}",
            "input_fingerprint": FINGERPRINT,
            "policy_version": "claim-ledger-v1",
        },
    }


def valid_graph():
    return [
        graph_claim("C1", "premise"),
        graph_claim("C2", "derived", depends_on=["C1"], obligation_ids=["V1"]),
        graph_claim(
            "C3",
            "boundary",
            depends_on=["C2"],
            obligation_ids=["V2"],
        ),
        graph_claim("C4", "final", depends_on=["C3"]),
        graph_claim("C5", "premise", target_ids=["Q2"]),
        graph_claim("C6", "final", depends_on=["C5"], target_ids=["Q2"]),
    ]


class ClaimLedgerContractTest(unittest.TestCase):
    def test_normalizes_claim_certificate_task_and_snapshot(self):
        claim = claim_ledger.normalize_claim(valid_claim())
        certificate = claim_ledger.normalize_certificate(valid_certificate())
        task = claim_ledger.normalize_atomic_task(valid_task())
        snapshot = claim_ledger.normalize_graph_snapshot({
            "schema_version": 1,
            "snapshot_version": 4,
            "policy_version": "claim-ledger-v1",
            "input_fingerprint": FINGERPRINT,
            "claims": [{**claim, "status": "verified"}],
            "certificates": [certificate],
            "tasks": [{**task, "status": "completed"}],
        })
        self.assertEqual(snapshot["claims"][0]["status"], "verified")
        self.assertEqual(snapshot["certificates"][0]["verdict"], "pass")
        self.assertEqual(snapshot["tasks"][0]["action"], "verify_claim")

    def test_agent_cannot_self_report_verified(self):
        payload = valid_claim()
        payload["status"] = "verified"
        with self.assertRaisesRegex(ValueError, "must be candidate"):
            claim_ledger.normalize_claim(payload)

    def test_rejects_invalid_enums_and_missing_fields(self):
        payload = valid_claim()
        payload["kind"] = "intuition"
        with self.assertRaisesRegex(ValueError, "kind is invalid"):
            claim_ledger.normalize_claim(payload)

        payload = valid_certificate()
        payload["verdict"] = "probably"
        with self.assertRaisesRegex(ValueError, "verdict is invalid"):
            claim_ledger.normalize_certificate(payload)

        payload = valid_task()
        del payload["input_snapshot"]
        with self.assertRaisesRegex(ValueError, "missing fields"):
            claim_ledger.normalize_atomic_task(payload)

    def test_rejects_unknown_fields_and_non_json_check_specs(self):
        payload = valid_claim()
        payload["confidence"] = 0.99
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            claim_ledger.normalize_claim(payload)

        payload = valid_claim()
        payload["check_spec"] = {"bad": {1, 2}}
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            claim_ledger.normalize_claim(payload)

    def test_rejects_self_dependency_and_duplicate_snapshot_identity(self):
        payload = valid_claim()
        payload["depends_on"] = ["C17"]
        with self.assertRaisesRegex(ValueError, "depend on itself"):
            claim_ledger.normalize_claim(payload)

        claim = claim_ledger.normalize_claim(valid_claim())
        snapshot = {
            "schema_version": 1,
            "snapshot_version": 1,
            "policy_version": "claim-ledger-v1",
            "input_fingerprint": FINGERPRINT,
            "claims": [claim, copy.deepcopy(claim)],
            "certificates": [],
            "tasks": [],
        }
        with self.assertRaisesRegex(ValueError, "unique id/version"):
            claim_ledger.normalize_graph_snapshot(snapshot)

    def test_pass_certificate_requires_decisive_check(self):
        payload = valid_certificate()
        payload["decisive_checks"] = []
        with self.assertRaisesRegex(ValueError, "decisive check"):
            claim_ledger.normalize_certificate(payload)

    def test_claim_fingerprint_is_stable_and_tracks_semantic_changes(self):
        original = valid_claim()
        reordered = {
            key: copy.deepcopy(original[key])
            for key in reversed(list(original))
        }
        reordered["status"] = "disputed"
        self.assertEqual(
            claim_ledger.claim_fingerprint(original),
            claim_ledger.claim_fingerprint(reordered),
        )

        changed_condition = copy.deepcopy(original)
        changed_condition["conditions"] = ["t >= 0"]
        self.assertNotEqual(
            claim_ledger.claim_fingerprint(original),
            claim_ledger.claim_fingerprint(changed_condition),
        )

        changed_dependency = copy.deepcopy(original)
        changed_dependency["depends_on"] = ["C12"]
        self.assertNotEqual(
            claim_ledger.claim_fingerprint(original),
            claim_ledger.claim_fingerprint(changed_dependency),
        )

    def test_verification_fingerprint_binds_exact_dependency_version(self):
        upstream = valid_claim()
        upstream.update({
            "id": "C11",
            "depends_on": [],
            "kind": "premise",
            "statement": "粒子从原点进入区域。",
            "check_spec": None,
        })
        version_two = copy.deepcopy(upstream)
        version_two["version"] = 2
        version_two["statement"] = "粒子在 t=0 从原点进入区域。"
        first = claim_ledger.claim_verification_input_fingerprint(
            valid_claim(), [upstream]
        )
        second = claim_ledger.claim_verification_input_fingerprint(
            valid_claim(), [version_two]
        )
        self.assertNotEqual(first, second)

    def test_task_fingerprint_ignores_identity_but_includes_seed(self):
        first = valid_task()
        renamed = copy.deepcopy(first)
        renamed["task_id"] = "verify-C17-retry"
        renamed["status"] = "running"
        self.assertEqual(
            claim_ledger.atomic_task_fingerprint(first),
            claim_ledger.atomic_task_fingerprint(renamed),
        )
        changed_seed = copy.deepcopy(first)
        changed_seed["random_seed"] = 7
        self.assertNotEqual(
            claim_ledger.atomic_task_fingerprint(first),
            claim_ledger.atomic_task_fingerprint(changed_seed),
        )

    def test_claim_versions_are_monotonic_and_no_op_revisions_are_rejected(self):
        previous = valid_claim()
        candidate = copy.deepcopy(previous)
        candidate["version"] = 2
        candidate["statement"] = "第一次进入区域的最早正时刻为 t1。"
        normalized = claim_ledger.require_next_claim_version(
            candidate, [previous]
        )
        self.assertEqual(normalized["version"], 2)
        self.assertEqual(
            claim_ledger.next_claim_version("C17", [previous, candidate]), 3
        )

        gap = copy.deepcopy(candidate)
        gap["version"] = 4
        with self.assertRaisesRegex(ValueError, "must be 2"):
            claim_ledger.require_next_claim_version(gap, [previous])

        no_op = copy.deepcopy(previous)
        no_op["version"] = 2
        with self.assertRaisesRegex(ValueError, "must change claim content"):
            claim_ledger.require_next_claim_version(no_op, [previous])

    def test_validates_dag_target_and_obligation_coverage(self):
        report = claim_ledger.validate_claim_graph(
            valid_graph(),
            expected_target_ids={"Q1", "Q2"},
            expected_obligation_ids={"V1", "V2"},
        )
        self.assertEqual(report["status"], "valid")
        self.assertEqual(
            report["topological_order"], ["C1", "C2", "C3", "C4", "C5", "C6"]
        )
        self.assertTrue(all(report["target_coverage"].values()))
        self.assertTrue(all(report["obligation_coverage"].values()))

    def test_rejects_cycles_and_dangling_dependencies(self):
        cyclic = valid_graph()
        cyclic[0]["depends_on"] = ["C4"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            claim_ledger.validate_claim_graph(
                cyclic,
                expected_target_ids={"Q1", "Q2"},
                expected_obligation_ids={"V1", "V2"},
            )

        dangling = valid_graph()
        dangling[1]["depends_on"] = ["C99"]
        with self.assertRaisesRegex(ValueError, "dangling dependency C99"):
            claim_ledger.validate_claim_graph(
                dangling,
                expected_target_ids={"Q1", "Q2"},
                expected_obligation_ids={"V1", "V2"},
            )

    def test_rejects_missing_target_and_obligation_coverage(self):
        without_q2_final = valid_graph()[:-1]
        with self.assertRaisesRegex(ValueError, "do not cover targets.*Q2"):
            claim_ledger.validate_claim_graph(
                without_q2_final,
                expected_target_ids={"Q1", "Q2"},
                expected_obligation_ids={"V1", "V2"},
            )

        without_v2 = valid_graph()
        without_v2[2]["obligation_ids"] = []
        with self.assertRaisesRegex(ValueError, "do not cover obligations.*V2"):
            claim_ledger.validate_claim_graph(
                without_v2,
                expected_target_ids={"Q1", "Q2"},
                expected_obligation_ids={"V1", "V2"},
            )

    def test_impact_cone_contains_only_true_downstream_claims(self):
        graph = valid_graph()
        self.assertEqual(
            claim_ledger.dependency_impact_cone(graph, {"C2"}),
            ["C3", "C4"],
        )
        self.assertEqual(
            claim_ledger.dependency_impact_cone(
                graph, {"C2"}, include_changed=True
            ),
            ["C2", "C3", "C4"],
        )
        self.assertNotIn(
            "C6", claim_ledger.dependency_impact_cone(graph, {"C2"})
        )

    def test_superseded_history_is_excluded_from_active_graph(self):
        old = graph_claim("C2", "derived", version=1, status="superseded")
        current = graph_claim(
            "C2",
            "derived",
            version=2,
            depends_on=["C1"],
            obligation_ids=["V1"],
        )
        graph = [valid_graph()[0], old, current, *valid_graph()[2:]]
        report = claim_ledger.validate_claim_graph(
            graph,
            expected_target_ids={"Q1", "Q2"},
            expected_obligation_ids={"V1", "V2"},
        )
        self.assertEqual(report["active_claim_count"], 6)

        current["status"] = "verified"
        old["status"] = "candidate"
        with self.assertRaisesRegex(ValueError, "multiple non-superseded versions"):
            claim_ledger.active_claims(graph)


if __name__ == "__main__":
    unittest.main()
