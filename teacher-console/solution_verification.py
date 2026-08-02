#!/usr/bin/env python3
"""W3 target-level risk, benefit, verification, and teacher-focus contracts."""

from __future__ import annotations

import re
from typing import Any

import claim_ledger
import structured_text

AUDIT_CONTRACT = "wuli.solution-verify.v1"
CLAIM_AUDIT_CONTRACT = "wuli.claim-verify.v2"
RISK_WEIGHT = {"low": 0.1, "medium": 0.35, "high": 0.7, "critical": 0.95}


def _safe_text(value: Any, field: str, maximum: int) -> str:
    return structured_text.reject_unsupported_controls(value, field).strip()[:maximum]


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
        "message": {"type": "string", "maxLength": 200},
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


CLAIM_AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string", "maxLength": 200},
        "claim_audits": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim_id": {"type": "string", "maxLength": 80},
                    "claim_version": {"type": "integer", "minimum": 1},
                    "verdict": {
                        "type": "string",
                        "enum": ["pass", "conflict", "insufficient"],
                    },
                    "normalized_result": {"type": "string", "maxLength": 300},
                    "decisive_checks": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 240},
                        "maxItems": 2,
                    },
                    "issues": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 240},
                        "maxItems": 2,
                    },
                },
                "required": [
                    "claim_id",
                    "claim_version",
                    "verdict",
                    "normalized_result",
                    "decisive_checks",
                    "issues",
                ],
            },
            "maxItems": 24,
        },
        "interface_audit": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["pass", "conflict", "insufficient"],
                },
                "decisive_checks": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 240},
                    "maxItems": 2,
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 240},
                    "maxItems": 2,
                },
            },
            "required": ["verdict", "decisive_checks", "issues"],
        },
    },
    "required": ["status", "message", "claim_audits", "interface_audit"],
}


def claim_output_contract() -> dict[str, Any]:
    return {
        "name": CLAIM_AUDIT_CONTRACT,
        "schema": CLAIM_AUDIT_SCHEMA,
        "instructions": (
            "只审计请求中的原子 Claim，不生成完整答案，不补写未请求的下游结论。"
            "只使用已批准 source_facts、当前 Claim 及其直接依赖；不得假定 Solver 正确，"
            "不得读取历史答案正文。每个请求 Claim 必须且只能返回一条 audit。"
            "pass 必须包含至少一条可复核的 decisive_check；有冲突返回 conflict；"
            "信息不足返回 insufficient，不得猜测。每条只写最小判定证据："
            "normalized_result 一句话，decisive_checks 最多两条，禁止复述题干或完整推导。"
            "若提供 stage_interface_view，还必须独立核对其中待语义验证的事件变换；"
            "interface_audit.pass 必须给出最小决定性检查。"
        ),
    }


def claim_verification_view(
    requests: list[dict[str, Any]],
    source_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a minimal view with no solver provenance or historical answer prose."""
    if not isinstance(requests, list) or not requests or len(requests) > 24:
        raise ValueError("claim verification requests must contain 1-24 items")
    facts = []
    fact_ids: set[str] = set()
    for raw in source_facts:
        if not isinstance(raw, dict):
            raise ValueError("source fact must be an object")
        fact_id = str(raw.get("id", "")).strip()
        statement = str(raw.get("statement", "")).strip()
        if not fact_id or not statement or fact_id in fact_ids:
            raise ValueError("source fact id/statement is invalid or duplicated")
        fact_ids.add(fact_id)
        facts.append({
            "id": fact_id[:80],
            "statement": statement[:8_000],
            "conditions": [
                str(item).strip()[:240]
                for item in raw.get("conditions", [])
                if str(item).strip()
            ][:12],
        })

    minimal_requests = []
    seen: set[tuple[str, int]] = set()
    for raw in requests:
        if not isinstance(raw, dict) or set(raw) != {"claim", "dependencies"}:
            raise ValueError("claim verification request fields are invalid")
        claim = claim_ledger.normalize_claim(
            raw["claim"], agent_submission=False
        )
        dependencies = [
            claim_ledger.normalize_claim(item, agent_submission=False)
            for item in raw["dependencies"]
        ]
        fingerprint = claim_ledger.claim_verification_input_fingerprint(
            claim, dependencies
        )
        key = (claim["id"], claim["version"])
        if key in seen:
            raise ValueError("claim verification request is duplicated")
        seen.add(key)
        minimal_requests.append({
            "claim": {
                key: claim[key]
                for key in (
                    "id",
                    "version",
                    "kind",
                    "statement",
                    "target_ids",
                    "stage_ids",
                    "depends_on",
                    "conditions",
                    "obligation_ids",
                    "check_spec",
                )
            },
            "dependencies": [
                {
                    key: dependency[key]
                    for key in (
                        "id",
                        "version",
                        "kind",
                        "statement",
                        "conditions",
                    )
                }
                for dependency in sorted(
                    dependencies, key=lambda item: (item["id"], item["version"])
                )
            ],
            "input_fingerprint": fingerprint,
        })
    return {
        "schema_version": 1,
        "source_facts": facts,
        "requests": minimal_requests,
        "forbidden_fields_removed": [
            "claim.source",
            "claim.status",
            "solver identity and role",
            "historical answer prose",
            "downstream and unrelated claims",
            "local paths and entry identifiers",
        ],
    }


def normalize_claim_audit(
    payload: dict[str, Any],
    expected_claim_versions: dict[str, int],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("claim audit output must be an object")
    if set(payload) != {
        "status",
        "message",
        "claim_audits",
        "interface_audit",
    }:
        raise ValueError("claim audit output fields are invalid")
    status = str(payload["status"]).strip().lower()
    message = _safe_text(payload["message"], "message", 1_000)
    if status == "unsupported":
        if payload["claim_audits"] not in (None, []):
            raise ValueError("unsupported claim audit must not contain audits")
        if payload["interface_audit"] is not None:
            raise ValueError("unsupported claim audit must not contain interface audit")
        return {
            "status": "unsupported",
            "message": message,
            "claim_audits": [],
            "interface_audit": None,
        }
    if status != "completed":
        raise ValueError("claim audit status must be completed or unsupported")
    raw_audits = payload["claim_audits"]
    if not isinstance(raw_audits, list):
        raise ValueError("claim_audits must be an array")
    audits = []
    seen: set[str] = set()
    for raw in raw_audits:
        if not isinstance(raw, dict) or set(raw) != {
            "claim_id",
            "claim_version",
            "verdict",
            "normalized_result",
            "decisive_checks",
            "issues",
        }:
            raise ValueError("claim audit fields are invalid")
        claim_id = _safe_text(raw["claim_id"], "claim_audits.claim_id", 80)
        version = raw["claim_version"]
        if claim_id not in expected_claim_versions or claim_id in seen:
            raise ValueError("claim audit id is unknown or duplicated")
        if version != expected_claim_versions[claim_id]:
            raise ValueError("claim audit version does not match request")
        verdict = str(raw["verdict"]).strip().lower()
        if verdict not in {"pass", "conflict", "insufficient"}:
            raise ValueError("claim audit verdict is invalid")
        if not isinstance(raw["decisive_checks"], list) or not isinstance(
            raw["issues"], list
        ):
            raise ValueError("claim audit checks and issues must be arrays")
        checks = [
            _safe_text(item, "claim_audits.decisive_checks", 500)
            for item in raw["decisive_checks"]
            if isinstance(item, str) and item.strip()
        ][:8]
        issues = [
            _safe_text(item, "claim_audits.issues", 500)
            for item in raw["issues"]
            if isinstance(item, str) and item.strip()
        ][:8]
        if verdict == "pass" and not checks:
            raise ValueError("pass claim audit requires a decisive check")
        if verdict in {"conflict", "insufficient"} and not issues:
            raise ValueError(f"{verdict} claim audit requires an issue")
        seen.add(claim_id)
        audits.append({
            "claim_id": claim_id,
            "claim_version": version,
            "verdict": verdict,
            "normalized_result": _safe_text(
                raw["normalized_result"], "claim_audits.normalized_result", 2_000
            ),
            "decisive_checks": checks,
            "issues": issues,
        })
    if seen != set(expected_claim_versions):
        missing = sorted(set(expected_claim_versions) - seen)
        raise ValueError(f"claim audit did not cover every request: {missing}")
    raw_interface = payload["interface_audit"]
    interface_audit = None
    if raw_interface is not None:
        if not isinstance(raw_interface, dict) or set(raw_interface) != {
            "verdict",
            "decisive_checks",
            "issues",
        }:
            raise ValueError("interface_audit fields are invalid")
        verdict = str(raw_interface["verdict"]).strip().lower()
        checks = [
            _safe_text(item, "interface_audit.decisive_checks", 240)
            for item in raw_interface["decisive_checks"]
            if isinstance(item, str) and item.strip()
        ][:2]
        issues = [
            _safe_text(item, "interface_audit.issues", 240)
            for item in raw_interface["issues"]
            if isinstance(item, str) and item.strip()
        ][:2]
        if verdict not in {"pass", "conflict", "insufficient"}:
            raise ValueError("interface_audit verdict is invalid")
        if verdict == "pass" and not checks:
            raise ValueError("pass interface_audit requires a decisive check")
        if verdict in {"conflict", "insufficient"} and not issues:
            raise ValueError(f"{verdict} interface_audit requires an issue")
        interface_audit = {
            "verdict": verdict,
            "decisive_checks": checks,
            "issues": issues,
        }
    return {
        "status": "completed",
        "message": message,
        "claim_audits": audits,
        "interface_audit": interface_audit,
    }


def materialize_claim_certificates(
    audit: dict[str, Any],
    input_fingerprints: dict[tuple[str, int], str],
    *,
    model_id: str,
    provider: str,
    context_isolated: bool,
) -> list[dict[str, Any]]:
    """Attach trusted runtime identity; Agent output cannot supply these fields."""
    if audit.get("status") != "completed":
        return []
    if not context_isolated:
        raise ValueError("independent semantic verifier context must be isolated")
    certificates = []
    for item in audit.get("claim_audits", []):
        key = (item["claim_id"], item["claim_version"])
        if key not in input_fingerprints:
            raise ValueError("claim audit has no trusted input fingerprint")
        certificates.append(claim_ledger.normalize_certificate({
            "claim_id": item["claim_id"],
            "claim_version": item["claim_version"],
            "verifier_kind": "independent-agent",
            "check_type": "semantic",
            "verdict": item["verdict"],
            "normalized_result": item["normalized_result"],
            "decisive_checks": item["decisive_checks"],
            "input_fingerprint": input_fingerprints[key],
            "verifier_identity": {
                "model_id": model_id,
                "provider": provider,
                "context_isolated": True,
            },
        }))
    return certificates


def normalize_audit(payload: dict[str, Any], expected_target_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("audit output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = _safe_text(payload.get("message", ""), "message", 1_000)
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("audit status must be completed or unsupported")
    audits = []
    seen: set[str] = set()
    for raw in payload.get("target_audits") or []:
        target_id = _safe_text(raw.get("target_id", ""), "target_audits.target_id", 80)
        verdict = str(raw.get("verdict", "")).strip()
        checks = [
            _safe_text(item, "target_audits.decisive_checks", 240)
            for item in raw.get("decisive_checks", [])
            if isinstance(item, str) and item.strip()
        ]
        issues = [
            _safe_text(item, "target_audits.issues", 240)
            for item in raw.get("issues", [])
            if isinstance(item, str) and item.strip()
        ]
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
            "recomputed_result": _safe_text(
                raw.get("recomputed_result", ""),
                "target_audits.recomputed_result",
                500,
            ),
            "decisive_checks": checks[:6],
            "issues": issues[:6],
        })
    if seen != expected_target_ids:
        raise ValueError("audit did not cover every requested target")
    return {"status": "completed", "message": message, "target_audits": audits}


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
