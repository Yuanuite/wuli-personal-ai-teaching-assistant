#!/usr/bin/env python3
"""W3 structured solving and evidence-based adjudication contracts."""

from __future__ import annotations

from typing import Any

SOLUTION_CONTRACT = "wuli.solution-reasoning.v1"
ADJUDICATION_CONTRACT = "wuli.solution-adjudicate.v1"

TEXT_ARRAY = {"type": "array", "items": {"type": "string"}, "maxItems": 12}

SOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "targets": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "final_answer": {"type": "string"},
                    "supporting_relations": TEXT_ARRAY,
                    "conditions": TEXT_ARRAY,
                    "covered_obligation_ids": TEXT_ARRAY,
                },
                "required": [
                    "id",
                    "final_answer",
                    "supporting_relations",
                    "conditions",
                    "covered_obligation_ids",
                ],
            },
            "maxItems": 12,
        },
        "stage_results": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stage_id": {"type": "string"},
                    "result": {"type": "string"},
                },
                "required": ["stage_id", "result"],
            },
            "maxItems": 16,
        },
        "option_verdicts": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "option": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["correct", "incorrect", "not-applicable"]},
                    "reason": {"type": "string"},
                },
                "required": ["option", "verdict", "reason"],
            },
            "maxItems": 12,
        },
        "blueprint_audit": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["followed", "revised-equivalent"]},
                "covered_target_ids": TEXT_ARRAY,
                "covered_obligation_ids": TEXT_ARRAY,
                "revisions": TEXT_ARRAY,
            },
            "required": [
                "status",
                "covered_target_ids",
                "covered_obligation_ids",
                "revisions",
            ],
        },
    },
    "required": [
        "status",
        "message",
        "targets",
        "stage_results",
        "option_verdicts",
        "blueprint_audit",
    ],
}


ADJUDICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "target_decisions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_id": {"type": "string"},
                    "selected_result": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["solver-a", "solver-b", "recomputed", "teacher-check"],
                    },
                    "decisive_relation": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "target_id",
                    "selected_result",
                    "decision",
                    "decisive_relation",
                    "reason",
                ],
            },
            "maxItems": 12,
        },
    },
    "required": ["status", "message", "target_decisions"],
}


def output_contract(*, role: str = "solver-a") -> dict[str, Any]:
    independent = (
        "你是盲解 Solver B：不得读取 Solver A 的输出，必须独立建立并复算关系。"
        if role == "solver-b"
        else "你是 Solver A：按双层蓝图求解；蓝图有误时可作等价修订，但必须记录。"
    )
    return {
        "name": f"{SOLUTION_CONTRACT}.{role}",
        "schema": SOLUTION_SCHEMA,
        "instructions": (
            "只输出结构化求解结果，不写教学 Markdown。"
            f"{independent}"
            "每个目标给出最终结论、可复算的决定性关系、成立条件和已覆盖校验义务。"
            "不得把检索片段当作答案；当前题干优先，证据只提供高中方法及适用条件。"
            "必须覆盖蓝图中的全部题目目标和校验义务。"
            "status=unsupported 时 targets、stage_results、option_verdicts、blueprint_audit 均为 null。"
        ),
    }


def adjudication_output_contract() -> dict[str, Any]:
    return {
        "name": ADJUDICATION_CONTRACT,
        "schema": ADJUDICATION_SCHEMA,
        "instructions": (
            "仅处理发生冲突的题目目标。不得按多数票选择；必须比较当前题干、适用条件、"
            "证据可靠性和可复算关系。能够独立复算时选择或重算结论；仍无法确定时标记"
            "teacher-check，并给教师一条决定性核对关系。"
        ),
    }


def _clean_text(value: Any, field: str, maximum: int = 800) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()[:maximum]


def _clean_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for raw in value:
        text = _clean_text(raw, field, 300)
        if text not in result:
            result.append(text)
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    return result[:12]


def normalize_solution(payload: dict[str, Any], blueprint: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("solution output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = str(payload.get("message", "")).strip()[:1000]
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("solution status must be completed or unsupported")

    target_ids = {str(item.get("id", "")) for item in blueprint.get("question_targets", [])}
    obligation_ids = {
        str(item.get("id", "")) for item in blueprint.get("verification_obligations", [])
    }
    stage_ids = {str(item.get("id", "")) for item in blueprint.get("physical_stages", [])}
    targets = []
    seen_targets: set[str] = set()
    covered_obligations: set[str] = set()
    for raw in payload.get("targets") or []:
        target_id = _clean_text(raw.get("id"), "targets.id", 40)
        if target_id not in target_ids or target_id in seen_targets:
            raise ValueError("solution target is unknown or duplicated")
        relations = _clean_list(
            raw.get("supporting_relations"), "targets.supporting_relations", allow_empty=False
        )
        covered = _clean_list(
            raw.get("covered_obligation_ids"), "targets.covered_obligation_ids"
        )
        if not set(covered).issubset(obligation_ids):
            raise ValueError("solution covers an unknown verification obligation")
        seen_targets.add(target_id)
        covered_obligations.update(covered)
        targets.append({
            "id": target_id,
            "final_answer": _clean_text(raw.get("final_answer"), "targets.final_answer"),
            "supporting_relations": relations,
            "conditions": _clean_list(raw.get("conditions"), "targets.conditions"),
            "covered_obligation_ids": covered,
        })
    if seen_targets != target_ids:
        raise ValueError("solution did not cover every question target")

    stage_results = []
    seen_stages: set[str] = set()
    for raw in payload.get("stage_results") or []:
        stage_id = _clean_text(raw.get("stage_id"), "stage_results.stage_id", 40)
        if stage_id not in stage_ids or stage_id in seen_stages:
            raise ValueError("stage result is unknown or duplicated")
        seen_stages.add(stage_id)
        stage_results.append({
            "stage_id": stage_id,
            "result": _clean_text(raw.get("result"), "stage_results.result"),
        })

    option_verdicts = []
    seen_options: set[str] = set()
    for raw in payload.get("option_verdicts") or []:
        option = _clean_text(raw.get("option"), "option_verdicts.option", 20).upper()
        verdict = str(raw.get("verdict", "")).strip()
        if option in seen_options or verdict not in {"correct", "incorrect", "not-applicable"}:
            raise ValueError("option verdict is duplicated or invalid")
        seen_options.add(option)
        option_verdicts.append({
            "option": option,
            "verdict": verdict,
            "reason": _clean_text(raw.get("reason"), "option_verdicts.reason"),
        })

    audit = payload.get("blueprint_audit")
    if not isinstance(audit, dict):
        raise ValueError("blueprint_audit must be an object")
    audit_status = str(audit.get("status", "")).strip()
    if audit_status not in {"followed", "revised-equivalent"}:
        raise ValueError("blueprint audit status is invalid")
    audit_targets = set(_clean_list(audit.get("covered_target_ids"), "covered_target_ids"))
    audit_obligations = set(
        _clean_list(audit.get("covered_obligation_ids"), "covered_obligation_ids")
    )
    if audit_targets != target_ids or audit_obligations != obligation_ids:
        raise ValueError("blueprint audit must cover every target and obligation")
    if covered_obligations != obligation_ids:
        raise ValueError("target results did not cover every verification obligation")
    revisions = _clean_list(audit.get("revisions"), "blueprint_audit.revisions")
    if audit_status == "revised-equivalent" and not revisions:
        raise ValueError("revised-equivalent requires at least one revision")

    return {
        "status": "completed",
        "message": message,
        "targets": targets,
        "stage_results": stage_results,
        "option_verdicts": option_verdicts,
        "blueprint_audit": {
            "status": audit_status,
            "covered_target_ids": sorted(audit_targets),
            "covered_obligation_ids": sorted(audit_obligations),
            "revisions": revisions,
        },
    }


def normalize_adjudication(
    payload: dict[str, Any], expected_target_ids: set[str]
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("adjudication output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = str(payload.get("message", "")).strip()[:1000]
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("adjudication status must be completed or unsupported")
    decisions = []
    seen: set[str] = set()
    for raw in payload.get("target_decisions") or []:
        target_id = _clean_text(raw.get("target_id"), "target_decisions.target_id", 40)
        decision = str(raw.get("decision", "")).strip()
        if target_id not in expected_target_ids or target_id in seen:
            raise ValueError("adjudication target is unknown or duplicated")
        if decision not in {"solver-a", "solver-b", "recomputed", "teacher-check"}:
            raise ValueError("adjudication decision is invalid")
        seen.add(target_id)
        decisions.append({
            "target_id": target_id,
            "selected_result": _clean_text(
                raw.get("selected_result"), "target_decisions.selected_result"
            ),
            "decision": decision,
            "decisive_relation": _clean_text(
                raw.get("decisive_relation"), "target_decisions.decisive_relation"
            ),
            "reason": _clean_text(raw.get("reason"), "target_decisions.reason"),
        })
    if seen != expected_target_ids:
        raise ValueError("adjudication did not cover every conflict target")
    return {"status": "completed", "message": message, "target_decisions": decisions}


def normalize_adjudication_with_verified_equivalence(
    payload: dict[str, Any],
    expected_target_ids: set[str],
    *,
    solver_a: dict[str, Any],
    verifier: dict[str, Any] | None,
) -> dict[str, Any]:
    """Accept an adjudicator's no-conflict finding only after independent passes."""
    try:
        return normalize_adjudication(payload, expected_target_ids)
    except ValueError:
        raw_decisions = payload.get("target_decisions") if isinstance(payload, dict) else None
        if str(payload.get("status", "")).strip().lower() != "completed" or raw_decisions:
            raise
        passed = {
            str(item.get("target_id", ""))
            for item in (verifier or {}).get("target_audits", [])
            if item.get("verdict") == "pass"
        }
        if not expected_target_ids.issubset(passed):
            raise
        targets = {
            str(item.get("id", "")): item for item in solver_a.get("targets", [])
        }
        audits = {
            str(item.get("target_id", "")): item
            for item in (verifier or {}).get("target_audits", [])
        }
        if not expected_target_ids.issubset(targets):
            raise
        return {
            "status": "completed",
            "message": str(payload.get("message", "")).strip()[:1000],
            "target_decisions": [
                {
                    "target_id": target_id,
                    "selected_result": str(targets[target_id].get("final_answer", "")).strip(),
                    "decision": "solver-a",
                    "decisive_relation": str(
                        next(iter(audits[target_id].get("decisive_checks", [])), "")
                    ).strip()[:800],
                    "reason": "两个独立求解结果被仲裁判定为等价，且独立验证器已复算通过。",
                }
                for target_id in sorted(expected_target_ids)
            ],
        }
