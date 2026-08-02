#!/usr/bin/env python3
"""Deadline budget contract (work-tree A2.1/A2.4).

The three deadline layers must satisfy:

    http_soft_deadline + cleanup_grace <= attempt_deadline <= task_deadline

The adapter's HTTP default must never silently exceed the job's attempt
deadline; the Gateway records the frozen budget so timeouts are auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

DEADLINE_BUDGET_SCHEMA = "wuli.deadline-budget.v1"

DEFAULT_TASK_DEADLINE_SECONDS = 90.0
DEFAULT_ATTEMPT_GRACE_SECONDS = 4.0
DEFAULT_CLEANUP_GRACE_SECONDS = 2.0
DEFAULT_HTTP_FRACTION = 0.85
MIN_HTTP_SOFT_DEADLINE_SECONDS = 10.0


@dataclass(frozen=True)
class DeadlineBudget:
    task_deadline: float
    attempt_deadline: float
    http_soft_deadline: float
    cleanup_grace: float
    task_deadline_source: str = "core-config"
    attempt_deadline_source: str = "derived"
    http_soft_deadline_source: str = "derived"

    def to_dict(self) -> dict:
        return {
            "schema": DEADLINE_BUDGET_SCHEMA,
            "task_deadline": round(self.task_deadline, 3),
            "attempt_deadline": round(self.attempt_deadline, 3),
            "http_soft_deadline": round(self.http_soft_deadline, 3),
            "cleanup_grace": round(self.cleanup_grace, 3),
            "task_deadline_source": self.task_deadline_source,
            "attempt_deadline_source": self.attempt_deadline_source,
            "http_soft_deadline_source": self.http_soft_deadline_source,
        }


def build_deadline_budget(
    *,
    task_deadline: float = DEFAULT_TASK_DEADLINE_SECONDS,
    configured_attempt: float | None = None,
    cleanup_grace: float = DEFAULT_CLEANUP_GRACE_SECONDS,
    attempt_grace: float = DEFAULT_ATTEMPT_GRACE_SECONDS,
    http_fraction: float = DEFAULT_HTTP_FRACTION,
    task_deadline_source: str = "core-config",
) -> DeadlineBudget:
    """Derive the three deadline layers from the task SLA.

    attempt_deadline is the model/attempt budget (clamped by the task
    deadline); http_soft_deadline reserves adapter cleanup inside the attempt.
    """
    task = max(10.0, float(task_deadline))
    if configured_attempt is not None and float(configured_attempt) > 0:
        attempt = max(15.0, min(task, float(configured_attempt)))
    else:
        attempt = max(15.0, task - attempt_grace)
    soft = max(MIN_HTTP_SOFT_DEADLINE_SECONDS, min(attempt * http_fraction, attempt - cleanup_grace - 1.0))
    return DeadlineBudget(
        task_deadline=task,
        attempt_deadline=attempt,
        http_soft_deadline=soft,
        cleanup_grace=float(cleanup_grace),
        task_deadline_source=task_deadline_source,
    )


def budget_is_valid(budget: DeadlineBudget) -> list[str]:
    """Return the ordered-deadline violations; empty list means valid."""
    problems: list[str] = []
    if not (budget.http_soft_deadline + budget.cleanup_grace <= budget.attempt_deadline):
        problems.append("http_soft_deadline + cleanup_grace > attempt_deadline")
    if not (budget.attempt_deadline <= budget.task_deadline):
        problems.append("attempt_deadline > task_deadline")
    if budget.http_soft_deadline <= 0 or budget.attempt_deadline <= 0 or budget.task_deadline <= 0:
        problems.append("deadlines must be positive")
    return problems


def effective_http_timeout(budget: DeadlineBudget, configured: float | None = None) -> float:
    """The adapter's HTTP timeout must never exceed the soft deadline."""
    if configured is None or configured <= 0:
        return budget.http_soft_deadline
    return min(float(configured), budget.http_soft_deadline)


__all__ = [
    "DEADLINE_BUDGET_SCHEMA",
    "DeadlineBudget",
    "build_deadline_budget",
    "budget_is_valid",
    "effective_http_timeout",
]
