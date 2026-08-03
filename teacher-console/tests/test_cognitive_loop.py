import copy
import random
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import claim_ledger  # noqa: E402
import cognitive_loop  # noqa: E402


def interface(stage_id, *, entry_x, exit_x):
    return {
        "stage_id": stage_id,
        "coordinate_frame": "laboratory",
        "time_origin": "problem-t0",
        "directions": {"x": "right", "y": "up"},
        "entry_state": {"x": entry_x, "speed": "v0"},
        "exit_state": {"x": exit_x, "speed": "v0"},
        "required_entry_keys": ["x", "speed"],
        "carried_state_keys": ["speed"],
    }


def transition():
    return {
        "from_stage": "P1",
        "to_stage": "P2",
        "event": "cross boundary",
        "state_mapping": [
            {"from_key": "x", "to_key": "x", "transform": None},
            {"from_key": "speed", "to_key": "speed", "transform": None},
        ],
        "introduced_entry_keys": [],
        "coordinate_transform": None,
        "time_transform": None,
        "direction_transform": None,
    }


class StageInterfaceTest(unittest.TestCase):
    def test_matching_stage_interfaces_pass(self):
        report = cognitive_loop.check_stage_interfaces(
            [
                interface("P1", entry_x="0", exit_x="L"),
                interface("P2", entry_x="L", exit_x="2L"),
            ],
            [transition()],
        )
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["issues"])

    def test_locates_reference_time_direction_and_state_mismatches(self):
        stages = [
            interface("P1", entry_x="0", exit_x="L"),
            interface("P2", entry_x="-L", exit_x="-2L"),
        ]
        stages[1]["coordinate_frame"] = "moving-frame"
        stages[1]["time_origin"] = "stage-entry"
        stages[1]["directions"]["x"] = "left"
        report = cognitive_loop.check_stage_interfaces(stages, [transition()])
        self.assertEqual(report["status"], "conflict")
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("coordinate-frame-mismatch", codes)
        self.assertIn("time-origin-mismatch", codes)
        self.assertIn("direction-mismatch", codes)
        self.assertIn("state-value-mismatch", codes)
        state_issue = next(item for item in report["issues"] if item["code"] == "state-value-mismatch")
        self.assertEqual(state_issue["from_stage"], "P1")
        self.assertEqual(state_issue["to_stage"], "P2")
        self.assertEqual(state_issue["state_key"], "x")

    def test_unverified_transform_is_provisional_not_pass(self):
        stages = [
            interface("P1", entry_x="0", exit_x="L"),
            interface("P2", entry_x="-L", exit_x="-2L"),
        ]
        stages[1]["directions"]["x"] = "left"
        mapped = transition()
        mapped["direction_transform"] = "x2=-x1"
        mapped["state_mapping"][0]["transform"] = "x2=-x1"
        report = cognitive_loop.check_stage_interfaces(stages, [mapped])
        self.assertEqual(report["status"], "provisional")
        self.assertEqual(report["semantic_required_count"], 2)

    def test_event_may_explicitly_introduce_a_new_entry_state(self):
        stages = [
            interface("P1", entry_x="0", exit_x="L"),
            interface("P2", entry_x="L", exit_x="2L"),
        ]
        stages[1]["entry_state"]["oil_density"] = "rho_oil"
        stages[1]["required_entry_keys"].append("oil_density")
        mapped = transition()
        mapped["introduced_entry_keys"] = ["oil_density"]
        report = cognitive_loop.check_stage_interfaces(stages, [mapped])
        self.assertEqual(report["status"], "pass")

    def test_carried_state_may_evolve_but_missing_transition_mapping_conflicts(self):
        first = interface("P1", entry_x="0", exit_x="L")
        first["exit_state"]["speed"] = "2v0"
        mapped = transition()
        mapped["state_mapping"] = mapped["state_mapping"][:1]
        report = cognitive_loop.check_stage_interfaces(
            [first, interface("P2", entry_x="L", exit_x="2L")],
            [mapped],
        )
        codes = {item["code"] for item in report["issues"]}
        self.assertNotIn("carried-state-changed", codes)
        self.assertIn("missing-entry-state", codes)

    def test_interface_contract_rejects_unknown_fields(self):
        payload = copy.deepcopy(interface("P1", entry_x="0", exit_x="L"))
        payload["confidence"] = 0.9
        with self.assertRaisesRegex(ValueError, "fields are invalid"):
            cognitive_loop.normalize_stage_interface(payload)

    def test_direction_keys_allow_signed_ascii_axes(self):
        payload = interface("P1", entry_x="0", exit_x="L")
        payload["directions"] = {"+x": "right", "-y": "down"}
        normalized = cognitive_loop.normalize_stage_interface(payload)
        self.assertEqual(normalized["directions"]["+x"], "right")

    def test_carried_keys_are_tracking_metadata_not_continuity_proof(self):
        payload = interface("P1", entry_x="0", exit_x="L")
        payload["carried_state_keys"] = ["future_observable"]
        normalized = cognitive_loop.normalize_stage_interface(payload)
        self.assertEqual(normalized["carried_state_keys"], ["future_observable"])

    def test_physics_symbol_state_keys_normalize_consistently(self):
        payload = interface("P1", entry_x="0", exit_x="L")
        payload["entry_state"]["ρ₀"] = "water density"
        payload["exit_state"]["ρ₀"] = "water density"
        payload["required_entry_keys"].append("ρ₀")
        normalized = cognitive_loop.normalize_stage_interface(payload)
        normalized_key = next(key for key in normalized["entry_state"] if key.startswith("u03c1"))
        self.assertIn(normalized_key, normalized["required_entry_keys"])


class ChallengeTicketTest(unittest.TestCase):
    def ticket(self):
        return {
            "id": "CH9",
            "snapshot_version": 4,
            "trigger": "verification-conflict",
            "claim_ids": ["C11", "C17"],
            "specific_doubt": "The first event may omit an earlier positive root.",
            "falsification_test": "Enumerate every positive root and sort by time.",
            "suggested_backjump": "C11",
            "status": "candidate",
        }

    def test_challenge_is_bound_specific_and_agent_candidate_only(self):
        normalized = cognitive_loop.normalize_challenge_ticket(
            self.ticket(),
            available_claim_ids={"C11", "C17", "C20"},
        )
        self.assertEqual(normalized["snapshot_version"], 4)
        self.assertEqual(normalized["suggested_backjump"], "C11")

        trusted = self.ticket()
        trusted["status"] = "resolved"
        with self.assertRaisesRegex(ValueError, "must be candidate"):
            cognitive_loop.normalize_challenge_ticket(trusted)

    def test_vague_or_unbound_challenge_is_rejected(self):
        vague = self.ticket()
        vague["specific_doubt"] = "检查一下"
        with self.assertRaisesRegex(ValueError, "specific and testable"):
            cognitive_loop.normalize_challenge_ticket(vague)

        unknown = self.ticket()
        with self.assertRaisesRegex(ValueError, "unknown claims"):
            cognitive_loop.normalize_challenge_ticket(unknown, available_claim_ids={"C11"})

        bad_backjump = self.ticket()
        bad_backjump["suggested_backjump"] = "C99"
        with self.assertRaisesRegex(ValueError, "one of claim_ids"):
            cognitive_loop.normalize_challenge_ticket(bad_backjump)

    def test_interface_issue_becomes_ticket_without_mutating_issue(self):
        issue = {
            "code": "state-value-mismatch",
            "from_stage": "P1",
            "to_stage": "P2",
            "state_key": "velocity",
            "message": "mismatch",
            "semantic_required": False,
        }
        original = copy.deepcopy(issue)
        ticket = cognitive_loop.challenge_from_interface_issue(
            issue,
            challenge_id="CH1",
            snapshot_version=2,
            claim_ids=["C1", "C2"],
            suggested_backjump="C1",
        )
        self.assertEqual(ticket["trigger"], "interface-mismatch")
        self.assertIn("velocity", ticket["specific_doubt"])
        self.assertEqual(issue, original)


def graph_claim(claim_id, depends_on=None):
    return {
        "id": claim_id,
        "version": 1,
        "kind": "derived",
        "statement": f"Claim {claim_id} statement",
        "target_ids": ["Q1"],
        "stage_ids": [],
        "depends_on": depends_on or [],
        "conditions": [],
        "obligation_ids": [],
        "check_spec": {"type": "semantic-required"},
        "status": "candidate",
        "source": {
            "task_id": f"build-{claim_id}",
            "input_fingerprint": "e" * 64,
            "policy_version": "claim-ledger-v1",
        },
    }


class ConflictDiagnosisTest(unittest.TestCase):
    def claims(self):
        return [
            graph_claim("C1"),
            graph_claim("C2", ["C1"]),
            graph_claim("C3", ["C2"]),
        ]

    def challenge(self):
        return {
            "id": "CH1",
            "snapshot_version": 2,
            "trigger": "verification-conflict",
            "claim_ids": ["C1", "C2", "C3"],
            "specific_doubt": "Two downstream Claims fail compatible checks.",
            "falsification_test": "Recompute the earliest failing dependency in isolation.",
            "suggested_backjump": "C1",
            "status": "candidate",
        }

    def evidence(self, **updates):
        result: dict[str, Any] = {
            "interface_issue_codes": [],
            "event_order_claim_ids": [],
            "missing_obligation_ids": [],
            "missing_target_ids": [],
            "conflicting_claim_ids": [],
            "component_failure_claim_ids": [],
        }
        result.update(updates)
        return result

    def test_reduces_downstream_failures_to_root_most_conflict(self):
        diagnosis = cognitive_loop.diagnose_minimal_conflict(
            self.challenge(),
            self.claims(),
            self.evidence(component_failure_claim_ids=["C2", "C3"]),
        )
        self.assertEqual(diagnosis["conflict_class"], "component")
        self.assertEqual(diagnosis["minimal_conflict_claim_ids"], ["C2"])
        self.assertEqual(diagnosis["suggested_backjump"], "C2")

    def test_distinguishes_order_interface_condition_and_decomposition(self):
        cases = [
            (
                self.evidence(event_order_claim_ids=["C3"]),
                "order",
            ),
            (
                self.evidence(interface_issue_codes=["direction_mismatch"]),
                "interface",
            ),
            (
                self.evidence(missing_obligation_ids=["V1"]),
                "omitted-condition",
            ),
            (
                self.evidence(missing_target_ids=["Q2"]),
                "decomposition",
            ),
        ]
        for evidence, expected in cases:
            with self.subTest(expected=expected):
                diagnosis = cognitive_loop.diagnose_minimal_conflict(self.challenge(), self.claims(), evidence)
                self.assertEqual(diagnosis["conflict_class"], expected)

    def test_rejects_evidence_outside_challenge_binding(self):
        with self.assertRaisesRegex(ValueError, "outside the challenge"):
            cognitive_loop.diagnose_minimal_conflict(
                self.challenge(),
                [*self.claims(), graph_claim("C4")],
                self.evidence(conflicting_claim_ids=["C4"]),
            )


class DependencyBackjumpTest(unittest.TestCase):
    def claims(self):
        return [
            graph_claim("C1"),
            graph_claim("C2", ["C1"]),
            graph_claim("C3", ["C2"]),
            graph_claim("C4"),
            graph_claim("C5", ["C4"]),
        ]

    def certificate(self, claim):
        dependencies = [item for item in self.claims() if item["id"] in claim["depends_on"]]
        return {
            "claim_id": claim["id"],
            "claim_version": claim["version"],
            "verifier_kind": "independent-agent",
            "check_type": "semantic",
            "verdict": "pass",
            "normalized_result": "pass",
            "decisive_checks": ["isolated recomputation"],
            "input_fingerprint": (claim_ledger.claim_verification_input_fingerprint(claim, dependencies)),
            "verifier_identity": {
                "model_id": f"verifier-{claim['id']}",
                "provider": "test",
                "context_isolated": True,
            },
        }

    def test_invalidates_only_root_and_true_downstream(self):
        claims = self.claims()
        certificates = [self.certificate(item) for item in claims]
        plan = cognitive_loop.invalidate_dependency_cone(
            claims,
            certificates,
            root_claim_ids={"C2"},
            snapshot_version=4,
            challenge_id="CH1",
        )
        self.assertEqual(plan["affected_claim_ids"], ["C2", "C3"])
        self.assertEqual(plan["preserved_claim_ids"], ["C1", "C4", "C5"])
        self.assertEqual(plan["next_snapshot_version"], 5)
        self.assertTrue(all(item["status"] == "disputed" for item in plan["invalidated_claims"]))
        self.assertEqual(
            {item["claim_id"] for item in plan["preserved_certificates"]},
            {"C1", "C4", "C5"},
        )
        self.assertEqual(
            [item["action"] for item in plan["rebuild_tasks"]],
            ["rebuild-root", "rebuild-downstream"],
        )
        self.assertEqual(
            [item["next_version"] for item in plan["rebuild_tasks"]],
            [2, 2],
        )

    def test_backjump_rejects_unknown_or_empty_root(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            cognitive_loop.invalidate_dependency_cone(
                self.claims(),
                [],
                root_claim_ids=set(),
                snapshot_version=1,
                challenge_id="CH1",
            )
        with self.assertRaisesRegex(ValueError, "roots are unknown"):
            cognitive_loop.invalidate_dependency_cone(
                self.claims(),
                [],
                root_claim_ids={"C99"},
                snapshot_version=1,
                challenge_id="CH1",
            )


def atomic_task(task_id="verify-C1", status="pending", strategy="semantic"):
    return {
        "task_id": task_id,
        "action": "verify_claim",
        "target_ids": ["C1"],
        "input_snapshot": 1,
        "input_fingerprint": "f" * 64,
        "output_contract": "wuli.claim-verify.v1",
        "strategy": strategy,
        "random_seed": None,
        "status": status,
    }


def progress_state(**updates):
    state = {
        "verified_claim_count": 0,
        "verified_critical_claim_count": 0,
        "accepted_certificate_count": 0,
        "closed_obligation_count": 0,
        "localized_conflict_count": 0,
        "open_conflict_scope_size": 3,
        "novel_hypothesis_count": 0,
    }
    state.update(updates)
    return state


class LoopControlTest(unittest.TestCase):
    def test_identical_task_fingerprint_allows_zero_repeat_calls(self):
        completed = atomic_task("old-task", "completed")
        duplicate = atomic_task("renamed-task", "pending")
        decision = cognitive_loop.deduplicate_atomic_task(duplicate, [completed])
        self.assertEqual(decision["decision"], "reuse")
        self.assertFalse(decision["provider_call_allowed"])

        distinct = atomic_task("new-strategy", "pending", "counterexample")
        decision = cognitive_loop.deduplicate_atomic_task(distinct, [completed])
        self.assertEqual(decision["decision"], "execute")
        self.assertTrue(decision["provider_call_allowed"])

    def test_progress_counts_evidence_and_conflict_reduction_not_iterations(self):
        before = progress_state()
        after = progress_state(
            accepted_certificate_count=1,
            localized_conflict_count=1,
            open_conflict_scope_size=1,
        )
        assessment = cognitive_loop.evaluate_progress(before, after)
        self.assertTrue(assessment["progressed"])
        self.assertIn("accepted_certificate_count", assessment["improvements"])
        self.assertIn("open_conflict_scope_reduced", assessment["improvements"])

        unchanged = cognitive_loop.evaluate_progress(before, before)
        self.assertFalse(unchanged["progressed"])

    def test_stagnation_switches_strategy_instead_of_repeating(self):
        control = cognitive_loop.initial_loop_control()
        no_progress = {"progressed": False}
        first = cognitive_loop.advance_loop_control(
            control,
            no_progress,
            evidence_status="PROVISIONAL",
            has_open_conflict=False,
        )
        self.assertEqual(first["action"], "continue-no-retry")
        self.assertFalse(first["provider_call_allowed"])
        second = cognitive_loop.advance_loop_control(
            first["control"],
            no_progress,
            evidence_status="PROVISIONAL",
            has_open_conflict=False,
        )
        self.assertEqual(second["action"], "switch-strategy")
        self.assertEqual(second["control"]["strategy_index"], 1)

    def test_hard_fuse_never_manufactures_verified(self):
        control = cognitive_loop.initial_loop_control()
        control["transition_count"] = 2
        result = cognitive_loop.advance_loop_control(
            control,
            {"progressed": True},
            evidence_status="UNRESOLVED",
            has_open_conflict=True,
            policy={
                "stagnation_before_strategy_change": 2,
                "strategy_count": 3,
                "max_transitions": 3,
            },
        )
        self.assertEqual(result["action"], "hard-fuse")
        self.assertEqual(result["control"]["terminal_status"], "UNRESOLVED")
        self.assertFalse(result["provider_call_allowed"])

    def test_only_verified_evidence_can_stop_as_verified(self):
        result = cognitive_loop.advance_loop_control(
            cognitive_loop.initial_loop_control(),
            {"progressed": False},
            evidence_status="VERIFIED",
            has_open_conflict=False,
        )
        self.assertEqual(result["control"]["terminal_status"], "VERIFIED")
        self.assertEqual(result["action"], "stop-verified")


class HypothesisPoolTest(unittest.TestCase):
    def claims(self):
        return [graph_claim("C1"), graph_claim("C2", ["C1"])]

    def challenge(self):
        return {
            "id": "CH1",
            "snapshot_version": 2,
            "trigger": "risk-audit",
            "claim_ids": ["C1", "C2"],
            "specific_doubt": "The current solution may omit a direction branch.",
            "falsification_test": "Enumerate both velocity directions and compare outcomes.",
            "suggested_backjump": "C1",
            "status": "candidate",
        }

    def hypothesis(self):
        return {
            "id": "H1",
            "snapshot_version": 2,
            "challenge_id": "CH1",
            "operator": "hidden-degree-of-freedom",
            "proposal": "The initial velocity sign creates a second admissible branch.",
            "explains_gap": "It explains why the current all-solutions Claim has one branch.",
            "novelty_basis": "No current Claim varies the initial velocity direction.",
            "falsification": {
                "test_type": "deterministic",
                "procedure": "Enumerate both signs and propagate each state to the boundary.",
                "expected_observation": "A second sign satisfies every source condition.",
                "failure_observation": "The second sign violates at least one source condition.",
            },
            "affected_claim_ids": ["C1", "C2"],
            "status": "candidate",
        }

    def test_all_controlled_association_operators_are_challenge_bound(self):
        probes = cognitive_loop.operator_probes(self.challenge(), self.claims())
        self.assertEqual(
            {item["operator"] for item in probes},
            set(cognitive_loop.HYPOTHESIS_OPERATORS),
        )
        self.assertTrue(all(item["challenge_id"] == "CH1" for item in probes))
        self.assertTrue(all(item["required_output"] for item in probes))

    def test_relevant_novel_falsifiable_hypothesis_enters_isolated_pool(self):
        claims = self.claims()
        original_claims = copy.deepcopy(claims)
        result = cognitive_loop.add_hypothesis_to_pool(self.hypothesis(), self.challenge(), claims, [])
        self.assertEqual(result["decision"], "accepted")
        self.assertEqual(result["pool"][0]["status"], "candidate")
        self.assertEqual(claims, original_claims)

    def test_duplicate_or_existing_claim_restatement_is_rejected(self):
        candidate = self.hypothesis()
        duplicate = cognitive_loop.add_hypothesis_to_pool(candidate, self.challenge(), self.claims(), [candidate])
        self.assertEqual(duplicate["decision"], "rejected")
        self.assertIn("duplicates", " ".join(duplicate["reasons"]))

        restatement = self.hypothesis()
        restatement["proposal"] = "Claim C1 statement"
        result = cognitive_loop.add_hypothesis_to_pool(restatement, self.challenge(), self.claims(), [])
        self.assertEqual(result["decision"], "rejected")
        self.assertIn("repeats", " ".join(result["reasons"]))

    def test_vague_or_self_promoted_hypothesis_is_rejected_by_contract(self):
        vague = self.hypothesis()
        vague["novelty_basis"] = "不确定"
        with self.assertRaisesRegex(ValueError, "specific and testable"):
            cognitive_loop.add_hypothesis_to_pool(vague, self.challenge(), self.claims(), [])
        promoted = self.hypothesis()
        promoted["status"] = "promoted"
        with self.assertRaisesRegex(ValueError, "must be candidate"):
            cognitive_loop.add_hypothesis_to_pool(promoted, self.challenge(), self.claims(), [])

    def test_risk_weighted_selection_is_replayable_and_never_promotes(self):
        first = self.hypothesis()
        second = copy.deepcopy(first)
        second["id"] = "H2"
        second["operator"] = "event-reordering"
        second["proposal"] = "A previously ignored boundary crossing occurs first."
        second["novelty_basis"] = "It changes event order rather than velocity direction."
        original = [copy.deepcopy(first), copy.deepcopy(second)]
        arguments: dict[str, Any] = {
            "conflict_class": "order",
            "claim_risks": {"C1": 0.9, "C2": 0.8},
            "operator_stats": {
                "hidden-degree-of-freedom": {
                    "attempt_count": 4,
                    "conflict_found_count": 1,
                    "certificate_gain_count": 1,
                },
                "event-reordering": {
                    "attempt_count": 3,
                    "conflict_found_count": 2,
                    "certificate_gain_count": 1,
                },
            },
            "random_seed": 20260729,
            "exploration_rate": 0.1,
        }
        selected_a = cognitive_loop.select_hypothesis([first, second], **arguments)
        selected_b = cognitive_loop.select_hypothesis([first, second], **arguments)
        self.assertEqual(selected_a, selected_b)
        self.assertEqual(selected_a["truth_status"], "candidate")
        self.assertEqual([first, second], original)

    def test_fixed_seed_uniform_exploration_is_replayable(self):
        first = self.hypothesis()
        second = copy.deepcopy(first)
        second["id"] = "H2"
        second["operator"] = "counterexample"
        second["proposal"] = "One admissible sign choice falsifies uniqueness."
        second["novelty_basis"] = "It seeks a counterexample instead of a hidden variable."
        result = cognitive_loop.select_hypothesis(
            [first, second],
            conflict_class="component",
            claim_risks={},
            operator_stats={},
            random_seed=17,
            exploration_rate=1.0,
        )
        replay = cognitive_loop.select_hypothesis(
            [first, second],
            conflict_class="component",
            claim_risks={},
            operator_stats={},
            random_seed=17,
            exploration_rate=1.0,
        )
        self.assertEqual(result["mode"], "uniform-exploration")
        self.assertEqual(
            result["selected_hypothesis_id"],
            replay["selected_hypothesis_id"],
        )
        self.assertEqual(
            result["selection_fingerprint"],
            replay["selection_fingerprint"],
        )

    def test_score_uses_external_claim_risk_not_agent_confidence(self):
        candidate = self.hypothesis()
        low = cognitive_loop.score_hypothesis(
            candidate,
            conflict_class="component",
            claim_risks={"C1": 0.1, "C2": 0.2},
            operator_stats={},
        )
        high = cognitive_loop.score_hypothesis(
            candidate,
            conflict_class="component",
            claim_risks={"C1": 0.9, "C2": 0.8},
            operator_stats={},
        )
        self.assertGreater(high["score"], low["score"])


class BoundedCognitiveLoopGateTest(unittest.TestCase):
    def test_random_dag_impact_cones_match_naive_reachability(self):
        rng = random.Random(20260729)
        claims = []
        downstream: dict[str, set[str]] = {f"C{index}": set() for index in range(30)}
        for index in range(30):
            claim_id = f"C{index}"
            dependencies = [f"C{parent}" for parent in range(index) if rng.random() < 0.08]
            claims.append(graph_claim(claim_id, dependencies))
            for parent_id in dependencies:
                downstream[parent_id].add(claim_id)

        for root_index in (0, 3, 7, 13, 21):
            root_id = f"C{root_index}"
            expected = set()
            frontier = [root_id]
            while frontier:
                parent_id = frontier.pop()
                for child_id in downstream[parent_id]:
                    if child_id not in expected:
                        expected.add(child_id)
                        frontier.append(child_id)
            actual = set(claim_ledger.dependency_impact_cone(claims, {root_id}))
            self.assertEqual(actual, expected)

    def test_persistent_conflict_exhausts_distinct_strategies_not_forever(self):
        control = cognitive_loop.initial_loop_control()
        actions = []
        for _ in range(20):
            result = cognitive_loop.advance_loop_control(
                control,
                {"progressed": False},
                evidence_status="UNRESOLVED",
                has_open_conflict=True,
                policy={
                    "stagnation_before_strategy_change": 2,
                    "strategy_count": 3,
                    "max_transitions": 20,
                },
            )
            actions.append(result["action"])
            control = result["control"]
            if control["terminal_status"] != "ACTIVE":
                break
        self.assertEqual(control["terminal_status"], "UNRESOLVED")
        self.assertIn("switch-strategy", actions)
        self.assertEqual(actions[-1], "strategy-fuse")
        self.assertNotIn("stop-verified", actions)
        self.assertLess(len(actions), 20)

    def test_repeated_hypothesis_candidates_do_not_grow_pool(self):
        helper = HypothesisPoolTest()
        candidate = helper.hypothesis()
        challenge = helper.challenge()
        claims = helper.claims()
        first = cognitive_loop.add_hypothesis_to_pool(candidate, challenge, claims, [])
        pool = first["pool"]
        for _ in range(20):
            repeated = cognitive_loop.add_hypothesis_to_pool(candidate, challenge, claims, pool)
            self.assertEqual(repeated["decision"], "rejected")
            self.assertEqual(len(repeated["pool"]), 1)
            pool = repeated["pool"]


if __name__ == "__main__":
    unittest.main()
