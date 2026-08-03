import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import correctness_faults  # noqa: E402
import correctness_metrics  # noqa: E402

FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "correctness_faults.v1.json"


def shadow_summary():
    scenarios = []
    for name, status, verified, coverage in (
        ("normal", "PROVISIONAL", 3, 1.0),
        ("conflict", "UNRESOLVED", 1, 0.0),
        ("insufficient", "PROVISIONAL", 1, 0.0),
        ("fuse", "PROVISIONAL", 1, 0.0),
    ):
        scenarios.append({
            "name": name,
            "aggregation_status": status,
            "claim_count": 3,
            "certificate_count": 3,
            "verified_claim_count": verified,
            "critical_certificate_coverage": coverage,
            "unresolved_claim_count": 3 - verified,
            "repeated_task_count": 0,
            "loop_transition_count": 1,
            "canonical_unchanged": True,
        })
    return {"status": "passed", "scenarios": scenarios}


class CorrectnessMetricsTest(unittest.TestCase):
    def test_report_tracks_required_claim_loop_and_fault_metrics(self):
        report = correctness_metrics.build_report(
            correctness_faults.load_fault_cases(FIXTURE),
            shadow_summary=shadow_summary(),
            generated_at="2026-07-29T00:00:00+00:00",
        )
        self.assertEqual(report["fault_metrics"]["detection_rate"], 1.0)
        self.assertEqual(report["fault_metrics"]["false_promotion_count"], 0)
        self.assertEqual(
            report["shadow_metrics"]["mean_critical_certificate_coverage"],
            0.25,
        )
        self.assertEqual(report["shadow_metrics"]["repeated_task_rate"], 0.0)
        self.assertEqual(report["loop_metrics"]["backjump_precision"], 1.0)
        self.assertEqual(report["loop_metrics"]["backjump_recall"], 1.0)
        self.assertEqual(report["shadow_metrics"]["hard_unresolved_rate"], 0.25)
        self.assertTrue(report["gates"]["zero_false_promotion"])
        self.assertFalse(report["gates"]["production_authorized"])

    def test_shadow_summary_missing_metrics_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            correctness_metrics.build_report(
                correctness_faults.load_fault_cases(FIXTURE),
                shadow_summary={
                    "status": "passed",
                    "scenarios": [{"name": "normal"}],
                },
            )

    def test_markdown_preserves_production_limitation(self):
        report = correctness_metrics.build_report(
            correctness_faults.load_fault_cases(FIXTURE),
            shadow_summary=shadow_summary(),
            generated_at="2026-07-29T00:00:00+00:00",
        )
        markdown = correctness_metrics.render_markdown(report)
        self.assertIn("错误晋升：0", markdown)
        self.assertIn("production_authorized`：false", markdown)
        self.assertIn("WAIT-5", markdown)


if __name__ == "__main__":
    unittest.main()
