import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "teacher-console" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import w3r_baseline_report  # noqa: E402

MANIFEST = ROOT / "teacher-console" / "tests" / "fixtures" / "w3r" / "baseline-manifest.json"


class W3RBaselineReportTest(unittest.TestCase):
    def test_frozen_sources_have_reproducible_metrics(self):
        first = w3r_baseline_report.build_report(MANIFEST, root=ROOT)
        second = w3r_baseline_report.build_report(MANIFEST, root=ROOT)
        self.assertEqual(first, second)
        self.assertEqual(first["case_count"], 5)
        self.assertFalse(first["production_behavior_changed"])
        self.assertTrue(
            all(
                item["source_digest"].startswith("sha256:") and item["answer_signature"].startswith("sha256:")
                for item in first["cases"]
            )
        )
        self.assertEqual(
            sum(item["kind"] == "official-competition" for item in first["cases"]),
            3,
        )


if __name__ == "__main__":
    unittest.main()
