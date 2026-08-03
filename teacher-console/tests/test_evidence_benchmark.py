import json
import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

import evidence_benchmark  # noqa: E402
import evidence_evaluation  # noqa: E402


FIXTURE = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "calibration-curated-v1.json"
)
APPROVED_DATASET = (
    ROOT
    / "student-error-library"
    / "evals"
    / "evidence-gold-calibration.json"
)
FULL_PAIRED_REPORT = (
    ROOT
    / "docs"
    / "reports"
    / "evidence-agent-mvp-d-paired-v1.json"
)
HOLDOUT_PAIRED_REPORT = (
    ROOT
    / "docs"
    / "reports"
    / "evidence-agent-mvp-e-holdout-paired-v1.json"
)
HOLDOUT_FIXTURE = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "holdout-curated-v1.json"
)
CURRENT_HOLDOUT_FIXTURE = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "holdout-curated-v3.json"
)
REPAIR_REPLAY_FIXTURE = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "repair-replay-v1.json"
)
FRESH_V2_FIXTURE = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "fresh-holdout-v2-v2.json"
)
FRESH_V2_OVERLAY = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "fresh-holdout-v2-evidence-overlay.json"
)
FRESH_V3_FIXTURE = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "fresh-holdout-v3.json"
)
FRESH_V3_OVERLAY = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "fresh-holdout-v3-evidence-overlay.json"
)
FRESH_V3_PREFLIGHT = (
    ROOT
    / "docs"
    / "reports"
    / "evidence-agent-mvp-h-third-fresh-holdout-preflight-v1.json"
)

WARNING_SCOPE_REPLAY_SCRIPT = (
    CONSOLE / "scripts" / "build_evidence_warning_scope_replay.py"
)
WARNING_SCOPE_REPLAY_FIXTURE = (
    CONSOLE
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "warning-scope-repair-replay-v1.json"
)
WARNING_SCOPE_REPLAY_REPORT = (
    ROOT
    / "docs"
    / "reports"
    / "evidence-agent-mvp-g2-warning-scope-repair-replay-v1.json"
)


class EvidenceBenchmarkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.normalized = evidence_evaluation.normalize_gold_dataset(cls.dataset)

    def test_curated_calibration_is_fingerprinted_but_not_teacher_approved(self):
        self.assertEqual(len(self.normalized["cases"]), 8)
        self.assertEqual(
            self.normalized["dataset_fingerprint"],
            "sha256:13f95f9534387a02bd1e9e17c981a788d8c7498f01079e39297797b3d2fe429f",
        )
        self.assertEqual(self.normalized["review_status"], "calibration_frozen")
        self.assertEqual(self.normalized["source_scope"], ["curated_technique"])

    def test_top_k_baseline_admits_ranked_false_friend_without_semantic_gate(self):
        gold = self.normalized["cases"][1]["gold_case"]
        prediction = evidence_benchmark.baseline_prediction(
            gold,
            {
                "queries": [
                    {
                        "need_id": "N1",
                        "candidate_evidence_ids": [
                            "EU-277d79cd61babffd9055b89c"
                        ],
                    }
                ]
            },
        )
        self.assertEqual(prediction["status"], "sufficient")
        self.assertEqual(
            prediction["selected_evidence_ids"],
            ["EU-277d79cd61babffd9055b89c"],
        )

    def test_paired_improvement_cannot_open_production_gate_on_calibration(self):
        baseline = []
        agent = []
        for item in self.normalized["cases"]:
            gold = item["gold_case"]
            required = gold["required_evidence_ids"]
            forbidden = gold["forbidden_evidence_ids"]
            baseline_selected = [*required, *forbidden]
            baseline.append(
                {
                    "case_id": gold["case_id"],
                    "status": "sufficient",
                    "candidate_evidence_ids": baseline_selected,
                    "selected_evidence_ids": baseline_selected,
                    "traceable_evidence_ids": baseline_selected,
                }
            )
            agent.append(
                {
                    "case_id": gold["case_id"],
                    "status": gold["expected_status"],
                    "candidate_evidence_ids": baseline_selected,
                    "selected_evidence_ids": required,
                    "traceable_evidence_ids": required,
                }
            )
        report = evidence_evaluation.paired_gold_report(
            self.dataset, baseline, agent
        )
        self.assertEqual(
            report["evidence_agent"]["metrics"]["false_friend_admission_count"],
            0,
        )
        self.assertGreater(report["metric_deltas"]["status_accuracy"], 0)
        self.assertFalse(report["gates"]["dataset_teacher_approved"])
        self.assertFalse(report["gates"]["independent_holdout_present"])
        self.assertFalse(report["gates"]["eligible_for_production"])

    def review(self, *, decision="approved"):
        return {
            "schema": "wuli.evidence-gold-review.v1",
            "dataset_id": self.normalized["dataset_id"],
            "dataset_fingerprint": self.normalized["dataset_fingerprint"],
            "reviewer": "物理教师",
            "reviewed_at": "2026-07-30T12:00:00+08:00",
            "status": (
                "approved" if decision == "approved" else "changes_requested"
            ),
            "decisions": [
                {
                    "case_id": item["gold_case"]["case_id"],
                    "decision": decision,
                    "note": "" if decision == "approved" else "请修改标签",
                }
                for item in self.normalized["cases"]
            ],
        }

    def test_browser_review_promotes_only_complete_matching_approval(self):
        approved = evidence_evaluation.apply_gold_review(
            self.dataset, self.review()
        )
        self.assertEqual(approved["review_status"], "teacher_approved")
        self.assertEqual(approved["label_origin"], "teacher_reviewed")
        self.assertEqual(approved["reviewer"], "物理教师")
        self.assertEqual(
            approved["dataset_fingerprint"],
            self.normalized["dataset_fingerprint"],
        )

    def test_review_rejects_wrong_fingerprint_missing_case_and_change_request(self):
        wrong = self.review()
        wrong["dataset_fingerprint"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            evidence_evaluation.apply_gold_review(self.dataset, wrong)

        missing = self.review()
        missing["decisions"].pop()
        with self.assertRaisesRegex(ValueError, "each known case"):
            evidence_evaluation.apply_gold_review(self.dataset, missing)

        changes = self.review(decision="changes_requested")
        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            evidence_evaluation.apply_gold_review(self.dataset, changes)

        missing_note = deepcopy(changes)
        missing_note["decisions"][0]["note"] = ""
        with self.assertRaisesRegex(ValueError, "requires a note"):
            evidence_evaluation.normalize_gold_review(
                missing_note, self.dataset
            )

    def test_saved_teacher_approval_and_live_paired_report_keep_holdout_gate_closed(self):
        approved = evidence_evaluation.normalize_gold_dataset(
            json.loads(APPROVED_DATASET.read_text(encoding="utf-8"))
        )
        report = json.loads(FULL_PAIRED_REPORT.read_text(encoding="utf-8"))
        paired = report["paired"]

        self.assertEqual(approved["review_status"], "teacher_approved")
        self.assertEqual(approved["label_origin"], "teacher_reviewed")
        self.assertEqual(len(approved["cases"]), 8)
        self.assertEqual(
            approved["dataset_fingerprint"],
            self.normalized["dataset_fingerprint"],
        )
        self.assertEqual(
            paired["dataset"]["dataset_fingerprint"],
            approved["dataset_fingerprint"],
        )
        self.assertEqual(
            paired["evidence_agent"]["metrics"]["evidence_precision"], 1.0
        )
        self.assertEqual(
            paired["evidence_agent"]["metrics"][
                "false_friend_admission_count"
            ],
            0,
        )
        self.assertEqual(
            paired["evidence_agent"]["metrics"]["status_accuracy"], 1.0
        )
        self.assertTrue(paired["gates"]["dataset_teacher_approved"])
        self.assertFalse(paired["gates"]["independent_holdout_present"])
        self.assertFalse(paired["gates"]["eligible_for_production"])

    def test_fresh_holdout_has_twenty_cases_and_no_calibration_evidence_units(self):
        holdout = evidence_evaluation.normalize_gold_dataset(
            json.loads(CURRENT_HOLDOUT_FIXTURE.read_text(encoding="utf-8"))
        )
        calibration_ids = {
            evidence_id
            for item in self.normalized["cases"]
            for field in (
                "required_evidence_ids",
                "acceptable_evidence_ids",
                "forbidden_evidence_ids",
            )
            for evidence_id in item["gold_case"][field]
        }
        holdout_ids = {
            evidence_id
            for item in holdout["cases"]
            for field in (
                "required_evidence_ids",
                "acceptable_evidence_ids",
                "forbidden_evidence_ids",
            )
            for evidence_id in item["gold_case"][field]
        }
        self.assertEqual(len(holdout["cases"]), 20)
        self.assertEqual(holdout["review_status"], "draft")
        self.assertEqual(holdout["label_origin"], "agent_proposed_holdout")
        self.assertFalse(calibration_ids & holdout_ids)
        self.assertEqual(
            {
                item["gold_case"]["evaluation_split"]
                for item in holdout["cases"]
            },
            {"holdout"},
        )
        self.assertEqual(
            {item["gold_case"]["batch_id"] for item in holdout["cases"]},
            {"evidence-holdout-2026-07-30-a"},
        )
        statuses = [
            item["gold_case"]["expected_status"] for item in holdout["cases"]
        ]
        self.assertEqual(statuses.count("sufficient"), 11)
        self.assertEqual(statuses.count("insufficient"), 9)

    def test_teacher_feedback_cases_treat_corrective_evidence_as_usable(self):
        holdout = evidence_evaluation.normalize_gold_dataset(
            json.loads(CURRENT_HOLDOUT_FIXTURE.read_text(encoding="utf-8"))
        )
        cases = {
            item["gold_case"]["case_id"]: item["gold_case"]
            for item in holdout["cases"]
        }
        for case_id in (
            "holdout-lorentz-right-hand-conflict",
            "holdout-angle-ledger-sign-conflict",
        ):
            gold = cases[case_id]
            self.assertEqual(gold["expected_status"], "sufficient")
            self.assertTrue(gold["required_evidence_ids"])
            self.assertFalse(gold["forbidden_evidence_ids"])

        rejected = cases["holdout-lorentz-positive-charge"]
        self.assertEqual(rejected["expected_status"], "insufficient")
        self.assertFalse(rejected["required_evidence_ids"])
        self.assertEqual(
            rejected["forbidden_evidence_ids"],
            ["EU-0da542351f084b059f89b89a"],
        )

    def test_saved_independent_holdout_failure_cannot_open_production_gate(self):
        report = json.loads(
            HOLDOUT_PAIRED_REPORT.read_text(encoding="utf-8")
        )
        paired = report["paired"]
        metrics = paired["evidence_agent"]["metrics"]
        self.assertEqual(paired["dataset"]["case_count"], 20)
        self.assertEqual(
            paired["dataset"]["review_status"], "teacher_approved"
        )
        self.assertTrue(paired["gates"]["independent_holdout_present"])
        self.assertEqual(metrics["candidate_required_recall"], 1.0)
        self.assertEqual(metrics["evidence_precision"], 1.0)
        self.assertEqual(metrics["false_friend_admission_count"], 0)
        self.assertEqual(metrics["gold_evidence_retention"], 0.8182)
        self.assertEqual(metrics["required_need_coverage"], 0.8182)
        self.assertEqual(metrics["status_accuracy"], 0.85)
        self.assertFalse(paired["gates"]["gold_retention_non_regression"])
        self.assertFalse(paired["gates"]["eligible_for_production"])

        failures = {
            item["case_id"]: item["predicted_status"]
            for item in paired["evidence_agent"]["cases"]
            if item["expected_status"] != item["predicted_status"]
        }
        self.assertEqual(
            failures,
            {
                "holdout-angle-ledger-sign-conflict": "insufficient",
                "holdout-angle-ledger-position-conflict": "unavailable",
                "holdout-circuit-path-valid": "insufficient",
            },
        )
        provider_failures = [
            item
            for item in report["executions"]
            if item["gateway"] and item["gateway"]["status"] == "failed"
        ]
        self.assertEqual(len(provider_failures), 1)
        self.assertEqual(
            provider_failures[0]["gateway"]["failure_type"],
            "adapter_protocol_error",
        )

    def test_repair_replay_is_explicitly_calibration_only(self):
        replay = evidence_evaluation.normalize_gold_dataset(
            json.loads(REPAIR_REPLAY_FIXTURE.read_text(encoding="utf-8"))
        )
        self.assertEqual(replay["review_status"], "draft")
        self.assertEqual(
            replay["label_origin"], "post_holdout_repair_replay"
        )
        self.assertEqual(len(replay["cases"]), 3)
        self.assertEqual(
            {
                item["gold_case"]["evaluation_split"]
                for item in replay["cases"]
            },
            {"calibration"},
        )
        self.assertEqual(
            {item["gold_case"]["batch_id"] for item in replay["cases"]},
            {""},
        )

    def test_second_fresh_holdout_uses_only_isolated_new_evidence(self):
        fresh = evidence_evaluation.normalize_gold_dataset(
            json.loads(FRESH_V2_FIXTURE.read_text(encoding="utf-8"))
        )
        overlay = json.loads(FRESH_V2_OVERLAY.read_text(encoding="utf-8"))
        revealed_ids = set()
        for path in (APPROVED_DATASET, ROOT / "student-error-library/evals/evidence-gold-holdout.json", REPAIR_REPLAY_FIXTURE):
            prior = evidence_evaluation.normalize_gold_dataset(
                json.loads(path.read_text(encoding="utf-8"))
            )
            for item in prior["cases"]:
                for field in (
                    "required_evidence_ids",
                    "acceptable_evidence_ids",
                    "forbidden_evidence_ids",
                ):
                    revealed_ids.update(item["gold_case"][field])
        fresh_ids = {
            evidence_id
            for item in fresh["cases"]
            for field in (
                "required_evidence_ids",
                "acceptable_evidence_ids",
                "forbidden_evidence_ids",
            )
            for evidence_id in item["gold_case"][field]
        }
        overlay_ids = {item["evidence_id"] for item in overlay["units"]}
        self.assertEqual(len(fresh["cases"]), 21)
        self.assertEqual(fresh["review_status"], "draft")
        self.assertEqual(
            fresh["label_origin"], "agent_proposed_fresh_holdout_v2"
        )
        self.assertFalse(revealed_ids & fresh_ids)
        self.assertEqual(fresh_ids, overlay_ids)
        self.assertEqual(
            fresh["evidence_snapshot_fingerprint"],
            overlay["overlay_fingerprint"],
        )
        self.assertEqual(
            {
                item["gold_case"]["evaluation_split"]
                for item in fresh["cases"]
            },
            {"holdout"},
        )
        self.assertEqual(
            {item["gold_case"]["batch_id"] for item in fresh["cases"]},
            {"evidence-holdout-2026-07-30-b"},
        )
        cases = {
            item["gold_case"]["case_id"]: item["gold_case"]
            for item in fresh["cases"]
        }
        for case_id in (
            "fresh2-interference-frequency-conflict",
            "fresh2-photo-below-threshold-conflict",
        ):
            gold = cases[case_id]
            self.assertEqual(gold["expected_status"], "sufficient")
            self.assertEqual(
                gold["retrieval_need"]["purpose"], "false_friend_check"
            )
            self.assertTrue(gold["retrieval_need"]["diagnostic_targets"])
            self.assertTrue(gold["required_evidence_ids"])
            self.assertFalse(gold["forbidden_evidence_ids"])

    def test_second_fresh_holdout_requires_its_exact_overlay(self):
        fresh = json.loads(FRESH_V2_FIXTURE.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "requires an evidence overlay"):
            evidence_benchmark.run_paired_benchmark(
                fresh,
                library_root=ROOT / "student-error-library",
                gateway=None,
            )
        with self.assertRaisesRegex(ValueError, "does not match overlay"):
            evidence_benchmark.run_paired_benchmark(
                fresh,
                library_root=ROOT / "student-error-library",
                gateway=None,
                projection_overlay=[],
            )

    def test_fresh_holdout_approval_promotes_bound_overlay_atomically(self):
        fresh = json.loads(FRESH_V2_FIXTURE.read_text(encoding="utf-8"))
        overlay = json.loads(FRESH_V2_OVERLAY.read_text(encoding="utf-8"))
        normalized = evidence_evaluation.normalize_gold_dataset(fresh)
        review = {
            "schema": "wuli.evidence-gold-review.v1",
            "dataset_id": normalized["dataset_id"],
            "dataset_fingerprint": normalized["dataset_fingerprint"],
            "reviewer": "物理教师",
            "reviewed_at": "2026-07-30T15:39:19.447Z",
            "status": "approved",
            "decisions": [
                {
                    "case_id": item["gold_case"]["case_id"],
                    "decision": "approved",
                    "note": "",
                }
                for item in normalized["cases"]
            ],
        }
        approved, receipt, approved_overlay = (
            evidence_evaluation.apply_gold_review_with_overlay(
                fresh, review, overlay
            )
        )
        self.assertEqual(approved["review_status"], "teacher_approved")
        self.assertEqual(receipt["status"], "approved")
        self.assertEqual(
            approved_overlay["review_status"], "teacher_approved"
        )
        self.assertEqual(
            approved["evidence_snapshot_fingerprint"],
            approved_overlay["overlay_fingerprint"],
        )

        wrong_overlay = deepcopy(overlay)
        wrong_overlay["overlay_id"] = "wrong-overlay"
        with self.assertRaisesRegex(ValueError, "id does not match"):
            evidence_evaluation.apply_gold_review_with_overlay(
                fresh, review, wrong_overlay
            )

    def test_warning_scope_replay_is_calibration_only_and_fingerprint_bound(self):
        spec = importlib.util.spec_from_file_location(
            "build_evidence_warning_scope_replay",
            WARNING_SCOPE_REPLAY_SCRIPT,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        source = json.loads(
            (ROOT / "student-error-library/evals/evidence-gold-holdout-v2.json")
            .read_text(encoding="utf-8")
        )
        overlay = json.loads(
            (ROOT / "student-error-library/evals/evidence-gold-holdout-v2-overlay.json")
            .read_text(encoding="utf-8")
        )
        replay = module.build(source, overlay)
        gold = replay["cases"][0]["gold_case"]
        need = gold["retrieval_need"]
        self.assertEqual(replay["review_status"], "draft")
        self.assertEqual(replay["label_origin"], "post_holdout_v2_repair_replay")
        self.assertEqual(gold["evaluation_split"], "calibration")
        self.assertEqual(gold["batch_id"], "")
        self.assertEqual(gold["case_id"], "fresh2-relative-endpoint-valid")
        self.assertEqual(need["forbidden_conflicts"], [])
        self.assertEqual(
            need["diagnostic_targets"],
            ["求得的垂直时刻不在区间内却不检查端点"],
        )
        self.assertEqual(
            replay["evidence_snapshot_fingerprint"],
            overlay["overlay_fingerprint"],
        )

    def test_saved_warning_scope_replay_closes_only_the_known_gap(self):
        replay = evidence_evaluation.normalize_gold_dataset(
            json.loads(WARNING_SCOPE_REPLAY_FIXTURE.read_text(encoding="utf-8"))
        )
        report = json.loads(
            WARNING_SCOPE_REPLAY_REPORT.read_text(encoding="utf-8")
        )
        paired = report["paired"]
        self.assertEqual(replay["review_status"], "draft")
        self.assertEqual(
            replay["label_origin"], "post_holdout_v2_repair_replay"
        )
        self.assertEqual(
            paired["dataset"]["dataset_fingerprint"],
            replay["dataset_fingerprint"],
        )
        self.assertEqual(
            paired["evidence_agent"]["metrics"]["gold_evidence_retention"],
            1.0,
        )
        self.assertEqual(
            paired["evidence_agent"]["metrics"]["evidence_precision"], 1.0
        )
        self.assertEqual(
            paired["evidence_agent"]["metrics"]["status_accuracy"], 1.0
        )
        self.assertFalse(paired["gates"]["dataset_teacher_approved"])
        self.assertFalse(paired["gates"]["independent_holdout_present"])
        self.assertFalse(paired["gates"]["eligible_for_production"])

    def test_third_fresh_holdout_is_unrevealed_isolated_and_preflight_only(self):
        fresh = evidence_evaluation.normalize_gold_dataset(
            json.loads(FRESH_V3_FIXTURE.read_text(encoding="utf-8"))
        )
        overlay = evidence_evaluation.normalize_evidence_overlay(
            json.loads(FRESH_V3_OVERLAY.read_text(encoding="utf-8"))
        )
        preflight = json.loads(
            FRESH_V3_PREFLIGHT.read_text(encoding="utf-8")
        )
        revealed_ids = set()
        for path in (
            APPROVED_DATASET,
            ROOT / "student-error-library/evals/evidence-gold-holdout.json",
            REPAIR_REPLAY_FIXTURE,
            ROOT / "student-error-library/evals/evidence-gold-holdout-v2.json",
            WARNING_SCOPE_REPLAY_FIXTURE,
        ):
            prior = evidence_evaluation.normalize_gold_dataset(
                json.loads(path.read_text(encoding="utf-8"))
            )
            for item in prior["cases"]:
                for field in (
                    "required_evidence_ids",
                    "acceptable_evidence_ids",
                    "forbidden_evidence_ids",
                ):
                    revealed_ids.update(item["gold_case"][field])
        fresh_ids = {
            evidence_id
            for item in fresh["cases"]
            for field in (
                "required_evidence_ids",
                "acceptable_evidence_ids",
                "forbidden_evidence_ids",
            )
            for evidence_id in item["gold_case"][field]
        }
        overlay_ids = {item["evidence_id"] for item in overlay["units"]}
        self.assertEqual(len(fresh["cases"]), 21)
        self.assertEqual(len(overlay["units"]), 7)
        self.assertEqual(fresh["review_status"], "draft")
        self.assertEqual(
            fresh["label_origin"], "agent_proposed_fresh_holdout_v3"
        )
        self.assertFalse(revealed_ids & fresh_ids)
        self.assertEqual(fresh_ids, overlay_ids)
        self.assertEqual(
            fresh["evidence_snapshot_fingerprint"],
            overlay["overlay_fingerprint"],
        )
        self.assertEqual(
            {item["gold_case"]["evaluation_split"] for item in fresh["cases"]},
            {"holdout"},
        )
        self.assertEqual(
            {item["gold_case"]["batch_id"] for item in fresh["cases"]},
            {"evidence-holdout-2026-07-31-c"},
        )
        self.assertEqual(preflight["status"], "passed")
        self.assertEqual(preflight["case_count"], 21)
        self.assertEqual(preflight["revealed_evidence_overlap"], [])
        self.assertEqual(
            preflight["required_or_forbidden_target_top5_rate"], 1.0
        )
        self.assertFalse(preflight["live_provider_run"])
        self.assertTrue(
            all(item["target_in_top5"] for item in preflight["cases"])
        )


if __name__ == "__main__":
    unittest.main()
