#!/usr/bin/env python3
"""Pure W3 adaptive reasoning orchestration and shadow acceptance helpers."""

from __future__ import annotations

import re
from typing import Any, Callable

from problem_decomposition import complexity_screen
from solution_verification import should_verify, target_risk, teacher_review_focus

W3_POLICY = "wuli-w3-shadow-v1"
DEFAULT_CALIBRATION = {
    "catch_probability": 0.62,
    "error_cost": 1.0,
    "verification_cost": 0.18,
    "false_conflict_probability": 0.08,
    "review_cost": 0.45,
    "challenge_risk_threshold": 0.9,
}


def solution_target_map(solution: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id", "")): item
        for item in solution.get("targets", [])
        if isinstance(item, dict) and item.get("id")
    }


def answer_signature(value: Any) -> str:
    text = str(value or "").lower()
    text = text.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉²³", "012345678923"))
    text = text.replace("\\pi", "π").replace("sqrt", "√")
    text = re.sub(r"\\(?:left|right|,|;|!|quad|qquad)", "", text)
    return re.sub(r"[\s，。；;：:、（）()]", "", text)


def answers_equivalent(left: Any, right: Any) -> bool:
    first, second = answer_signature(left), answer_signature(right)
    if not first or not second:
        return False
    final_first = first.rsplit("=", 1)[-1]
    final_second = second.rsplit("=", 1)[-1]
    return first == second or final_first == final_second or (
        min(len(first), len(second)) >= 8 and (first in second or second in first)
    )


def project_blueprint(
    blueprint: dict[str, Any], target_ids: set[str]
) -> dict[str, Any]:
    """Create a target-scoped blueprint for a blind second solver."""
    target_ids = {str(item) for item in target_ids if str(item)}
    obligation_ids = {
        str(item.get("id", ""))
        for item in blueprint.get("verification_obligations", [])
        if str(item.get("target_id", "")) in target_ids
    }
    return {
        **blueprint,
        "question_targets": [
            item
            for item in blueprint.get("question_targets", [])
            if str(item.get("id", "")) in target_ids
        ],
        "reasoning_steps": [
            {
                **item,
                "target_ids": [
                    target_id
                    for target_id in item.get("target_ids", [])
                    if str(target_id) in target_ids
                ],
            }
            for item in blueprint.get("reasoning_steps", [])
            if set(map(str, item.get("target_ids", []))) & target_ids
        ],
        "retrieval_needs": [
            {
                **item,
                "target_ids": [
                    target_id
                    for target_id in item.get("target_ids", [])
                    if str(target_id) in target_ids
                ],
            }
            for item in blueprint.get("retrieval_needs", [])
            if set(map(str, item.get("target_ids", []))) & target_ids
        ],
        "verification_obligations": [
            item
            for item in blueprint.get("verification_obligations", [])
            if str(item.get("id", "")) in obligation_ids
        ],
    }


def conflict_target_ids(
    solver_a: dict[str, Any],
    verifier: dict[str, Any] | None,
    solver_b: dict[str, Any] | None,
) -> set[str]:
    conflicts = {
        str(item.get("target_id", ""))
        for item in (verifier or {}).get("target_audits", [])
        if item.get("verdict") == "conflict"
    }
    if solver_b:
        first = solution_target_map(solver_a)
        second = solution_target_map(solver_b)
        for target_id in first.keys() & second.keys():
            if not answers_equivalent(
                first[target_id].get("final_answer", ""),
                second[target_id].get("final_answer", ""),
            ):
                conflicts.add(target_id)
    return {item for item in conflicts if item}


def selected_targets(
    solver_a: dict[str, Any], adjudication: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Apply target-level adjudication without exposing internal role details."""
    decisions = {
        str(item.get("target_id", "")): item
        for item in (adjudication or {}).get("target_decisions", [])
        if isinstance(item, dict)
    }
    result = []
    for target in solver_a.get("targets", []):
        target_id = str(target.get("id", ""))
        decision = decisions.get(target_id)
        result.append({
            **target,
            "final_answer": (
                str(decision.get("selected_result", "")).strip()
                if decision
                else str(target.get("final_answer", "")).strip()
            ),
        })
    return result


def teacher_review_snapshot(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a concise, role-neutral audit view for the private teacher console."""
    focus = report.get("teacher_review_focus", [])
    if not isinstance(focus, list):
        return []
    primary = solution_target_map(report.get("solver_a") or {})
    cross_check = solution_target_map(report.get("solver_b") or {})
    audits = {
        str(item.get("target_id", "")): item
        for item in (report.get("verifier") or {}).get("target_audits", [])
        if isinstance(item, dict)
    }
    decisions = {
        str(item.get("target_id", "")): item
        for item in (report.get("adjudication") or {}).get("target_decisions", [])
        if isinstance(item, dict)
    }

    def section(label: str, text: Any, *, actionable: bool = False) -> dict[str, Any] | None:
        value = str(text or "").strip()
        if not value:
            return None
        return {"label": label, "text": value[:1200], "actionable": actionable}

    result = []
    for raw in focus[:2]:
        if not isinstance(raw, dict):
            continue
        target_id = str(raw.get("target_id", ""))
        target = primary.get(target_id, {})
        audit = audits.get(target_id, {})
        alternative = cross_check.get(target_id, {})
        decision = decisions.get(target_id, {})
        sections = [
            section("主候选结论", target.get("final_answer"), actionable=True),
            section("独立复算结论", audit.get("recomputed_result"), actionable=True),
        ]
        alternative_result = str(alternative.get("final_answer", "")).strip()
        if alternative_result and not answers_equivalent(
            target.get("final_answer", ""), alternative_result
        ):
            sections.append(section("交叉候选结论", alternative_result, actionable=True))
        sections.extend([
            section("仲裁采用结论", decision.get("selected_result"), actionable=True),
            section(
                "决定性关系",
                "\n".join(
                    f"• {item}"
                    for item in audit.get("decisive_checks", [])[:4]
                    if str(item).strip()
                ),
            ),
            section(
                "仲裁说明",
                decision.get("decisive_relation") or decision.get("reason"),
            ),
        ])
        item = {
            "target_id": target_id,
            "priority": str(raw.get("priority", "medium")),
            "prompt": str(raw.get("prompt", "请核对关键结论"))[:160],
            "decisive_check": str(raw.get("decisive_check", ""))[:200],
            "audit": {
                "verdict": str(audit.get("verdict", "review")),
                "sections": [value for value in sections if value][:6],
            },
        }
        result.append(item)
    return result


def render_recommended_student_solution(
    blueprint: dict[str, Any],
    solver_a: dict[str, Any],
    adjudication: dict[str, Any] | None,
) -> str:
    """Deterministically render one concise teacher-reviewable answer candidate."""
    targets = selected_targets(solver_a, adjudication)
    lines = ["## 答案速览", ""]
    for index, target in enumerate(targets, 1):
        lines.append(f"- （{index}）{target.get('final_answer', '')}")
    operations = [
        str(item.get("operation", "")).strip()
        for item in blueprint.get("reasoning_steps", [])
        if str(item.get("operation", "")).strip()
    ]
    mainline = " → ".join(operations[:3])
    if len(operations) > 3:
        mainline += " → 完成各目标复算"
    lines.extend([
        "",
        "## 一眼识别",
        "",
        f"- **最短主线**：{mainline or '按物理阶段建立关系并逐目标复算。'}",
        "",
        "## 详细解答",
        "",
    ])
    stage_results = [
        item for item in solver_a.get("stage_results", []) if str(item.get("result", "")).strip()
    ]
    if len(stage_results) > 5:
        stage_results = [
            *stage_results[:4],
            {
                "stage_id": "merged",
                "result": "；".join(str(item.get("result", "")).strip() for item in stage_results[4:]),
            },
        ]
    for index, stage in enumerate(stage_results, 1):
        lines.extend([
            f"### 第 {index} 步",
            "",
            str(stage.get("result", "")).strip(),
            "",
        ])
    obligations = sorted(
        blueprint.get("verification_obligations", []),
        key=lambda item: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
                str(item.get("risk", "")), 4
            ),
            str(item.get("id", "")),
        ),
    )
    lines.extend(["## 易错点", ""])
    for item in obligations[:3]:
        lines.append(f"- {str(item.get('check', '')).strip()}")
    lines.extend([
        "",
        "## 30 秒自测",
        "",
        "遮住答案后，能否只用上面的决定性关系重新得到各小问结论？",
        "",
    ])
    return "\n".join(lines)


def risk_plan(
    blueprint: dict[str, Any],
    solution: dict[str, Any],
    evidence: dict[str, Any],
    *,
    calibration: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    calibration = {**DEFAULT_CALIBRATION, **(calibration or {})}
    obligations = blueprint.get("verification_obligations", [])
    by_target: dict[str, list[dict[str, Any]]] = {}
    for item in obligations:
        by_target.setdefault(str(item.get("target_id", "")), []).append(item)
    evidence_status = str(
        evidence.get("evidence_set", {}).get("status")
        or evidence.get("status")
        or "unavailable"
    )
    blueprint_status = str(solution.get("blueprint_audit", {}).get("status", "followed"))
    result = []
    for target in solution.get("targets", []):
        risk = target_risk(
            target,
            by_target.get(str(target.get("id", "")), []),
            evidence_status=evidence_status,
            blueprint_status=blueprint_status,
        )
        result.append(should_verify(risk, calibration))
    return result


def build_shadow_summary(
    *,
    screen: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    solver_a: dict[str, Any] | None = None,
    risk_decisions: list[dict[str, Any]] | None = None,
    verifier: dict[str, Any] | None = None,
    solver_b: dict[str, Any] | None = None,
    adjudication: dict[str, Any] | None = None,
    stage_warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    obligations = (blueprint or {}).get("verification_obligations", [])
    audits = (verifier or {}).get("target_audits", [])
    focus = teacher_review_focus(audits, obligations)
    recommended = (
        render_recommended_student_solution(blueprint or {}, solver_a, adjudication)
        if solver_a and solver_a.get("status") == "completed"
        else ""
    )
    try:
        from analysis_artifacts import student_method_errors

        method_errors = student_method_errors(recommended) if recommended else []
    except ImportError:
        method_errors = []
    if method_errors and len(focus) < 2:
        focus.append({
            "target_id": "answer-method",
            "priority": "high",
            "prompt": "建议核对学生版是否保持高中范围内的最短推导",
            "decisive_check": method_errors[0][:200],
        })
    for warning in stage_warnings or []:
        if len(focus) >= 2:
            break
        focus.append({
            "target_id": str(warning.get("target_id", "cross-check")),
            "priority": "high",
            "prompt": str(warning.get("prompt", "建议独立核对高风险结论"))[:160],
            "decisive_check": str(warning.get("decisive_check", ""))[:200],
        })
    return {
        "schema_version": 1,
        "policy": W3_POLICY,
        "mode": "shadow",
        "screen": screen,
        "blueprint": blueprint,
        "evidence": evidence,
        "solver_a": solver_a,
        "risk_decisions": risk_decisions or [],
        "verifier": verifier,
        "solver_b": solver_b,
        "adjudication": adjudication,
        "stage_warnings": stage_warnings or [],
        "recommended_student_solution": recommended,
        "teacher_review_focus": focus[:2],
        "metrics": {
            "target_count": len((solver_a or {}).get("targets", [])),
            "verified_target_count": sum(
                1 for item in (risk_decisions or []) if item.get("decision") == "verify"
            ),
            "solver_b_used": solver_b is not None,
            "conflict_target_count": len(conflict_target_ids(solver_a or {}, verifier, solver_b)),
            "teacher_focus_count": len(focus[:2]),
        },
    }


def run_shadow(
    problem: str,
    *,
    stage_runner: Callable[[str, dict[str, Any]], dict[str, Any]],
    evidence_builder: Callable[[dict[str, Any]], dict[str, Any]],
    has_physics_model: bool = False,
    calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run W3 internal stages without writing canonical answer files.

    ``stage_runner`` is deliberately provider-neutral. It receives a stage name
    and a minimal context object, allowing the teacher Gateway to keep one
    external ``analysis.generate`` job while tests use deterministic fixtures.
    """
    calibration = {**DEFAULT_CALIBRATION, **(calibration or {})}
    screen = complexity_screen(problem, has_physics_model=has_physics_model)
    if screen["decision"] == "w2":
        return build_shadow_summary(screen=screen)

    blueprint = stage_runner("decompose", {"problem": problem, "screen": screen})
    if blueprint.get("status") != "completed":
        return build_shadow_summary(screen=screen, blueprint=blueprint)
    evidence = evidence_builder(blueprint)
    solver_a = stage_runner(
        "solver-a",
        {"problem": problem, "blueprint": blueprint, "evidence": evidence},
    )
    if solver_a.get("status") != "completed":
        return build_shadow_summary(
            screen=screen, blueprint=blueprint, evidence=evidence, solver_a=solver_a
        )

    decisions = risk_plan(
        blueprint, solver_a, evidence, calibration=calibration
    )
    verify_ids = {
        str(item.get("target_id", ""))
        for item in decisions
        if item.get("decision") == "verify"
    }
    verifier = None
    stage_warnings: list[dict[str, Any]] = []
    if verify_ids:
        try:
            verifier = stage_runner(
                "verifier",
                {
                    "problem": problem,
                    "blueprint": blueprint,
                    "evidence": evidence,
                    "candidate_targets": [
                        item
                        for item in solver_a.get("targets", [])
                        if item.get("id") in verify_ids
                    ],
                    "target_ids": sorted(verify_ids),
                },
            )
        except Exception as exc:
            verifier = {"status": "failed", "message": str(exc)[:500], "target_audits": []}
            stage_warnings.append({
                "target_id": "verification",
                "prompt": "独立验证器未形成完整复算，请教师重点核对高风险目标",
                "decisive_check": "优先核对蓝图中的 critical/high 校验义务。",
            })

    challenge = {
        str(item.get("target_id", ""))
        for item in decisions
        if float(item.get("risk", 0)) >= float(calibration["challenge_risk_threshold"])
    }
    challenge.update(conflict_target_ids(solver_a, verifier, None))
    solver_b = None
    if challenge:
        challenge_blueprint = project_blueprint(blueprint, challenge)
        try:
            solver_b = stage_runner(
                "solver-b",
                {
                    "problem": problem,
                    "blueprint": challenge_blueprint,
                    "evidence": evidence,
                    "target_ids": sorted(challenge),
                },
            )
        except Exception as exc:
            solver_b = {"status": "failed", "message": str(exc)[:500], "targets": []}
            stage_warnings.append({
                "target_id": "blind-solver",
                "prompt": "盲解交叉验证未覆盖全部挑战目标",
                "decisive_check": "请优先复算挑战目标的最终关系与首次/唯一/临界条件。",
            })

    conflicts = conflict_target_ids(
        solver_a,
        verifier if verifier and verifier.get("status") == "completed" else None,
        solver_b if solver_b and solver_b.get("status") == "completed" else None,
    )
    adjudication = None
    if conflicts:
        try:
            adjudication = stage_runner(
                "adjudicator",
                {
                    "problem": problem,
                    "blueprint": blueprint,
                    "evidence": evidence,
                    "solver_a": solver_a,
                    "verifier": verifier,
                    "solver_b": solver_b,
                    "target_ids": sorted(conflicts),
                },
            )
        except Exception as exc:
            adjudication = {"status": "failed", "message": str(exc)[:500]}
            stage_warnings.append({
                "target_id": "adjudication",
                "prompt": "交叉结果存在差异且未完成仲裁",
                "decisive_check": "请比较两个结论的适用条件并独立复算决定性关系。",
            })
    return build_shadow_summary(
        screen=screen,
        blueprint=blueprint,
        evidence=evidence,
        solver_a=solver_a,
        risk_decisions=decisions,
        verifier=verifier,
        solver_b=solver_b,
        adjudication=adjudication,
        stage_warnings=stage_warnings,
    )


def acceptance_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate frozen teacher-truth replay records without leaking answer text."""
    approved = [item for item in cases if item.get("review_status") == "approved"]
    holdout = [item for item in approved if item.get("evaluation_split") == "holdout"]
    calibration = [item for item in approved if item.get("evaluation_split") == "calibration"]
    replay = [item for item in approved if item.get("evaluation_split") == "replay"]

    def cohort_metrics(cohort: list[dict[str, Any]]) -> dict[str, Any]:
        targets = sum(int(item.get("target_count", 0)) for item in cohort)
        correct = sum(int(item.get("correct_target_count", 0)) for item in cohort)
        w2_values = [item.get("w2_correct_target_count") for item in cohort]
        w2_complete = bool(cohort) and all(value is not None for value in w2_values)
        w2_correct = (
            sum(int(value) for value in w2_values if value is not None)
            if w2_complete
            else None
        )
        supplements = sum(
            int(item.get("validated_supplement_target_count", 0)) for item in cohort
        )
        revised_references = sum(
            1 for item in cohort if item.get("reference_revised_after_shadow")
        )
        calls = sum(int(item.get("agent_call_count", 0)) for item in cohort)
        focus = sum(int(item.get("teacher_focus_count", 0)) for item in cohort)
        return {
            "case_count": len(cohort),
            "target_count": targets,
            "target_accuracy": round(correct / targets, 4) if targets else None,
            "w2_target_accuracy": (
                round(w2_correct / targets, 4)
                if targets and w2_correct is not None
                else None
            ),
            "accuracy_delta": (
                round((correct - w2_correct) / targets, 4)
                if targets and w2_correct is not None
                else None
            ),
            "validated_supplement_target_count": supplements,
            "reference_revision_case_count": revised_references,
            "average_agent_calls": round(calls / len(cohort), 3) if cohort else None,
            "average_teacher_focus": round(focus / len(cohort), 3) if cohort else None,
        }

    calibration_metrics = cohort_metrics(calibration)
    holdout_metrics = cohort_metrics(holdout)
    replay_metrics = cohort_metrics(replay)
    holdout_ready = len(holdout) >= 5 and int(holdout_metrics["target_count"] or 0) >= 12
    accuracy_safe = (
        holdout_ready
        and holdout_metrics["accuracy_delta"] is not None
        and holdout_metrics["accuracy_delta"] >= 0
    )
    workload_safe = (
        holdout_ready
        and holdout_metrics["average_teacher_focus"] is not None
        and holdout_metrics["average_teacher_focus"] <= 2
    )
    independent_holdout_intact = (
        holdout_ready and int(holdout_metrics["reference_revision_case_count"] or 0) == 0
    )
    return {
        "schema_version": 1,
        "policy": W3_POLICY,
        "calibration": calibration_metrics,
        "holdout": holdout_metrics,
        "replay": replay_metrics,
        "gates": {
            "holdout_ready": holdout_ready,
            "accuracy_non_regression": accuracy_safe,
            "teacher_focus_bounded": workload_safe,
            "independent_holdout_intact": independent_holdout_intact,
            "fresh_holdout_required": not independent_holdout_intact,
            "production_eligible": bool(
                accuracy_safe and workload_safe and independent_holdout_intact
            ),
        },
    }
