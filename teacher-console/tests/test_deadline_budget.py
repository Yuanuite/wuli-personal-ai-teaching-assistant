"""Unit tests for the deadline budget contract (A2.1/A2.4)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
for path in (CONSOLE,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from deadline_budget import (  # noqa: E402
    PROVIDER_DEADLINE_BINDING_SCHEMA,
    TIMEOUT_LAYER_ATTEMPT_HARD,
    TIMEOUT_LAYER_HTTP_SOFT,
    budget_from_dict,
    budget_is_valid,
    build_deadline_budget,
    effective_http_timeout,
    provider_deadline_binding,
)


class DeadlineBudgetTest(unittest.TestCase):
    def test_ordered_deadline_invariant_holds(self):
        budget = build_deadline_budget(task_deadline=90)
        self.assertEqual(budget.task_deadline, 90)
        self.assertLessEqual(budget.attempt_deadline, budget.task_deadline)
        self.assertLessEqual(
            budget.http_soft_deadline + budget.cleanup_grace, budget.attempt_deadline
        )
        self.assertEqual(budget_is_valid(budget), [])

    def test_configured_attempt_clamps_inside_task(self):
        budget = build_deadline_budget(task_deadline=90, configured_attempt=30)
        self.assertEqual(budget.attempt_deadline, 30)
        self.assertLessEqual(
            budget.http_soft_deadline + budget.cleanup_grace, budget.attempt_deadline
        )
        self.assertEqual(budget_is_valid(budget), [])

    def test_configured_attempt_never_exceeds_task(self):
        budget = build_deadline_budget(task_deadline=60, configured_attempt=120)
        self.assertEqual(budget.attempt_deadline, 60)
        self.assertEqual(budget_is_valid(budget), [])

    def test_http_timeout_capped_at_soft_deadline(self):
        budget = build_deadline_budget(task_deadline=90)
        self.assertEqual(effective_http_timeout(budget, 300), budget.http_soft_deadline)
        self.assertEqual(effective_http_timeout(budget, 10), 10)
        self.assertEqual(effective_http_timeout(budget, None), budget.http_soft_deadline)

    def test_invalid_budget_detected(self):
        # A budget where the soft deadline exceeds the attempt must fail.
        class _Bad:
            http_soft_deadline = 100.0
            attempt_deadline = 50.0
            task_deadline = 120.0
            cleanup_grace = 2.0
            task_deadline_source = "test"
            attempt_deadline_source = "test"
            http_soft_deadline_source = "test"

            def to_dict(self):
                return {}

        problems = budget_is_valid(_Bad())
        self.assertIn("http_soft_deadline + cleanup_grace > attempt_deadline", problems)

    def test_analysis_task_budget_fits_within_90s(self):
        budget = build_deadline_budget(task_deadline=90)
        self.assertEqual(budget.task_deadline, 90)
        self.assertLess(budget.http_soft_deadline, 90)
        self.assertGreater(budget.http_soft_deadline, 0)


class ProviderDeadlineBindingTest(unittest.TestCase):
    """A0.3 (w3-w3r work-tree): the child-binding contract."""

    def test_binding_accepts_budget_dict_and_marks_http_soft(self):
        budget = build_deadline_budget(task_deadline=90)
        binding = provider_deadline_binding(
            budget.to_dict(),
            effective_timeout=budget.http_soft_deadline,
            timeout_layer=TIMEOUT_LAYER_HTTP_SOFT,
            provider="openai-compatible",
            model_id="mock",
        )
        self.assertEqual(binding["schema"], PROVIDER_DEADLINE_BINDING_SCHEMA)
        self.assertEqual(binding["timeout_layer"], TIMEOUT_LAYER_HTTP_SOFT)
        self.assertEqual(binding["effective_timeout"], budget.http_soft_deadline)
        self.assertEqual(binding["http_soft_deadline"], budget.http_soft_deadline)

    def test_binding_expresses_recorded_correct_child_wrong(self):
        # The state the failure fixture captures: the recorded budget says
        # 76.5s soft, but the child actually ran with the 300s adapter default.
        budget = build_deadline_budget(task_deadline=90)
        binding = provider_deadline_binding(
            budget.to_dict(),
            effective_timeout=300,
            timeout_layer=TIMEOUT_LAYER_ATTEMPT_HARD,
        )
        self.assertEqual(binding["timeout_layer"], TIMEOUT_LAYER_ATTEMPT_HARD)
        self.assertGreater(binding["effective_timeout"], binding["http_soft_deadline"])
        self.assertLessEqual(binding["attempt_deadline"], binding["task_deadline"])

    def test_budget_from_dict_roundtrip(self):
        budget = build_deadline_budget(task_deadline=90, configured_attempt=40)
        rebuilt = budget_from_dict(budget.to_dict())
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.attempt_deadline, budget.attempt_deadline)
        self.assertEqual(rebuilt.http_soft_deadline, budget.http_soft_deadline)

    def test_budget_from_dict_rejects_malformed(self):
        self.assertIsNone(budget_from_dict(None))
        self.assertIsNone(budget_from_dict({"task_deadline": "x"}))

    def test_effective_timeout_accepts_dict_single_path(self):
        budget = build_deadline_budget(task_deadline=90)
        self.assertEqual(effective_http_timeout(budget.to_dict(), 300), budget.http_soft_deadline)
        self.assertEqual(effective_http_timeout(budget.to_dict(), None), budget.http_soft_deadline)
        self.assertEqual(effective_http_timeout(None, 12), 12)
        self.assertEqual(effective_http_timeout(None, None), 0.0)


if __name__ == "__main__":
    unittest.main()
