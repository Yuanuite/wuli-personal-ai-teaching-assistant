#!/usr/bin/env python3
"""Versioned, deterministic contracts for non-solving W3 answer rendering."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import teaching_method_policy

BRIEF_SCHEMA = "wuli.w3r-brief.v1"
RENDER_RESULT_SCHEMA = "wuli.w3r-render-result.v1"
RESPONSE_MODES = {"single", "enumerate_all", "prove", "explain"}
TEACHING_ROLES = {"setup", "derivation", "substitution", "conclusion", "check"}
RENDER_STATUSES = {"completed", "needs_render_material", "rejected"}
GATE_STATUSES = {"pass", "retryable", "reject"}
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")

BRIEF_FIELDS = {
    "schema",
    "problem_fingerprint",
    "proof_package_fingerprint",
    "question_targets",
    "final_answers",
    "proof_skeleton",
    "verified_claims",
    "verification_obligations",
    "teaching_cues",
    "method_scope",
    "allowed_symbols",
}
RENDER_RESULT_FIELDS = {
    "schema",
    "status",
    "brief_fingerprint",
    "student_solution_md",
    "teacher_solution_md",
    "claim_span_map",
    "render_gate_report",
    "attempt",
}


def stable_fingerprint(value: Any) -> str:
    """Return a stable, type-preserving SHA-256 fingerprint."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def answer_signature(value: Any) -> str:
    """Normalize harmless typography without claiming algebraic equivalence."""
    text = str(value or "").lower()
    text = text.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉²³", "012345678923"))
    text = text.replace("\\pi", "π").replace("sqrt", "√")
    text = re.sub(r"\\(?:left|right|,|;|!|quad|qquad)", "", text)
    return re.sub(r"[\s，。；;：:、（）()$]", "", text)


def _object(value: Any, field: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown or missing:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise ValueError(f"{field} has invalid fields: {', '.join(details)}")
    return value


def _text(value: Any, field: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not empty and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return result


def _identifier(value: Any, field: str) -> str:
    result = _text(value, field, 80)
    if not _ID.fullmatch(result):
        raise ValueError(f"{field} must be a stable ASCII identifier")
    return result


def _fingerprint(value: Any, field: str) -> str:
    result = _text(value, field, 71).lower()
    if not _SHA256.fullmatch(result):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return result


def _strings(
    value: Any,
    field: str,
    *,
    maximum_items: int = 24,
    maximum_text: int = 500,
    identifiers: bool = False,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    if len(value) > maximum_items:
        raise ValueError(f"{field} has too many items")
    result = []
    for index, raw in enumerate(value):
        item = (
            _identifier(raw, f"{field}[{index}]")
            if identifiers
            else _text(raw, f"{field}[{index}]", maximum_text)
        )
        if item in result:
            raise ValueError(f"{field} must not contain duplicates")
        result.append(item)
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    return result


def normalize_w3r_brief(payload: Any) -> dict[str, Any]:
    """Validate the exact v1 Brief contract and its cross-field references."""
    raw = _object(payload, "brief", BRIEF_FIELDS)
    if raw["schema"] != BRIEF_SCHEMA:
        raise ValueError(f"brief.schema must be {BRIEF_SCHEMA}")
    result: dict[str, Any] = {
        "schema": BRIEF_SCHEMA,
        "problem_fingerprint": _fingerprint(
            raw["problem_fingerprint"], "brief.problem_fingerprint"
        ),
        "proof_package_fingerprint": _fingerprint(
            raw["proof_package_fingerprint"], "brief.proof_package_fingerprint"
        ),
    }

    targets = []
    for index, item in enumerate(raw["question_targets"]):
        item = _object(
            item,
            f"brief.question_targets[{index}]",
            {"target_id", "prompt", "response_mode"},
        )
        mode = _text(item["response_mode"], "response_mode", 24)
        if mode not in RESPONSE_MODES:
            raise ValueError(f"response_mode is invalid: {mode!r}")
        targets.append({
            "target_id": _identifier(item["target_id"], "target_id"),
            "prompt": _text(item["prompt"], "prompt", 2_000),
            "response_mode": mode,
        })
    if not targets:
        raise ValueError("brief.question_targets must not be empty")
    target_ids = [item["target_id"] for item in targets]
    if len(target_ids) != len(set(target_ids)):
        raise ValueError("brief.question_targets contains duplicate target_id")
    result["question_targets"] = targets

    answers = []
    for index, item in enumerate(raw["final_answers"]):
        item = _object(
            item,
            f"brief.final_answers[{index}]",
            {"target_id", "text", "answer_signature", "claim_ids", "complete"},
        )
        target_id = _identifier(item["target_id"], "final_answer.target_id")
        text = _text(item["text"], "final_answer.text", 2_000)
        signature = _text(
            item["answer_signature"], "final_answer.answer_signature", 2_000
        )
        if signature != answer_signature(text):
            raise ValueError("final_answer.answer_signature does not match text")
        if not isinstance(item["complete"], bool):
            raise ValueError("final_answer.complete must be boolean")
        answers.append({
            "target_id": target_id,
            "text": text,
            "answer_signature": signature,
            "claim_ids": _strings(
                item["claim_ids"],
                "final_answer.claim_ids",
                identifiers=True,
                allow_empty=False,
            ),
            "complete": item["complete"],
        })
    if [item["target_id"] for item in answers] != target_ids:
        raise ValueError("final_answers must cover question_targets in the same order")
    result["final_answers"] = answers

    claims = []
    for index, item in enumerate(raw["verified_claims"]):
        item = _object(
            item,
            f"brief.verified_claims[{index}]",
            {"claim_id", "statement", "conditions", "status", "certificate_ids"},
        )
        if item["status"] != "verified":
            raise ValueError("only verified Claims may enter a W3R Brief")
        claims.append({
            "claim_id": _identifier(item["claim_id"], "claim.claim_id"),
            "statement": _text(item["statement"], "claim.statement", 2_000),
            "conditions": _strings(item["conditions"], "claim.conditions"),
            "status": "verified",
            "certificate_ids": _strings(
                item["certificate_ids"],
                "claim.certificate_ids",
                identifiers=True,
                allow_empty=False,
            ),
        })
    claim_ids = [item["claim_id"] for item in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("verified_claims contains duplicate claim_id")
    result["verified_claims"] = claims

    steps = []
    for index, item in enumerate(raw["proof_skeleton"]):
        item = _object(
            item,
            f"brief.proof_skeleton[{index}]",
            {
                "step_id",
                "target_ids",
                "claim_ids",
                "depends_on",
                "statement",
                "formula_latex",
                "conditions",
                "teaching_role",
            },
        )
        role = _text(item["teaching_role"], "step.teaching_role", 24)
        if role not in TEACHING_ROLES:
            raise ValueError(f"teaching_role is invalid: {role!r}")
        steps.append({
            "step_id": _identifier(item["step_id"], "step.step_id"),
            "target_ids": _strings(
                item["target_ids"], "step.target_ids", identifiers=True, allow_empty=False
            ),
            "claim_ids": _strings(
                item["claim_ids"], "step.claim_ids", identifiers=True, allow_empty=False
            ),
            "depends_on": _strings(
                item["depends_on"], "step.depends_on", identifiers=True
            ),
            "statement": _text(item["statement"], "step.statement", 2_000),
            "formula_latex": _text(
                item["formula_latex"], "step.formula_latex", 2_000, empty=True
            ),
            "conditions": _strings(item["conditions"], "step.conditions"),
            "teaching_role": role,
        })
    step_ids = [item["step_id"] for item in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("proof_skeleton contains duplicate step_id")
    result["proof_skeleton"] = steps

    obligations = []
    for index, item in enumerate(raw["verification_obligations"]):
        item = _object(
            item,
            f"brief.verification_obligations[{index}]",
            {"obligation_id", "target_id", "check", "render_as", "resolved"},
        )
        render_as = _text(item["render_as"], "obligation.render_as", 24)
        if render_as not in {"condition", "self_check", "teacher_note"}:
            raise ValueError(f"obligation.render_as is invalid: {render_as!r}")
        if not isinstance(item["resolved"], bool):
            raise ValueError("obligation.resolved must be boolean")
        obligations.append({
            "obligation_id": _identifier(
                item["obligation_id"], "obligation.obligation_id"
            ),
            "target_id": _identifier(item["target_id"], "obligation.target_id"),
            "check": _text(item["check"], "obligation.check", 1_000),
            "render_as": render_as,
            "resolved": item["resolved"],
        })
    result["verification_obligations"] = obligations

    cues = []
    for index, item in enumerate(raw["teaching_cues"]):
        item = _object(
            item,
            f"brief.teaching_cues[{index}]",
            {"cue_id", "step_ids", "instruction"},
        )
        cues.append({
            "cue_id": _identifier(item["cue_id"], "cue.cue_id"),
            "step_ids": _strings(
                item["step_ids"], "cue.step_ids", identifiers=True, allow_empty=False
            ),
            "instruction": _text(item["instruction"], "cue.instruction", 1_000),
        })
    result["teaching_cues"] = cues

    scope = _object(
        raw["method_scope"],
        "brief.method_scope",
        {"level", "max_main_steps", "forbidden_methods"},
    )
    if scope["level"] not in {"high_school", "olympiad_official"}:
        raise ValueError(
            "method_scope.level must be high_school or olympiad_official"
        )
    max_steps = scope["max_main_steps"]
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 5:
        raise ValueError("method_scope.max_main_steps must be between 1 and 5")
    result["method_scope"] = {
        "level": scope["level"],
        "max_main_steps": max_steps,
        "forbidden_methods": _strings(
            scope["forbidden_methods"],
            "method_scope.forbidden_methods",
            maximum_items=16,
            maximum_text=80,
        ),
    }
    result["allowed_symbols"] = _strings(
        raw["allowed_symbols"],
        "brief.allowed_symbols",
        maximum_items=64,
        maximum_text=80,
    )

    target_set, claim_set, step_set = set(target_ids), set(claim_ids), set(step_ids)
    for answer in answers:
        if not set(answer["claim_ids"]).issubset(claim_set):
            raise ValueError("final_answer references an unknown Claim")
        supporting_claims = [
            item for item in claims if item["claim_id"] in answer["claim_ids"]
        ]
        if not any(
            answer_signature(item["statement"]) == answer["answer_signature"]
            for item in supporting_claims
        ):
            raise ValueError("final_answer signature is not sourced by its Claims")
        if (
            next(item for item in targets if item["target_id"] == answer["target_id"])[
                "response_mode"
            ]
            == "enumerate_all"
            and not answer["complete"]
        ):
            raise ValueError("enumerate_all final answer must declare complete=true")
    for step in steps:
        if not set(step["target_ids"]).issubset(target_set):
            raise ValueError("proof step references an unknown target")
        if not set(step["claim_ids"]).issubset(claim_set):
            raise ValueError("proof step references an unknown Claim")
        if not set(step["depends_on"]).issubset(step_set):
            raise ValueError("proof step has a dangling dependency")
        expected_conditions = list(dict.fromkeys(
            condition
            for claim in claims
            if claim["claim_id"] in step["claim_ids"]
            for condition in claim["conditions"]
        ))
        if step["conditions"] != expected_conditions:
            raise ValueError("proof step conditions do not match its Claims")
    for item in obligations:
        if item["target_id"] not in target_set:
            raise ValueError("verification obligation references an unknown target")
    for cue in cues:
        if not set(cue["step_ids"]).issubset(step_set):
            raise ValueError("teaching cue references an unknown step")

    _topological_steps(steps)
    return result


def _topological_steps(steps: list[dict[str, Any]]) -> list[str]:
    parents = {item["step_id"]: set(item["depends_on"]) for item in steps}
    ready = sorted(step_id for step_id, deps in parents.items() if not deps)
    order = []
    while ready:
        step_id = ready.pop(0)
        order.append(step_id)
        for child_id in sorted(parents):
            if step_id in parents[child_id]:
                parents[child_id].remove(step_id)
                if not parents[child_id] and child_id not in order and child_id not in ready:
                    ready.append(child_id)
                    ready.sort()
    if len(order) != len(steps):
        raise ValueError("proof_skeleton contains a dependency cycle")
    return order


def brief_fingerprint(payload: Any) -> str:
    return stable_fingerprint(normalize_w3r_brief(payload))


def preflight_w3r_brief(
    payload: Any,
    *,
    expected_proof_package_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Return a non-throwing material/readiness decision for orchestration."""
    try:
        brief = normalize_w3r_brief(payload)
    except ValueError as exc:
        return {
            "status": "needs_render_material",
            "violations": [{"code": "invalid-brief", "message": str(exc)}],
        }
    violations = []
    if (
        expected_proof_package_fingerprint is not None
        and brief["proof_package_fingerprint"]
        != expected_proof_package_fingerprint
    ):
        violations.append({
            "code": "proof-package-fingerprint-mismatch",
            "message": "Brief does not bind the expected frozen Proof Package.",
        })
    step_targets = {
        target_id
        for step in brief["proof_skeleton"]
        for target_id in step["target_ids"]
    }
    for target in brief["question_targets"]:
        target_id = target["target_id"]
        target_steps = [
            item for item in brief["proof_skeleton"] if target_id in item["target_ids"]
        ]
        if target_id not in step_targets or not any(
            item["teaching_role"] == "conclusion" for item in target_steps
        ):
            violations.append({
                "code": "target-proof-incomplete",
                "target_id": target_id,
                "message": "Target lacks a conclusion-bearing Proof Skeleton path.",
            })
    if len(brief["proof_skeleton"]) > brief["method_scope"]["max_main_steps"]:
        violations.append({
            "code": "proof-mainline-too-long",
            "message": (
                f"Proof Skeleton has {len(brief['proof_skeleton'])} steps; "
                f"maximum is {brief['method_scope']['max_main_steps']}."
            ),
        })
    for item in brief["verification_obligations"]:
        if not item["resolved"]:
            violations.append({
                "code": "unresolved-verification-obligation",
                "target_id": item["target_id"],
                "message": item["check"],
            })
    return {
        "status": "pass" if not violations else "needs_render_material",
        "brief_fingerprint": brief_fingerprint(brief),
        "violations": violations,
        "brief": brief,
    }


def normalize_render_result(payload: Any) -> dict[str, Any]:
    raw = _object(payload, "render_result", RENDER_RESULT_FIELDS)
    if raw["schema"] != RENDER_RESULT_SCHEMA:
        raise ValueError(f"render_result.schema must be {RENDER_RESULT_SCHEMA}")
    status = _text(raw["status"], "render_result.status", 32)
    if status not in RENDER_STATUSES:
        raise ValueError(f"render_result.status is invalid: {status!r}")
    attempt = raw["attempt"]
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt not in {1, 2}:
        raise ValueError("render_result.attempt must be 1 or 2")
    spans = []
    for index, item in enumerate(raw["claim_span_map"]):
        item = _object(
            item,
            f"render_result.claim_span_map[{index}]",
            {
                "document",
                "section_id",
                "claim_ids",
                "step_ids",
                "text_fingerprint",
            },
        )
        document = _text(item["document"], "span.document", 16)
        if document not in {"student", "teacher"}:
            raise ValueError("span.document must be student or teacher")
        spans.append({
            "document": document,
            "section_id": _identifier(item["section_id"], "span.section_id"),
            "claim_ids": _strings(
                item["claim_ids"], "span.claim_ids", identifiers=True, allow_empty=False
            ),
            "step_ids": _strings(
                item["step_ids"], "span.step_ids", identifiers=True, allow_empty=False
            ),
            "text_fingerprint": _fingerprint(
                item["text_fingerprint"], "span.text_fingerprint"
            ),
        })
    gate = _object(
        raw["render_gate_report"],
        "render_result.render_gate_report",
        {"status", "violations", "metrics"},
    )
    gate_status = _text(gate["status"], "render_gate_report.status", 16)
    if gate_status not in GATE_STATUSES:
        raise ValueError("render_gate_report.status is invalid")
    if not isinstance(gate["violations"], list) or not all(
        isinstance(item, dict) for item in gate["violations"]
    ):
        raise ValueError("render_gate_report.violations must be an object array")
    if not isinstance(gate["metrics"], dict):
        raise ValueError("render_gate_report.metrics must be an object")
    return {
        "schema": RENDER_RESULT_SCHEMA,
        "status": status,
        "brief_fingerprint": _fingerprint(
            raw["brief_fingerprint"], "render_result.brief_fingerprint"
        ),
        "student_solution_md": _text(
            raw["student_solution_md"], "student_solution_md", 100_000, empty=True
        ),
        "teacher_solution_md": _text(
            raw["teacher_solution_md"], "teacher_solution_md", 120_000, empty=True
        ),
        "claim_span_map": spans,
        "render_gate_report": {
            "status": gate_status,
            "violations": gate["violations"],
            "metrics": gate["metrics"],
        },
        "attempt": attempt,
    }


def infer_response_mode(prompt: str, answer_type: str = "") -> str:
    text = f"{prompt} {answer_type}"
    if re.search(r"所有|全部|各个|枚举|解集|范围", text):
        return "enumerate_all"
    if re.search(r"证明|论证", text):
        return "prove"
    if re.search(r"解释|说明|为什么", text):
        return "explain"
    return "single"


def build_w3r_brief(
    problem: str,
    blueprint: dict[str, Any],
    proof_package: dict[str, Any],
    *,
    method_profile: str = teaching_method_policy.DEFAULT_PROFILE,
) -> dict[str, Any]:
    """Project only a verified W3 Proof Package; never synthesize physics."""
    if not isinstance(proof_package, dict):
        return {
            "status": "needs_render_material",
            "violations": [{"code": "proof-package-missing"}],
        }
    aggregation_status = str(
        proof_package.get("aggregation_status")
        or proof_package.get("status")
        or ""
    ).upper()
    if aggregation_status != "VERIFIED":
        return {
            "status": "needs_render_material",
            "violations": [{
                "code": "proof-package-not-verified",
                "message": f"Proof Package status is {aggregation_status or 'missing'}.",
            }],
        }
    if not isinstance(blueprint, dict):
        return {
            "status": "needs_render_material",
            "violations": [{"code": "blueprint-missing"}],
        }

    raw_claims = [
        item for item in proof_package.get("claims", [])
        if isinstance(item, dict) and str(item.get("status", "")).lower() == "verified"
    ]
    certificates = [
        item for item in proof_package.get("certificates", [])
        if isinstance(item, dict) and str(item.get("verdict", "")).lower() == "pass"
    ]
    certs_by_claim: dict[str, list[str]] = {}
    for index, certificate in enumerate(certificates, 1):
        claim_id = str(certificate.get("claim_id", "")).strip()
        if claim_id:
            certs_by_claim.setdefault(claim_id, []).append(
                f"vc-{index}-{claim_id}"
            )
    verified_claims = []
    for claim in raw_claims:
        claim_id = str(claim.get("id") or claim.get("claim_id") or "").strip()
        if not claim_id or not certs_by_claim.get(claim_id):
            continue
        verified_claims.append({
            "claim_id": claim_id,
            "statement": str(claim.get("statement", "")).strip(),
            "conditions": [
                str(item).strip()
                for item in claim.get("conditions", [])
                if str(item).strip()
            ],
            "status": "verified",
            "certificate_ids": certs_by_claim[claim_id],
        })
    verified_ids = {item["claim_id"] for item in verified_claims}

    raw_targets = [
        item for item in blueprint.get("question_targets", [])
        if isinstance(item, dict)
    ]
    question_targets = [{
        "target_id": str(item.get("id", "")).strip(),
        "prompt": str(item.get("prompt", "")).strip(),
        "response_mode": infer_response_mode(
            str(item.get("prompt", "")), str(item.get("answer_type", ""))
        ),
    } for item in raw_targets]
    final_answers = []
    final_by_target: dict[str, dict[str, Any]] = {}
    for item in proof_package.get("final_answers", []):
        if not isinstance(item, dict) or str(item.get("status", "")).lower() != "verified":
            continue
        claim_id = str(item.get("claim_id", "")).strip()
        for target_id in item.get("target_ids", []):
            target_id = str(target_id).strip()
            if target_id and claim_id in verified_ids:
                final_by_target[target_id] = item
    for target in question_targets:
        target_id = target["target_id"]
        item = final_by_target.get(target_id)
        if not item:
            continue
        text = str(item.get("statement", "")).strip()
        final_answers.append({
            "target_id": target_id,
            "text": text,
            "answer_signature": answer_signature(text),
            "claim_ids": [str(item["claim_id"])],
            "complete": target["response_mode"] != "enumerate_all"
            or bool(item.get("complete", False)),
        })

    claim_by_id = {
        str(item.get("id") or item.get("claim_id")): item for item in raw_claims
    }
    final_claim_ids = {
        str(item.get("claim_id", ""))
        for item in final_by_target.values()
        if str(item.get("claim_id", "")) in verified_ids
    }
    selected_claim_ids: set[str] = set()

    def select_ancestors(claim_id: str) -> None:
        if claim_id in selected_claim_ids:
            return
        selected_claim_ids.add(claim_id)
        for dependency_id in claim_by_id.get(claim_id, {}).get("depends_on", []):
            dependency_id = str(dependency_id)
            if dependency_id in verified_ids:
                select_ancestors(dependency_id)

    for claim_id in final_claim_ids:
        select_ancestors(claim_id)
    pending = set(selected_claim_ids)
    ordered_ids: list[str] = []
    while pending:
        ready = sorted(
            claim_id
            for claim_id in pending
            if not (
                {
                    str(item)
                    for item in claim_by_id[claim_id].get("depends_on", [])
                }
                & pending
            )
        )
        if not ready:
            return {
                "status": "needs_render_material",
                "violations": [{"code": "claim-dependency-cycle"}],
            }
        ordered_ids.extend(ready)
        pending.difference_update(ready)
    verified_by_id = {
        item["claim_id"]: item for item in verified_claims
    }
    renderable_ids = [
        claim_id
        for claim_id in ordered_ids
        if str(claim_by_id[claim_id].get("kind", "")) != "premise"
    ]
    method_scope = teaching_method_policy.scope(method_profile)
    max_steps = method_scope["max_main_steps"]
    group_size = max(1, (len(renderable_ids) + max_steps - 1) // max_steps)
    claim_groups = [
        renderable_ids[index:index + group_size]
        for index in range(0, len(renderable_ids), group_size)
    ]
    step_by_claim_id = {
        claim_id: f"s{group_index}"
        for group_index, group in enumerate(claim_groups, 1)
        for claim_id in group
    }
    target_set = {target["target_id"] for target in question_targets}
    proof_skeleton = []
    for group_index, group in enumerate(claim_groups, 1):
        group_set = set(group)
        sources = [claim_by_id[claim_id] for claim_id in group]
        items = [verified_by_id[claim_id] for claim_id in group]
        target_ids = list(dict.fromkeys(
            str(target_id)
            for source in sources
            for target_id in source.get("target_ids", [])
            if str(target_id) in target_set
        ))
        if not target_ids:
            continue
        dependencies = sorted({
            step_by_claim_id[str(dependency_id)]
            for source in sources
            for dependency_id in source.get("depends_on", [])
            if str(dependency_id) in step_by_claim_id
            and str(dependency_id) not in group_set
        })
        statements = list(dict.fromkeys(
            str(item["statement"]).strip() for item in items
        ))
        statement = "；".join(statements)
        if len(statement) > 2_000:
            return {
                "status": "needs_render_material",
                "violations": [{
                    "code": "proof-step-text-too-long",
                    "message": (
                        "Verified atomic Claims cannot be grouped within the "
                        "non-solving five-step rendering budget."
                    ),
                }],
            }
        conditions = list(dict.fromkeys(
            condition
            for item in verified_claims
            if item["claim_id"] in group_set
            for condition in item["conditions"]
        ))
        proof_skeleton.append({
            "step_id": f"s{group_index}",
            "target_ids": target_ids,
            "claim_ids": group,
            "depends_on": dependencies,
            "statement": statement,
            "formula_latex": (
                str(sources[0].get("formula_latex", "")).strip()
                if len(sources) == 1
                else ""
            ),
            "conditions": conditions,
            "teaching_role": (
                "conclusion"
                if any(str(source.get("kind", "")) == "final" for source in sources)
                else "derivation"
            ),
        })

    unresolved_ids = {
        str(item.get("obligation_id") or item.get("id") or "")
        for item in proof_package.get("unresolved_obligations", [])
        if isinstance(item, dict)
    }
    verification_obligations = [{
        "obligation_id": str(item.get("id", "")).strip(),
        "target_id": str(item.get("target_id", "")).strip(),
        "check": str(item.get("check", "")).strip(),
        "render_as": (
            "condition" if str(item.get("risk", "")) == "critical" else "self_check"
        ),
        "resolved": str(item.get("id", "")).strip() not in unresolved_ids,
    } for item in blueprint.get("verification_obligations", []) if isinstance(item, dict)]

    package_fingerprint = stable_fingerprint(proof_package)
    brief = {
        "schema": BRIEF_SCHEMA,
        "problem_fingerprint": stable_fingerprint({"problem": str(problem).strip()}),
        "proof_package_fingerprint": package_fingerprint,
        "question_targets": question_targets,
        "final_answers": final_answers,
        "proof_skeleton": proof_skeleton,
        "verified_claims": verified_claims,
        "verification_obligations": verification_obligations,
        "teaching_cues": [],
        "method_scope": {
            "level": method_scope["level"],
            "max_main_steps": method_scope["max_main_steps"],
            "forbidden_methods": method_scope["forbidden_methods"],
        },
        "allowed_symbols": [
            str(item).strip()
            for item in proof_package.get("allowed_symbols", [])
            if str(item).strip()
        ],
    }
    preflight = preflight_w3r_brief(
        brief, expected_proof_package_fingerprint=package_fingerprint
    )
    if preflight["status"] != "pass":
        return preflight
    return {
        "status": "completed",
        "brief": preflight["brief"],
        "brief_fingerprint": preflight["brief_fingerprint"],
        "violations": [],
    }
