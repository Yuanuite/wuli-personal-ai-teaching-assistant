import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = ROOT / "teacher-console" / "scripts" / "evidence_budget_benchmark.py"
SPEC = importlib.util.spec_from_file_location("evidence_budget_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class EvidenceBudgetBenchmarkTest(unittest.TestCase):
    def case(self, index: int) -> dict:
        return {
            "id": f"case-{index}",
            "entry_id": f"entry-{index}",
            "task_type": "answer.revise",
            "query": "动量守恒",
            "required_facts": ["规定正方向"],
            "review_status": "approved",
        }

    @staticmethod
    def fake_evidence(*_args, char_budget: int, **_kwargs):
        padding = "补充证据" * (1200 if char_budget >= 20000 else 100)
        return {
            "status": "ready",
            "references": [{"methods": ["规定正方向"], "padding": padding}],
            "context_budget": {"requested_chars": char_budget},
        }

    @mock.patch.object(benchmark.knowledge_store, "build_agent_evidence", side_effect=fake_evidence.__func__)
    def test_approved_fixed_set_can_pass_preflight(self, _build):
        report = benchmark.run(Path("/unused"), [self.case(index) for index in range(20)])
        self.assertTrue(report["preflight_passed"])
        self.assertEqual(report["authorizes"], "paired-model-evaluation")
        self.assertEqual(report["required_fact_retention"], 1.0)
        self.assertGreaterEqual(report["median_savings_ratio"], 0.25)

    @mock.patch.object(benchmark.knowledge_store, "build_agent_evidence", side_effect=fake_evidence.__func__)
    def test_draft_or_small_set_never_authorizes_policy_change(self, _build):
        cases = [self.case(index) for index in range(3)]
        cases[0]["review_status"] = "draft"
        report = benchmark.run(Path("/unused"), cases, include_draft=True)
        self.assertFalse(report["preflight_passed"])
        self.assertEqual(report["authorizes"], "no-policy-change")

    def test_validation_requires_teacher_labelled_facts(self):
        case = self.case(1)
        case["required_facts"] = []
        report = benchmark.validate_cases([case])
        self.assertFalse(report["valid"])


if __name__ == "__main__":
    unittest.main()
