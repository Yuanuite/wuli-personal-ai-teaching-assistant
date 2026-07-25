#!/usr/bin/env python3
"""Canonical, privacy-minimized outcome for one terminal Agent request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _seconds(start: Any, end: Any) -> float | None:
    left = _time(start)
    right = _time(end)
    if not left or not right:
        return None
    return round(max(0.0, (right - left).total_seconds()), 3)


def _clean_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    clean = {
        key: item
        for key, item in value.items()
        if key in {"prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens"}
        and isinstance(item, int)
        and not isinstance(item, bool)
        and item >= 0
    }
    input_tokens = clean.get("input_tokens", clean.get("prompt_tokens"))
    output_tokens = clean.get("output_tokens", clean.get("completion_tokens"))
    if "total_tokens" not in clean and input_tokens is not None and output_tokens is not None:
        clean["total_tokens"] = input_tokens + output_tokens
    return clean


def _non_negative_int(value: Any, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _aggregate_attempt_usage(attempts: list[dict[str, Any]]) -> dict[str, int]:
    usages = [_clean_usage(item.get("token_usage")) for item in attempts]
    usages = [item for item in usages if item]
    if not usages:
        return {}
    keys = {key for usage in usages for key in usage}
    return {key: sum(usage.get(key, 0) for usage in usages) for key in sorted(keys)}


@dataclass(frozen=True)
class AgentRequestOutcome:
    """Stable observation contract; never stores prompts or provider output."""

    status: str
    provider: str
    model: str
    failure_type: str
    usage: dict[str, int]
    usage_measurement: str
    attempt_count: int
    attempted_providers: tuple[str, ...]
    provider_seconds: float
    queue_seconds: float | None
    run_seconds: float | None
    total_seconds: float | None
    budget_guard_reason: str
    resumed_from_checkpoint: bool
    evidence_status: str
    evidence_reference_count: int
    evidence_requested_chars: int | None
    evidence_serialized_chars: int | None
    evidence_truncated: bool | None

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": 1,
            "status": self.status,
            "provider": self.provider or None,
            "model": self.model or None,
            "failure_type": self.failure_type or None,
            "usage": {
                **self.usage,
                "measurement": self.usage_measurement,
            },
            "attempts": {
                "count": self.attempt_count,
                "providers": list(self.attempted_providers),
                "provider_seconds": self.provider_seconds,
            },
            "timing": {
                "queue_seconds": self.queue_seconds,
                "run_seconds": self.run_seconds,
                "total_seconds": self.total_seconds,
            },
            "controls": {
                "budget_guard_reason": self.budget_guard_reason or None,
                "resumed_from_checkpoint": self.resumed_from_checkpoint,
            },
            "evidence_context": {
                "status": self.evidence_status or "unavailable",
                "reference_count": self.evidence_reference_count,
                "requested_chars": self.evidence_requested_chars,
                "serialized_chars": self.evidence_serialized_chars,
                "truncated": self.evidence_truncated,
            },
        }
        return value


def build_agent_request_outcome(
    result: dict[str, Any] | None,
    *,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the one canonical outcome used by jobs, archives, and benchmarks."""
    result = result if isinstance(result, dict) else {}
    record = record if isinstance(record, dict) else {}
    attempts = [item for item in result.get("attempts", []) if isinstance(item, dict)]
    usage = _aggregate_attempt_usage(attempts)
    if not usage:
        usage = _clean_usage(result.get("usage"))
    usage_measurement = "provider-reported" if usage else "unavailable"

    evidence = result.get("evidence_context")
    if not isinstance(evidence, dict):
        evidence = {}
    budget = evidence.get("budget") if isinstance(evidence.get("budget"), dict) else {}
    guard = result.get("budget_guard")
    if not isinstance(guard, dict):
        guard = {}

    providers = tuple(
        dict.fromkeys(str(item.get("provider", "")).strip() for item in attempts if item.get("provider"))
    )
    provider_seconds = round(
        sum(
            float(item.get("duration_seconds", 0))
            for item in attempts
            if isinstance(item.get("duration_seconds"), (int, float))
        ),
        3,
    )
    failure = str(result.get("failure_type") or record.get("failure_type") or "")
    status = str(record.get("status") or result.get("status") or "failed")
    outcome = AgentRequestOutcome(
        status=status,
        provider=str(result.get("provider") or record.get("provider") or ""),
        model=str(result.get("model") or result.get("model_id") or record.get("model_id") or ""),
        failure_type=failure,
        usage=usage,
        usage_measurement=usage_measurement,
        attempt_count=len(attempts),
        attempted_providers=providers,
        provider_seconds=provider_seconds,
        queue_seconds=_seconds(record.get("created_at"), record.get("started_at")),
        run_seconds=_seconds(record.get("started_at"), record.get("completed_at")),
        total_seconds=_seconds(record.get("created_at"), record.get("completed_at")),
        budget_guard_reason=str(guard.get("reason", "")),
        resumed_from_checkpoint=result.get("resumed_from_checkpoint") is True,
        evidence_status=str(evidence.get("status", "unavailable")),
        evidence_reference_count=_non_negative_int(evidence.get("reference_count", 0)),
        evidence_requested_chars=budget.get("requested_chars")
        if isinstance(budget.get("requested_chars"), int)
        else None,
        evidence_serialized_chars=budget.get("serialized_chars")
        if isinstance(budget.get("serialized_chars"), int)
        else None,
        evidence_truncated=budget.get("truncated") if isinstance(budget.get("truncated"), bool) else None,
    )
    return outcome.to_dict()
