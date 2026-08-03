#!/usr/bin/env python3
"""Versioned, deterministic W2/W3 production routing and fallback policy."""

from __future__ import annotations

from typing import Any, cast

import analysis_artifacts
import problem_decomposition
import w3r_contract

POLICY_VERSION = "wuli-analysis-adaptive-v1"
CONFIG_SCHEMA_VERSION = 1
MODES = {"off", "gray", "default"}
W3_FAILURE_POLICIES = {"fallback-w2", "stop"}
DEFAULT_CONFIG = {
    "schema_version": CONFIG_SCHEMA_VERSION,
    "policy_version": POLICY_VERSION,
    "mode": "off",
    "gray_entry_ids": [],
    "max_agent_calls": 6,
    "max_teacher_focus": 2,
    "max_latency_seconds": 900,
    "w3_failure_policy": "fallback-w2",
}

W3R_POLICY_VERSION = "wuli-w3r-routing-v1"
W3R_CONFIG_SCHEMA_VERSION = 1
W3R_MODES = {"off", "shadow", "gray", "default"}
W3R_EVIDENCE_FIELDS = {
    "report_digest",
    "paired_case_count",
    "teacher_reviewed_case_count",
    "fresh_holdout_case_count",
    "fresh_holdout_target_count",
    "final_answer_fidelity",
    "claim_support_coverage",
    "condition_retention",
    "target_coverage",
    "latex_validity",
    "unsupported_claim_rate",
    "teacher_readability_preference",
    "teacher_edit_rate_non_regression",
}
DEFAULT_W3R_EVIDENCE = {
    "report_digest": "",
    "paired_case_count": 0,
    "teacher_reviewed_case_count": 0,
    "fresh_holdout_case_count": 0,
    "fresh_holdout_target_count": 0,
    "final_answer_fidelity": 0.0,
    "claim_support_coverage": 0.0,
    "condition_retention": 0.0,
    "target_coverage": 0.0,
    "latex_validity": 0.0,
    "unsupported_claim_rate": 1.0,
    "teacher_readability_preference": 0.0,
    "teacher_edit_rate_non_regression": False,
}
DEFAULT_W3R_CONFIG = {
    "schema_version": W3R_CONFIG_SCHEMA_VERSION,
    "policy_version": W3R_POLICY_VERSION,
    "mode": "off",
    "gray_entry_ids": [],
    "evidence": DEFAULT_W3R_EVIDENCE,
}


def normalize_w3r_config(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Normalize the independent renderer rollout policy; invalid means off."""
    if raw in (None, {}):
        return {
            **DEFAULT_W3R_CONFIG,
            "evidence": dict(DEFAULT_W3R_EVIDENCE),
        }, []
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {
            **DEFAULT_W3R_CONFIG,
            "evidence": dict(DEFAULT_W3R_EVIDENCE),
        }, ["w3r routing config must be an object"]
    required = {
        "schema_version",
        "policy_version",
        "mode",
        "gray_entry_ids",
        "evidence",
    }
    if set(raw) != required:
        errors.append("w3r routing config fields do not match schema v1")
    mode = str(raw.get("mode", "")).strip().lower()
    if raw.get("schema_version") != W3R_CONFIG_SCHEMA_VERSION:
        errors.append("w3r routing config schema version mismatch")
    if str(raw.get("policy_version", "")).strip() != W3R_POLICY_VERSION:
        errors.append("w3r routing policy version mismatch")
    if mode not in W3R_MODES:
        errors.append("w3r routing mode must be off, shadow, gray, or default")
    gray_entry_ids = raw.get("gray_entry_ids", [])
    if not isinstance(gray_entry_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in gray_entry_ids
    ):
        errors.append("w3r gray_entry_ids must contain non-empty strings")
        gray_entry_ids = []
    elif len(gray_entry_ids) != len(set(gray_entry_ids)):
        errors.append("w3r gray_entry_ids must not contain duplicates")

    evidence = raw.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != W3R_EVIDENCE_FIELDS:
        errors.append("w3r evidence fields do not match schema v1")
        evidence = dict(DEFAULT_W3R_EVIDENCE)
    normalized_evidence = dict(DEFAULT_W3R_EVIDENCE)
    for field in (
        "paired_case_count",
        "teacher_reviewed_case_count",
        "fresh_holdout_case_count",
        "fresh_holdout_target_count",
    ):
        value = evidence.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append(f"w3r evidence {field} must be a non-negative integer")
        else:
            normalized_evidence[field] = value
    for field in (
        "final_answer_fidelity",
        "claim_support_coverage",
        "condition_retention",
        "target_coverage",
        "latex_validity",
        "unsupported_claim_rate",
        "teacher_readability_preference",
    ):
        value = evidence.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
            errors.append(f"w3r evidence {field} must be between 0 and 1")
        else:
            normalized_evidence[field] = float(value)
    digest = str(evidence.get("report_digest", "")).strip().lower()
    if digest and (
        len(digest) != 71
        or not digest.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in digest[7:])
    ):
        errors.append("w3r evidence report_digest must be a sha256 fingerprint")
    normalized_evidence["report_digest"] = digest
    edit_rate = evidence.get("teacher_edit_rate_non_regression")
    if not isinstance(edit_rate, bool):
        errors.append("w3r evidence teacher_edit_rate_non_regression must be boolean")
    else:
        normalized_evidence["teacher_edit_rate_non_regression"] = edit_rate
    if errors:
        return {
            **DEFAULT_W3R_CONFIG,
            "evidence": dict(DEFAULT_W3R_EVIDENCE),
        }, errors
    return {
        "schema_version": W3R_CONFIG_SCHEMA_VERSION,
        "policy_version": W3R_POLICY_VERSION,
        "mode": mode,
        "gray_entry_ids": list(gray_entry_ids),
        "evidence": normalized_evidence,
    }, []


def w3r_evidence_errors(config: dict[str, Any], *, default: bool) -> list[str]:
    """Require hard fidelity evidence before gray/default renderer selection."""
    evidence = config["evidence"]
    errors = []
    if not evidence["report_digest"]:
        errors.append("w3r-evidence-report-unbound")
    if evidence["paired_case_count"] < 2:
        errors.append("w3r-paired-cases-insufficient")
    if evidence["teacher_reviewed_case_count"] < 2:
        errors.append("w3r-teacher-review-insufficient")
    for field in (
        "final_answer_fidelity",
        "claim_support_coverage",
        "condition_retention",
        "target_coverage",
        "latex_validity",
    ):
        if evidence[field] != 1.0:
            errors.append(f"w3r-{field.replace('_', '-')}-below-hard-gate")
    if evidence["unsupported_claim_rate"] != 0.0:
        errors.append("w3r-unsupported-claim-rate-nonzero")
    if evidence["teacher_readability_preference"] <= 0.5:
        errors.append("w3r-readability-preference-not-positive")
    if not evidence["teacher_edit_rate_non_regression"]:
        errors.append("w3r-teacher-edit-rate-regressed")
    if default:
        if evidence["fresh_holdout_case_count"] < 5:
            errors.append("w3r-fresh-holdout-cases-insufficient")
        if evidence["fresh_holdout_target_count"] < 12:
            errors.append("w3r-fresh-holdout-targets-insufficient")
    return errors


def w3r_render_readiness(report: Any) -> tuple[bool, list[str]]:
    """Accept only a Gate-passed result bound to a VERIFIED W3 proof."""
    errors: list[str] = []
    if not isinstance(report, dict):
        return False, ["w3r-report-missing"]
    evidence = report.get("claim_evidence_shadow")
    aggregation = (
        evidence.get("aggregation", {})
        if isinstance(evidence, dict) and isinstance(evidence.get("aggregation"), dict)
        else {}
    )
    if aggregation.get("status") != "VERIFIED":
        errors.append("w3r-proof-package-not-verified")
    shadow = report.get("w3r_shadow")
    if not isinstance(shadow, dict):
        return False, [*errors, "w3r-shadow-missing"]
    if shadow.get("status") != "completed":
        errors.append("w3r-render-not-completed")
    brief = shadow.get("brief")
    render = shadow.get("render_result")
    try:
        normalized = w3r_contract.normalize_render_result(render)
        normalized_brief = w3r_contract.normalize_w3r_brief(brief)
    except ValueError:
        return False, [*errors, "w3r-render-contract-invalid"]
    if w3r_contract.brief_fingerprint(normalized_brief) != normalized["brief_fingerprint"]:
        errors.append("w3r-render-brief-binding-invalid")
    import w3_rendering

    recomputed_gate = w3_rendering.render_gate(normalized_brief, normalized)
    if recomputed_gate != normalized["render_gate_report"]:
        errors.append("w3r-render-gate-report-stale")
    if shadow.get("brief_fingerprint") != normalized["brief_fingerprint"]:
        errors.append("w3r-brief-fingerprint-mismatch")
    gate = normalized["render_gate_report"]
    if gate["status"] != "pass" or gate["violations"]:
        errors.append("w3r-render-gate-not-passed")
    metrics = gate["metrics"]
    for field in (
        "final_answer_fidelity",
        "claim_support_coverage",
        "condition_retention",
        "target_coverage",
        "latex_validity",
    ):
        if metrics.get(field) != 1.0:
            errors.append(f"w3r-{field.replace('_', '-')}-below-hard-gate")
    if metrics.get("unsupported_claim_rate") != 0.0:
        errors.append("w3r-unsupported-claim-rate-nonzero")
    if analysis_artifacts.student_method_errors(
        normalized["student_solution_md"],
        str(report.get("method_profile", "high_school_standard")),
    ):
        errors.append("w3r-student-method-gate-failed")
    return not errors, sorted(set(errors))


def select_renderer(
    report: dict[str, Any],
    *,
    entry_id: str,
    config: Any,
) -> dict[str, Any]:
    """Select W3R only after rollout evidence and per-candidate hard gates."""
    normalized, config_errors = normalize_w3r_config(config)
    ready, readiness_errors = w3r_render_readiness(report)
    mode = normalized["mode"]
    evidence_errors: list[str] = []
    reason = "w3r-disabled"
    selected = "legacy"
    if config_errors:
        reason = "w3r-invalid-config"
    elif mode == "shadow":
        reason = "w3r-shadow-only"
    elif mode == "gray" and entry_id not in normalized["gray_entry_ids"]:
        reason = "w3r-outside-gray-cohort"
    elif mode in {"gray", "default"}:
        evidence_errors = w3r_evidence_errors(normalized, default=mode == "default")
        if evidence_errors:
            reason = "w3r-rollout-evidence-insufficient"
        elif not ready:
            reason = "w3r-candidate-gate-failed"
        else:
            selected = "w3r"
            reason = "w3r-gate-passed"
    return {
        "schema_version": 1,
        "policy_version": W3R_POLICY_VERSION,
        "mode": mode,
        "selected_renderer": selected,
        "reason": reason,
        "config_errors": config_errors,
        "evidence_errors": evidence_errors,
        "readiness_errors": readiness_errors,
        "legacy_fallback_used": selected != "w3r",
    }


def w3r_teacher_review_summary(
    report: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    """Expose a compact teacher-only summary without Claim/span payloads."""
    shadow = report.get("w3r_shadow")
    render = (
        shadow.get("render_result", {})
        if isinstance(shadow, dict) and isinstance(shadow.get("render_result"), dict)
        else {}
    )
    gate = render.get("render_gate_report", {}) if isinstance(render.get("render_gate_report"), dict) else {}
    violations = gate.get("violations", [])
    return {
        "schema_version": 1,
        "selected_renderer": selection.get("selected_renderer", "legacy"),
        "reason": selection.get("reason", "w3r-disabled"),
        "render_status": render.get("status", "unavailable"),
        "gate_status": gate.get("status", "unavailable"),
        "metrics": gate.get("metrics", {}),
        "attempt": render.get("attempt", 0),
        "violation_codes": [
            str(item.get("code", "")) for item in violations[:8] if isinstance(item, dict) and item.get("code")
        ],
        "teacher_confirmation_required": True,
    }


def normalize_config(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Normalize local policy config; invalid values fail closed to W2."""
    if raw in (None, {}):
        return dict(DEFAULT_CONFIG), []
    errors: list[str] = []
    if not isinstance(raw, dict):
        return dict(DEFAULT_CONFIG), ["routing config must be an object"]
    schema_version = raw.get("schema_version")
    policy_version = str(raw.get("policy_version", "")).strip()
    mode = str(raw.get("mode", "")).strip().lower()
    gray_entry_ids = raw.get("gray_entry_ids", [])
    w3_failure_policy = str(raw.get("w3_failure_policy", DEFAULT_CONFIG["w3_failure_policy"])).strip().lower()
    if schema_version != CONFIG_SCHEMA_VERSION:
        errors.append("routing config schema version mismatch")
    if policy_version != POLICY_VERSION:
        errors.append("routing policy version mismatch")
    if mode not in MODES:
        errors.append("routing mode must be off, gray, or default")
    if not isinstance(gray_entry_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in gray_entry_ids
    ):
        errors.append("gray_entry_ids must contain non-empty strings")
        gray_entry_ids = []
    if len(set(gray_entry_ids)) != len(gray_entry_ids):
        errors.append("gray_entry_ids must not contain duplicates")
    if w3_failure_policy not in W3_FAILURE_POLICIES:
        errors.append("w3_failure_policy must be fallback-w2 or stop")

    def bounded_int(field: str, default: int, minimum: int, maximum: int) -> int:
        value = raw.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            errors.append(f"{field} must be between {minimum} and {maximum}")
            return default
        return cast(int, value)

    if errors:
        return dict(DEFAULT_CONFIG), errors
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "mode": mode,
        "gray_entry_ids": list(gray_entry_ids),
        "max_agent_calls": bounded_int("max_agent_calls", 6, 1, 12),
        "max_teacher_focus": bounded_int("max_teacher_focus", 2, 0, 2),
        "max_latency_seconds": bounded_int("max_latency_seconds", 900, 1, 3_600),
        "w3_failure_policy": w3_failure_policy,
    }, errors


def decide(
    problem: str,
    *,
    entry_id: str,
    config: Any,
    has_physics_model: bool = False,
) -> dict[str, Any]:
    normalized, errors = normalize_config(config)
    screen = problem_decomposition.complexity_screen(problem, has_physics_model=has_physics_model)
    route = "w2"
    reason = "w3-production-disabled"
    if errors:
        reason = "invalid-policy-config"
    elif normalized["mode"] == "gray" and entry_id not in normalized["gray_entry_ids"]:
        reason = "outside-gray-cohort"
    elif normalized["mode"] in {"gray", "default"}:
        if screen["decision"] == "decompose":
            route = "w3"
            reason = "deterministic-complexity-screen"
        else:
            reason = "deterministic-low-risk-screen"
    return {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "mode": normalized["mode"],
        "route": route,
        "reason": reason,
        "screen": screen,
        "config_errors": errors,
        "limits": {
            "max_agent_calls": normalized["max_agent_calls"],
            "max_teacher_focus": normalized["max_teacher_focus"],
            "max_latency_seconds": normalized["max_latency_seconds"],
        },
        "w3_failure_policy": normalized["w3_failure_policy"],
    }


def production_readiness(request: Any, decision: dict[str, Any]) -> tuple[bool, list[str]]:
    """Accept only complete, bounded, teacher-reviewable W3 output."""
    errors: list[str] = []
    if not isinstance(request, dict) or request.get("status") != "completed":
        return False, ["w3-request-not-completed"]
    report = request.get("report")
    if not isinstance(report, dict):
        return False, ["w3-report-missing"]
    if report.get("policy") != "wuli-w3-shadow-v1":
        errors.append("w3-policy-mismatch")
    if report.get("screen", {}).get("decision") != "decompose":
        errors.append("w3-screen-not-complex")
    if report.get("solver_a", {}).get("status") != "completed":
        errors.append("w3-solver-incomplete")
    if not str(report.get("recommended_student_solution", "")).strip():
        errors.append("w3-candidate-missing")
    warnings = report.get("stage_warnings", [])
    if warnings:
        errors.append("w3-stage-warning")
    adjudication = report.get("adjudication")
    if isinstance(adjudication, dict) and adjudication.get("status") != "completed":
        errors.append("w3-adjudication-incomplete")
    verifier = report.get("verifier")
    if isinstance(verifier, dict) and verifier.get("status") != "completed":
        errors.append("w3-verifier-incomplete")
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    calls = len(request.get("stages", [])) if isinstance(request.get("stages"), list) else 0
    focus = int(metrics.get("teacher_focus_count", 0))
    limits = decision.get("limits", {})
    if calls > int(limits.get("max_agent_calls", 6)):
        errors.append("w3-agent-call-limit-exceeded")
    if focus > int(limits.get("max_teacher_focus", 2)):
        errors.append("w3-teacher-focus-limit-exceeded")
    elapsed = request.get("production_elapsed_seconds", 0)
    if (
        isinstance(elapsed, (int, float))
        and not isinstance(elapsed, bool)
        and elapsed > int(limits.get("max_latency_seconds", 900))
    ):
        errors.append("w3-latency-limit-exceeded")
    method_errors = analysis_artifacts.student_method_errors(
        str(report.get("recommended_student_solution", "")),
        str(report.get("method_profile", "high_school_standard")),
    )
    if method_errors:
        errors.append("w3-student-method-gate-failed")
    return not errors, errors


def candidate_files(
    report: dict[str, Any],
    *,
    entry_id: str = "",
    w3r_config: Any = None,
    selection: dict[str, Any] | None = None,
    diagram_plugin_id: str = "",
) -> dict[str, str]:
    """Build canonical answer layers, selecting W3R without rerunning W3."""
    blueprint = report.get("blueprint", {}) if isinstance(report.get("blueprint"), dict) else {}
    selection = selection or select_renderer(
        report,
        entry_id=entry_id,
        config=w3r_config,
    )
    render_result = (
        report.get("w3r_shadow", {}).get("render_result", {}) if isinstance(report.get("w3r_shadow"), dict) else {}
    )
    if selection.get("selected_renderer") == "w3r":
        normalized = w3r_contract.normalize_render_result(render_result)
        student_body = normalized["student_solution_md"].strip()
        teacher_body = normalized["teacher_solution_md"].strip()
    else:
        student_body = str(report.get("recommended_student_solution", "")).strip()
        teacher_body = ""
    image_block = (
        "![解题流程图（可选插件）](assets/explanatory.svg)\n\n"
        if diagram_plugin_id
        else "![物理过程示意图](assets/explanatory.svg)\n\n"
    )
    student = "# 解析（学生版）\n\n" + image_block + f"{student_body}\n"
    obligations = blueprint.get("verification_obligations", [])
    diagram_nodes = [
        str(item.get("operation", "")).strip()
        for item in blueprint.get("reasoning_steps", [])
        if isinstance(item, dict) and str(item.get("operation", "")).strip()
    ]
    if len(diagram_nodes) < 2:
        diagram_nodes.extend(
            str(item.get("label", "")).strip()
            for item in blueprint.get("physical_stages", [])
            if isinstance(item, dict) and str(item.get("label", "")).strip()
        )
    audit_lines = [
        "## 教师审计",
        "",
        f"- 路由策略：`{POLICY_VERSION}`",
        "- 候选来源：W3 结构化求解；仍须教师逐题复核后批准。",
    ]
    for item in obligations[:6] if isinstance(obligations, list) else []:
        check = str(item.get("check", "")).strip() if isinstance(item, dict) else ""
        if check:
            audit_lines.append(f"- 核对义务：{check}")
    if selection.get("selected_renderer") == "w3r":
        teacher = f"# 解析（教师版）\n\n{image_block}{teacher_body}\n"
    else:
        teacher = student.rstrip() + "\n\n" + "\n".join(audit_lines) + "\n"
    files = {
        "student-solution.md": student,
        "teacher-solution.md": teacher,
        "solution.md": teacher,
    }
    if diagram_plugin_id:
        files["assets/explanatory.svg"] = analysis_artifacts.render_explanation_diagram(
            "W3 关键关系", diagram_nodes, plugin_id=diagram_plugin_id
        )
    return files
