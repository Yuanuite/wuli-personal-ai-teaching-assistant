import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import correctness_ablation  # noqa: E402


class CorrectnessAblationTest(unittest.TestCase):
    def test_loop_ablation_keeps_conditions_fixed_and_never_promotes_faults(self):
        report = correctness_ablation.run_ablation(
            generated_at="2026-07-29T00:00:00+00:00"
        )
        self.assertTrue(report["fixed_conditions"]["conditions_equal"])
        self.assertEqual(report["metrics"]["off_false_promotion_count"], 0)
        self.assertEqual(report["metrics"]["on_false_promotion_count"], 0)
        self.assertEqual(report["metrics"]["answer_consistency_rate"], 1.0)
        self.assertGreater(report["metrics"]["diagnostic_challenge_gain"], 0)
        self.assertEqual(report["metrics"]["association_replay_rate"], 1.0)
        self.assertEqual(
            report["metrics"]["association_truth_promotion_count"], 0
        )
        self.assertTrue(report["gates"]["no_error_promotion_regression"])
        self.assertFalse(report["gates"]["production_authorized"])

    def test_fault_paths_gain_bounded_challenges_and_fuse(self):
        report = correctness_ablation.run_ablation()
        fault_cases = [
            item
            for item in report["cases"]
            if item["injected_verdict"] != "pass"
        ]
        self.assertTrue(fault_cases)
        for item in fault_cases:
            with self.subTest(scenario=item["scenario"]):
                self.assertEqual(item["off"]["challenge_count"], 0)
                self.assertGreater(item["on"]["challenge_count"], 0)
                self.assertTrue(item["on"]["fuse_triggered"])
                self.assertNotEqual(
                    item["on"]["aggregation_status"], "VERIFIED"
                )

    def test_report_is_replayable_under_fixed_timestamp(self):
        first = correctness_ablation.run_ablation(
            generated_at="2026-07-29T00:00:00+00:00"
        )
        second = correctness_ablation.run_ablation(
            generated_at="2026-07-29T00:00:00+00:00"
        )
        self.assertEqual(first, second)
        markdown = correctness_ablation.render_markdown(first)
        self.assertIn("同题、同模型、同证据：true", markdown)
        self.assertIn("尚不能证明自然题正确率提高", markdown)


if __name__ == "__main__":
    unittest.main()
