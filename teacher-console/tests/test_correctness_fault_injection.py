import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import correctness_faults  # noqa: E402

FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "correctness_faults.v1.json"


class CorrectnessFaultInjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = correctness_faults.load_fault_cases(FIXTURE)

    def test_fixture_contract_and_unique_case_ids(self):
        self.assertGreaterEqual(len(self.cases), 12)
        required_categories = {
            "arithmetic",
            "dimension",
            "interval",
            "event-order",
            "interface",
            "promotion",
            "hypothesis",
            "backjump",
            "loop",
        }
        self.assertTrue(required_categories.issubset({case["category"] for case in self.cases}))

    def test_every_declared_fault_is_detected_without_false_pass(self):
        outcomes = correctness_faults.run_fault_suite(self.cases)
        for outcome in outcomes:
            with self.subTest(case_id=outcome["case_id"]):
                self.assertTrue(outcome["detected"])
                self.assertFalse(outcome["false_promotion"])

    def test_fault_suite_is_replayable(self):
        self.assertEqual(
            correctness_faults.run_fault_suite(self.cases),
            correctness_faults.run_fault_suite(self.cases),
        )


if __name__ == "__main__":
    unittest.main()
