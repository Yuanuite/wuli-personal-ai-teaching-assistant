#!/usr/bin/env python3
"""W3 target-level risk, benefit, verification, and teacher-focus contracts."""

from __future__ import annotations

import re
from typing import Any

AUDIT_CONTRACT = "wuli.solution-verify.v1"
RISK_WEIGHT = {"low": 0.1, "medium": 0.35, "high": 0.7, "critical": 0.95}


def target_risk(
    target: dict[str, Any],
    obligations: list[dict[str, Any]],
    *,
    evidence_status: str,
    blueprint_status: str,
) -> dict[str, Any]:
    text = " ".join([
        str(target.get("prompt", "")),
        str(target.get("final_answer", "")),
        *(str(item.get("check", "")) for item in obligations),
    ])
    signals: list[str] = []
    score = max(
        [RISK_WEIGHT.get(str(item.get("risk", "low")), 0.1) for item in obligations] or [0.1]
    )
    for name, pattern, increment in (
        ("uniqueness", r"唯一|所有可能|全部|至少|至多", 0.2),
        ("first-event", r"第一次|首次|最早", 0.2),
        ("extremum", r"最大|最小|临界", 0.15),
        ("classification", r"区间|分类|其他情况|排除", 0.15),
    ):
        if re.search(pattern, text):
            signals.append(name)
            score += increment
    if evidence_status not in {"selected", "ready"}:
        signals.append("weak-evidence")
        score += 0.1
    if blueprint_status == "revised-equivalent":
        signals.append("blueprint-revised")
        score += 0.15
    return {
        "target_id": str(target.get("id", "")),
        "risk": round(min(score, 1.0), 4),
        "signals": signals,
    }


def expected_gain(
    risk: float,
    *,
    catch_probability: float,
    error_cost: float,
    verification_cost: float,
    false_conflict_probability: float,
    review_cost: float,
) -> float:
    return round(
        float(risk) * float(catch_probability) * float(error_cost)
        - float(verification_cost)
        - float(false_conflict_probability) * float(review_cost),
        6,
    )


def should_verify(risk: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any]:
    gain = expected_gain(
        float(risk.get("risk", 0.0)),
        catch_probability=float(calibration.get("catch_probability", 0.6)),
        error_cost=float(calibration.get("error_cost", 1.0)),
        verification_cost=float(calibration.get("verification_cost", 0.2)),
        false_conflict_probability=float(calibration.get("false_conflict_probability", 0.1)),
        review_cost=float(calibration.get("review_cost", 0.5)),
    )
    return {
        **risk,
        "expected_gain": gain,
        "decision": "verify" if gain > 0 else "deterministic-only",
        "policy": "positive-expected-gain-v1",
    }


def verification_evidence_view(evidence: dict[str, Any]) -> dict[str, Any]:
    """Strip historical answer prose before the independent verifier sees evidence."""
    references = []
    for raw in evidence.get("references", []) if isinstance(evidence, dict) else []:
        if not isinstance(raw, dict):
            continue
        references.append({
            "reference": str(raw.get("reference", ""))[:80],
            "knowledge_points": [
                str(item)[:80] for item in raw.get("knowledge_points", [])[:8]
            ],
            "methods": [str(item)[:160] for item in raw.get("methods", [])[:6]],
            "secondary_conclusions": [
                str(item)[:200] for item in raw.get("secondary_conclusions", [])[:5]
            ],
            "coverage": raw.get("coverage", {}),
            "evidence_audit": raw.get("evidence_audit", {}),
            "retrieval_target_ids": raw.get("retrieval_target_ids", []),
        })
    evidence_set = evidence.get("evidence_set", {}) if isinstance(evidence, dict) else {}
    return {
        "schema_version": 1,
        "status": str(evidence.get("status", "unavailable")) if isinstance(evidence, dict) else "unavailable",
        "references": references,
        "evidence_set": {
            key: evidence_set[key]
            for key in (
                "selection_status",
                "contradiction_count",
                "condition_covered_count",
                "condition_missing_count",
            )
            if key in evidence_set
        },
        "forbidden_fields_removed": [
            "title",
            "matched_evidence.snippet",
            "historical answer text",
            "entry and local path identifiers",
        ],
    }


AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "target_audits": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["pass", "conflict", "insufficient"]},
                    "recomputed_result": {"type": "string"},
                    "decisive_checks": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                    "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
                },
                "required": [
                    "target_id",
                    "verdict",
                    "recomputed_result",
                    "decisive_checks",
                    "issues",
                ],
            },
            "maxItems": 12,
        },
    },
    "required": ["status", "message", "target_audits"],
}


def output_contract() -> dict[str, Any]:
    return {
        "name": AUDIT_CONTRACT,
        "schema": AUDIT_SCHEMA,
        "instructions": (
            "只审计指定风险目标。根据已复核题干、双层蓝图、候选结论和高中结论独立复算。"
            "不得读取或推断历史答案正文，不得重写完整解析。"
            "pass 必须给出至少一条决定性检查；无法复算时返回 insufficient，不得猜测。"
        ),
    }


def normalize_audit(payload: dict[str, Any], expected_target_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("audit output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = str(payload.get("message", "")).strip()
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("audit status must be completed or unsupported")
    audits = []
    seen: set[str] = set()
    for raw in payload.get("target_audits") or []:
        target_id = str(raw.get("target_id", "")).strip()
        verdict = str(raw.get("verdict", "")).strip()
        checks = [str(item).strip()[:240] for item in raw.get("decisive_checks", []) if str(item).strip()]
        issues = [str(item).strip()[:240] for item in raw.get("issues", []) if str(item).strip()]
        if target_id not in expected_target_ids or target_id in seen:
            raise ValueError("audit target is unknown or duplicated")
        if verdict not in {"pass", "conflict", "insufficient"}:
            raise ValueError("audit verdict is invalid")
        if verdict == "pass" and not checks:
            raise ValueError("pass audit requires a decisive check")
        seen.add(target_id)
        audits.append({
            "target_id": target_id,
            "verdict": verdict,
            "recomputed_result": str(raw.get("recomputed_result", "")).strip()[:500],
            "decisive_checks": checks[:6],
            "issues": issues[:6],
        })
    if seen != expected_target_ids:
        raise ValueError("audit did not cover every requested target")
    return {"status": "completed", "message": message[:1000], "target_audits": audits}


def teacher_review_focus(audits: list[dict[str, Any]], obligations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return at most two concise, actionable review-focus cards."""
    obligation_by_target: dict[str, list[str]] = {}
    for item in obligations:
        obligation_by_target.setdefault(str(item.get("target_id", "")), []).append(str(item.get("check", "")))
    priority = {"conflict": 0, "insufficient": 1}
    flagged = [item for item in audits if item.get("verdict") in priority]
    flagged.sort(key=lambda item: (priority[str(item.get("verdict"))], str(item.get("target_id", ""))))
    result = []
    for item in flagged[:2]:
        target_id = str(item.get("target_id", ""))
        issue = next(iter(item.get("issues") or []), "")
        decisive = next(iter(item.get("decisive_checks") or []), "")
        if not decisive:
            decisive = next(iter(obligation_by_target.get(target_id, [])), "")
        result.append({
            "target_id": target_id,
            "priority": "high" if item.get("verdict") == "conflict" else "medium",
            "prompt": (issue or f"请重点核对 {target_id} 的关键结论")[:160],
            "decisive_check": decisive[:200],
        })
    return result
