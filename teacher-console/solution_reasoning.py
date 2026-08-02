#!/usr/bin/env python3
"""W3 structured solving and evidence-based adjudication contracts."""

from __future__ import annotations

import re
from typing import Any

import claim_ledger
import cognitive_loop
import structured_text

SOLUTION_CONTRACT = "wuli.solution-reasoning.v2.1"
ADJUDICATION_CONTRACT = "wuli.solution-adjudicate.v1"

TEXT_ARRAY = {"type": "array", "items": {"type": "string"}, "maxItems": 12}
STRING_MAP = {
    "type": "object",
    "additionalProperties": {"type": "string"},
    "maxProperties": 64,
}
STAGE_INTERFACE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "stage_id": {"type": "string"},
        "coordinate_frame": {"type": "string"},
        "time_origin": {"type": "string"},
        "directions": STRING_MAP,
        "entry_state": STRING_MAP,
        "exit_state": STRING_MAP,
        "required_entry_keys": TEXT_ARRAY,
        "carried_state_keys": TEXT_ARRAY,
    },
    "required": [
        "stage_id",
        "coordinate_frame",
        "time_origin",
        "directions",
        "entry_state",
        "exit_state",
        "required_entry_keys",
        "carried_state_keys",
    ],
}
STAGE_TRANSITION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "from_stage": {"type": "string"},
        "to_stage": {"type": "string"},
        "event": {"type": "string"},
        "state_mapping": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "from_key": {"type": "string"},
                    "to_key": {"type": "string"},
                    "transform": {"type": ["string", "null"]},
                },
                "required": ["from_key", "to_key", "transform"],
            },
        },
        "introduced_entry_keys": TEXT_ARRAY,
        "coordinate_transform": {"type": ["string", "null"]},
        "time_transform": {"type": ["string", "null"]},
        "direction_transform": {"type": ["string", "null"]},
    },
    "required": [
        "from_stage",
        "to_stage",
        "event",
        "state_mapping",
        "introduced_entry_keys",
        "coordinate_transform",
        "time_transform",
        "direction_transform",
    ],
}

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
        "stage_interfaces": {
            "type": ["array", "null"],
            "items": STAGE_INTERFACE_SCHEMA,
            "maxItems": 16,
        },
        "stage_transitions": {
            "type": ["array", "null"],
            "items": STAGE_TRANSITION_SCHEMA,
            "maxItems": 15,
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
        "stage_interfaces",
        "stage_transitions",
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
            "必须为蓝图每个物理阶段输出一个 stage_interface；entry_state/exit_state 使用"
            "稳定 ASCII key 和可直接比较的字符串值。相邻阶段必须输出 stage_transition，"
            "明确事件和状态映射；只有真实发生坐标、时间原点、方向或状态变换时才填写"
            "对应 transform，否则填 null，禁止用空泛变换掩盖不一致。"
            "事件中新加入、无法从上一阶段出口映射的入口量必须列入"
            "introduced_entry_keys；它们仍必须存在于下一阶段 entry_state。"
            "carried_state_keys 只是希望继续跟踪的状态量提示，可以在阶段内演化；"
            "跨阶段连续性由 exit_state、下一阶段 entry_state 和 state_mapping 检查。"
            "不得把检索片段当作答案；当前题干优先，证据只提供高中方法及适用条件。"
            "必须覆盖蓝图中的全部题目目标和校验义务。"
            "status=unsupported 时 targets、stage_results、stage_interfaces、"
            "stage_transitions、option_verdicts、blueprint_audit 均为 null。"
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
    return structured_text.reject_unsupported_controls(value, field).strip()[:maximum]


def _clean_optional_text(value: Any, field: str, maximum: int = 1000) -> str:
    if value is None:
        return ""
    return structured_text.reject_unsupported_controls(value, field).strip()[:maximum]


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


def normalize_solution(
    payload: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    require_stage_interfaces: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("solution output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = _clean_optional_text(payload.get("message", ""), "message")
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("solution status must be completed or unsupported")

    target_ids = {str(item.get("id", "")) for item in blueprint.get("question_targets", [])}
    obligation_ids = {
        str(item.get("id", "")) for item in blueprint.get("verification_obligations", [])
    }
    stage_ids = {str(item.get("id", "")) for item in blueprint.get("physical_stages", [])}
    ordered_stage_ids = [
        str(item.get("id", "")) for item in blueprint.get("physical_stages", [])
    ]
    step_to_stage = {
        str(item.get("step_id", "")): str(item.get("stage_id", ""))
        for item in blueprint.get("stage_step_links", [])
        if str(item.get("step_id", "")) and str(item.get("stage_id", ""))
    }
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
        if any(item.startswith("MFC_") for item in covered):
            domain_text = "；".join([
                *relations,
                *_clean_list(raw.get("conditions"), "targets.conditions"),
            ])
            has_free_surface_height = bool(
                re.search(
                    r"(?:\bH(?:_oil)?\b|油柱高度|自由液面|油面位置|y_s)",
                    domain_text,
                    re.IGNORECASE,
                )
            )
            has_full_oil_interval = bool(
                re.search(
                    r"(?:"
                    r"(?:F_?oil|F_?油|油侧.{0,12}(?:合力|压力)).{0,160}"
                    r"(?:H(?:_oil)?\s*(?:\^?2|²)|(?:0|from_0).{0,12}H)"
                    r"|全润湿.{0,40}(?:y_s|H)"
                    r"|(?:integral|∫).{0,24}(?:y_s).{0,24}h"
                    r"|(?:integral|\\int|∫).{0,48}H(?:_oil)?"
                    r")",
                    domain_text,
                    re.IGNORECASE,
                )
            )
            if not (has_free_surface_height and has_full_oil_interval):
                raise ValueError(
                    "multi-fluid column solution must determine the free-surface "
                    "height and cover the full oil-wetted plate interval"
                )
            final_answer = str(raw.get("final_answer", ""))
            has_density_ratio = bool(
                re.search(
                    r"(?:rho_?0|ρ₀).{0,120}(?:/|}\s*\{).{0,80}"
                    r"(?:rho_?\{?(?:\\mathrm\{)?oil|ρ_?oil)",
                    final_answer,
                    re.IGNORECASE,
                )
            )
            if not has_density_ratio:
                raise ValueError(
                    "multi-fluid column final answer must retain the density "
                    "ratio introduced by the actual oil-column height"
                )
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

    stage_result_parts: dict[str, list[str]] = {}
    for raw in payload.get("stage_results") or []:
        stage_id = _clean_text(raw.get("stage_id"), "stage_results.stage_id", 40)
        if stage_id not in stage_ids and step_to_stage.get(stage_id) in stage_ids:
            stage_id = step_to_stage[stage_id]
        if stage_id not in stage_ids:
            raise ValueError("stage result references an unknown physical stage")
        result = _clean_text(raw.get("result"), "stage_results.result")
        if result not in stage_result_parts.setdefault(stage_id, []):
            stage_result_parts[stage_id].append(result)
    stage_results = [
        {
            "stage_id": stage_id,
            "result": "；".join(stage_result_parts[stage_id]),
        }
        for stage_id in ordered_stage_ids
        if stage_id in stage_result_parts
    ]

    stage_interfaces = [
        cognitive_loop.normalize_stage_interface(item)
        for item in (payload.get("stage_interfaces") or [])
    ]
    interface_ids = [item["stage_id"] for item in stage_interfaces]
    if require_stage_interfaces and (
        len(interface_ids) != len(set(interface_ids)) or set(interface_ids) != stage_ids
    ):
        raise ValueError("solution stage_interfaces must cover every physical stage exactly once")

    stage_transitions = [
        cognitive_loop.normalize_stage_transition(item)
        for item in (payload.get("stage_transitions") or [])
    ]
    expected_pairs = list(zip(ordered_stage_ids, ordered_stage_ids[1:]))
    actual_pairs = [
        (item["from_stage"], item["to_stage"]) for item in stage_transitions
    ]
    if require_stage_interfaces and actual_pairs != expected_pairs:
        raise ValueError(
            "solution stage_transitions must connect each adjacent physical stage in order"
        )

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
        "stage_interfaces": stage_interfaces,
        "stage_transitions": stage_transitions,
        "option_verdicts": option_verdicts,
        "blueprint_audit": {
            "status": audit_status,
            "covered_target_ids": sorted(audit_targets),
            "covered_obligation_ids": sorted(audit_obligations),
            "revisions": revisions,
        },
    }


def project_claim_ledger(
    payload: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    input_fingerprint: str,
    snapshot_version: int = 1,
) -> dict[str, Any]:
    """Compatibility projection from the current solution contract."""
    normalized = normalize_solution(
        payload, blueprint, require_stage_interfaces=False
    )
    if normalized.get("status") != "completed":
        raise ValueError("unsupported solution cannot be projected to a claim ledger")
    return claim_ledger.project_legacy_solution(
        normalized,
        blueprint,
        input_fingerprint=input_fingerprint,
        snapshot_version=snapshot_version,
    )


def normalize_adjudication(
    payload: dict[str, Any], expected_target_ids: set[str]
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("adjudication output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = _clean_optional_text(payload.get("message", ""), "message")
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
            "message": _clean_optional_text(payload.get("message", ""), "message"),
            "target_decisions": [
                {
                    "target_id": target_id,
                    "selected_result": _clean_text(
                        targets[target_id].get("final_answer", ""),
                        "target_decisions.selected_result",
                    ),
                    "decision": "solver-a",
                    "decisive_relation": _clean_text(
                        next(iter(audits[target_id].get("decisive_checks", [])), ""),
                        "target_decisions.decisive_relation",
                    ),
                    "reason": "两个独立求解结果被仲裁判定为等价，且独立验证器已复算通过。",
                }
                for target_id in sorted(expected_target_ids)
            ],
        }
