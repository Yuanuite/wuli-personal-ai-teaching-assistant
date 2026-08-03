#!/usr/bin/env python3
"""Deadline budget contract (work-tree A2.1/A2.4, w3-w3r A0.3/A1.2).

The three deadline layers must satisfy:

    http_soft_deadline + cleanup_grace <= attempt_deadline <= task_deadline

The adapter's HTTP default must never silently exceed the job's attempt
deadline; the Gateway records the frozen budget so timeouts are auditable.
``effective_http_timeout`` is the single computation of the child HTTP
timeout; the Gateway env builder, the adapter and tests must not re-implement
the ``min()`` rule. ``provider_deadline_binding`` records which layer actually
fired and what timeout the child received, so a correct recorded budget cannot
mask a mis-bound child environment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEADLINE_BUDGET_SCHEMA = "wuli.deadline-budget.v1"
PROVIDER_DEADLINE_BINDING_SCHEMA = "wuli.provider-deadline-binding.v1"

# timeout_layer values: which deadline the failure is attributed to.
TIMEOUT_LAYER_HTTP_SOFT = "http_soft"
TIMEOUT_LAYER_ATTEMPT_HARD = "attempt_hard"
TIMEOUT_LAYER_TASK_BUDGET = "task_budget"

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


def budget_from_dict(raw: dict[str, Any] | None) -> DeadlineBudget | None:
    """Rebuild a frozen budget from its ``to_dict()`` representation."""
    if not isinstance(raw, dict):
        return None
    try:
        return DeadlineBudget(
            task_deadline=float(raw["task_deadline"]),
            attempt_deadline=float(raw["attempt_deadline"]),
            http_soft_deadline=float(raw["http_soft_deadline"]),
            cleanup_grace=float(raw.get("cleanup_grace", DEFAULT_CLEANUP_GRACE_SECONDS)),
            task_deadline_source=str(raw.get("task_deadline_source", "core-config")),
            attempt_deadline_source=str(raw.get("attempt_deadline_source", "derived")),
            http_soft_deadline_source=str(raw.get("http_soft_deadline_source", "derived")),
        )
    except (TypeError, ValueError, KeyError):
        return None


def effective_http_timeout(
    budget: DeadlineBudget | dict[str, Any] | None,
    configured: float | None = None,
) -> float:
    """The adapter's HTTP timeout must never exceed the soft deadline.

    Single computation path: the Gateway env builder, tests and any other
    consumer must call this instead of re-implementing ``min()``.
    """
    resolved = budget_from_dict(budget) if isinstance(budget, dict) else budget
    if resolved is None:
        return float(configured) if configured and configured > 0 else 0.0
    soft = resolved.http_soft_deadline
    if configured is None or configured <= 0:
        return soft
    return min(float(configured), soft)


def provider_deadline_binding(
    budget: DeadlineBudget | dict[str, Any] | None,
    *,
    effective_timeout: float,
    timeout_layer: str,
    provider: str = "",
    model_id: str = "",
    budget_digest: str = "",
    config_digest: str = "",
) -> dict[str, Any]:
    """Record the child deadline binding (work-tree A0.3).

    Expresses states like "recorded budget correct but child binding wrong":
    ``effective_timeout`` is what the child actually received, while the budget
    fields are the frozen contract.
    """
    resolved = budget_from_dict(budget) if isinstance(budget, dict) else budget
    return {
        "schema": PROVIDER_DEADLINE_BINDING_SCHEMA,
        "task_deadline": round(resolved.task_deadline, 3) if resolved else None,
        "attempt_deadline": round(resolved.attempt_deadline, 3) if resolved else None,
        "http_soft_deadline": round(resolved.http_soft_deadline, 3) if resolved else None,
        "effective_timeout": round(float(effective_timeout), 3),
        "timeout_layer": timeout_layer,
        "budget_digest": budget_digest,
        "config_digest": config_digest,
        "provider": provider,
        "model_id": model_id,
    }


__all__ = [
    "DEADLINE_BUDGET_SCHEMA",
    "PROVIDER_DEADLINE_BINDING_SCHEMA",
    "TIMEOUT_LAYER_HTTP_SOFT",
    "TIMEOUT_LAYER_ATTEMPT_HARD",
    "TIMEOUT_LAYER_TASK_BUDGET",
    "DeadlineBudget",
    "build_deadline_budget",
    "budget_is_valid",
    "budget_from_dict",
    "effective_http_timeout",
    "provider_deadline_binding",
]
