#!/usr/bin/env python3
"""Pure W3 adaptive reasoning orchestration and shadow acceptance helpers."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import claim_ledger
import cognitive_loop
import correctness_policy
import proof_aggregation
import solution_reasoning
from log import TraceContext, get_logger

logger = get_logger("w3_pipeline")

from problem_decomposition import complexity_screen, infer_default_obligation_suggestions
from solution_verification import (
    claim_verification_view,
    materialize_claim_certificates,
    should_verify,
    target_risk,
    teacher_review_focus,
)

W3_POLICY = "wuli-w3-shadow-v1"
MULTIFLUID_COLUMN_OBLIGATION_PREFIX = "MFC_"
SOURCE_DOMAIN_OBLIGATION_PREFIX = "SRC_"
DEFAULT_CALIBRATION = {
    "catch_probability": 0.62,
    "error_cost": 1.0,
    "verification_cost": 0.18,
    "false_conflict_probability": 0.08,
    "review_cost": 0.45,
    "challenge_risk_threshold": 0.9,
}


def semantic_audit_plan(
    snapshot: dict[str, Any],
    *,
    batch_size: int = 8,
) -> dict[str, Any]:
    """Explain the minimum safe semantic audit under the current certificate policy."""
    if batch_size < 1:
        raise ValueError("semantic audit batch_size must be positive")
    active = claim_ledger.active_claims(snapshot["claims"])
    semantic_ids = []
    structural_ids = []
    blocker_counts: dict[str, int] = {}
    for claim_id in claim_ledger.topological_claim_ids(snapshot["claims"]):
        item = active[claim_id]
        if item["kind"] == "premise":
            continue
        check_spec = item.get("check_spec")
        check_type = (
            str(check_spec.get("type", "")).strip() if isinstance(check_spec, dict) else "missing"
        ) or "missing"
        if check_type == "aggregation":
            structural_ids.append(claim_id)
        semantic_ids.append(claim_id)
        blocker_counts[check_type] = blocker_counts.get(check_type, 0) + 1
    request_count = len(semantic_ids)
    return {
        "schema_version": 1,
        "status": "no-safe-reduction" if request_count else "empty",
        "policy": correctness_policy.CORRECTNESS_POLICY_VERSION,
        "batch_size": batch_size,
        "semantic_request_count": request_count,
        "minimum_safe_request_count": request_count,
        "safe_skippable_claim_count": 0,
        "projected_batch_count": ((request_count + batch_size - 1) // batch_size if request_count else 0),
        "semantic_claim_ids": semantic_ids,
        "local_structural_certificate_claim_ids": structural_ids,
        "reduction_blockers": [
            {
                "check_spec_type": check_type,
                "claim_count": count,
                "reason": (
                    "The current Solver projection does not provide an executable deterministic checker for this Claim."
                    if check_type != "aggregation"
                    else "The local aggregation certificate checks links only; "
                    "final-answer semantics still require an independent audit."
                ),
            }
            for check_type, count in sorted(blocker_counts.items())
        ],
        "recommended_action": ("extend-solver-check-spec-before-reducing-audits" if request_count else "none"),
    }


def _attach_approved_problem_root(
    snapshot: dict[str, Any],
    problem: str,
    *,
    target_ids: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    root_id = f"L-P-{claim_ledger.stable_fingerprint('approved-problem-v1', problem)[:12]}"
    input_fingerprint = snapshot["input_fingerprint"]
    premise = claim_ledger.normalize_claim({
        "id": root_id,
        "version": 1,
        "kind": "premise",
        "statement": (
            f"Approved problem snapshot sha256:{claim_ledger.stable_fingerprint('problem-text-v1', problem)}"
        ),
        "target_ids": sorted(target_ids),
        "stage_ids": [],
        "depends_on": [],
        "conditions": [],
        "obligation_ids": [],
        "check_spec": None,
        "status": "candidate",
        "source": {
            "task_id": f"L-TP-{root_id[-12:]}",
            "input_fingerprint": input_fingerprint,
            "policy_version": correctness_policy.CLAIM_LEDGER_POLICY_VERSION,
        },
    })
    claims = [premise]
    for raw in snapshot["claims"]:
        item = claim_ledger.normalize_claim(raw, agent_submission=False)
        if not item["depends_on"]:
            item = {**item, "depends_on": [root_id]}
        claims.append(item)
    rooted = claim_ledger.normalize_graph_snapshot({
        **snapshot,
        "claims": claims,
    })
    source_certificate = claim_ledger.normalize_certificate({
        "claim_id": root_id,
        "claim_version": 1,
        "verifier_kind": "source",
        "check_type": "source-match",
        "verdict": "pass",
        "normalized_result": premise["statement"],
        "decisive_checks": ["Claim binds the exact approved problem-text fingerprint."],
        "input_fingerprint": (claim_ledger.claim_verification_input_fingerprint(premise, [])),
        "verifier_identity": {
            "model_id": "approved-source-snapshot-v1",
            "provider": "local",
            "context_isolated": True,
        },
    })
    return rooted, source_certificate


def _challenge_tickets_from_evidence(
    aggregation: dict[str, Any],
    *,
    snapshot_version: int,
) -> list[dict[str, Any]]:
    """Turn failed certificate obligations into specific, bounded challenges."""
    graph_evidence = aggregation.get("claim_evidence", {})
    active_ids = {
        str(item.get("id", ""))
        for item in graph_evidence.get("claims", [])
        if isinstance(item, dict) and item.get("id")
    }
    challenges = []
    for assessment in graph_evidence.get("assessments", []):
        if not isinstance(assessment, dict):
            continue
        decision = str(assessment.get("decision", "provisional"))
        if decision == "verified":
            continue
        claim_id = str(assessment.get("claim_id", ""))
        if claim_id not in active_ids:
            continue
        dependency_ids = [
            str(item) for item in assessment.get("unverified_dependency_ids", []) if str(item) in active_ids
        ]
        bound_ids = list(dict.fromkeys([*dependency_ids, claim_id]))
        issues = [str(item) for item in assessment.get("issues", []) if str(item).strip()]
        trigger = (
            "verification-conflict"
            if decision == "disputed" or int(assessment.get("conflict_count", 0)) > 0
            else "missing-coverage"
        )
        ticket = cognitive_loop.normalize_challenge_ticket(
            {
                "id": f"CH-{claim_id}-v{int(assessment.get('claim_version', 1))}",
                "snapshot_version": snapshot_version,
                "trigger": trigger,
                "claim_ids": bound_ids,
                "specific_doubt": (
                    f"Claim {claim_id} cannot be promoted because "
                    f"{'; '.join(issues) or 'its required certificate route is incomplete'}."
                ),
                "falsification_test": (
                    f"Recompute Claim {claim_id} from its approved dependencies with "
                    "a distinct permitted strategy and compare the decisive relation."
                ),
                "suggested_backjump": dependency_ids[0] if dependency_ids else claim_id,
                "status": "open",
            },
            available_claim_ids=active_ids,
            agent_submission=False,
        )
        challenges.append(ticket)
    return challenges


def _loop_snapshot(
    aggregation: dict[str, Any],
    certificates: list[dict[str, Any]],
    challenges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record one bounded control transition; never fabricate repair progress."""
    graph_evidence = aggregation["claim_evidence"]
    verified_ids = set(graph_evidence.get("verified_claim_ids", []))
    critical_ids = set(graph_evidence.get("critical_claim_ids", []))
    verified_claims = [
        item for item in graph_evidence.get("claims", []) if isinstance(item, dict) and item.get("id") in verified_ids
    ]
    current = {
        "verified_claim_count": len(verified_ids),
        "verified_critical_claim_count": len(verified_ids & critical_ids),
        "accepted_certificate_count": sum(1 for item in certificates if item.get("verdict") == "pass"),
        "closed_obligation_count": len({
            obligation_id for claim in verified_claims for obligation_id in claim.get("obligation_ids", [])
        }),
        "localized_conflict_count": len(challenges),
        "open_conflict_scope_size": len({
            claim_id for challenge in challenges for claim_id in challenge.get("claim_ids", [])
        }),
        "novel_hypothesis_count": 0,
    }
    previous = {
        **{key: 0 for key in current},
        "open_conflict_scope_size": current["open_conflict_scope_size"],
    }
    progress = cognitive_loop.evaluate_progress(previous, current)
    # This integration slice has exactly one executable Claim strategy:
    # isolated direct recomputation.  If that strategy leaves an obligation
    # open, record a deterministic fuse instead of pretending a later Agent
    # retry will happen.
    integration_policy = {
        "stagnation_before_strategy_change": 1,
        "strategy_count": 1,
        "max_transitions": 3,
    }
    transition = cognitive_loop.advance_loop_control(
        cognitive_loop.initial_loop_control(),
        progress,
        evidence_status=str(graph_evidence.get("result_status", "PROVISIONAL")),
        has_open_conflict=bool(challenges),
        policy=integration_policy,
    )
    transitions = [transition]
    if transition["control"]["terminal_status"] == "ACTIVE":
        no_progress = cognitive_loop.evaluate_progress(current, current)
        transition = cognitive_loop.advance_loop_control(
            transition["control"],
            no_progress,
            evidence_status=str(graph_evidence.get("result_status", "PROVISIONAL")),
            has_open_conflict=bool(challenges),
            policy=integration_policy,
        )
        transitions.append(transition)
    return {
        "policy": integration_policy,
        "progress": progress,
        "transition": transition,
        "transitions": transitions,
        "provider_calls_reused": 0,
        "repeated_task_count": 0,
        "fuse_triggered": transition["action"] in {"hard-fuse", "strategy-fuse"},
    }


def run_claim_verification_batches(
    semantic_requests: list[dict[str, Any]],
    source_facts: list[dict[str, Any]],
    stage_runner: Callable[[str, dict[str, Any]], dict[str, Any]],
    *,
    stage_interface_view: dict[str, Any] | None = None,
    claim_verifier_concurrency: int | None = None,
    log_prefix: str = "w3_shadow",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, float]:
    """Run the isolated claim-verifier batches shared by W3 and core-first (D3).

    Returns ``(audit_batches, semantic_certificates, concurrency, duration)``.
    The stage runner must accept ``("claim-verifier", {verification_view,
    expected_claim_versions, batch_index})`` and return the normalized audit
    payload with a trusted ``_runtime_identity`` attachment.
    """
    batch_inputs = []
    for batch_index, start in enumerate(range(0, len(semantic_requests), 8)):
        view = claim_verification_view(semantic_requests[start : start + 8], source_facts)
        if batch_index == 0 and stage_interface_view is not None:
            view["stage_interface_view"] = stage_interface_view
        expected_versions = {item["claim"]["id"]: item["claim"]["version"] for item in view["requests"]}
        batch_inputs.append((batch_index, view, expected_versions))

    def run_audit_batch(batch_input):
        batch_index, view, expected_versions = batch_input
        batch_audit = stage_runner(
            "claim-verifier",
            {
                "verification_view": view,
                "expected_claim_versions": expected_versions,
                "batch_index": batch_index,
            },
        )
        runtime_identity = batch_audit.pop("_runtime_identity", {})
        input_fingerprints = {
            (
                item["claim"]["id"],
                item["claim"]["version"],
            ): item["input_fingerprint"]
            for item in view["requests"]
        }
        certificates = materialize_claim_certificates(
            batch_audit,
            input_fingerprints,
            model_id=str(runtime_identity.get("model_id", "")),
            provider=str(runtime_identity.get("provider", "")),
            context_isolated=bool(runtime_identity.get("context_isolated")),
        )
        return batch_audit, certificates

    concurrency = (
        correctness_policy.claim_verify_concurrency()
        if claim_verifier_concurrency is None
        else int(claim_verifier_concurrency)
    )
    if not 1 <= concurrency <= correctness_policy.MAX_CLAIM_VERIFY_CONCURRENCY:
        raise ValueError(
            f"claim_verifier_concurrency must be between 1 and {correctness_policy.MAX_CLAIM_VERIFY_CONCURRENCY}"
        )
    audit_started = time.monotonic()
    if concurrency > 1 and len(batch_inputs) > 1:
        with ThreadPoolExecutor(
            max_workers=min(concurrency, len(batch_inputs)),
            thread_name_prefix="claim-verify",
        ) as executor:
            batch_results = list(executor.map(run_audit_batch, batch_inputs))
    else:
        batch_results = [run_audit_batch(item) for item in batch_inputs]
    audit_duration_seconds = round(time.monotonic() - audit_started, 4)
    logger.info(
        "stage=%s audit=completed batch_count=%d concurrency=%d duration_seconds=%.4f certificate_count=%d",
        log_prefix,
        len(batch_inputs),
        concurrency,
        audit_duration_seconds,
        sum(len(item[1]) for item in batch_results),
    )
    audit_batches = [item[0] for item in batch_results]
    semantic_certificates = [
        certificate for _, batch_certificates in batch_results for certificate in batch_certificates
    ]
    return audit_batches, semantic_certificates, concurrency, audit_duration_seconds


def run_claim_evidence_shadow(
    problem: str,
    blueprint: dict[str, Any],
    solver_a: dict[str, Any],
    *,
    stage_runner: Callable[[str, dict[str, Any]], dict[str, Any]],
    risk_decisions: list[dict[str, Any]],
    cognitive_loop_enabled: bool = True,
    claim_verifier_concurrency: int | None = None,
) -> dict[str, Any]:
    """Run the claim verifier as a private extension of an existing W3 solve."""
    with TraceContext("w3-claim-evidence-shadow", log=logger) as ctx:
        ctx.info("stage=w3_shadow status=started cognitive_loop_enabled=%s", cognitive_loop_enabled)
    input_fingerprint = claim_ledger.stable_fingerprint(
        "w3-claim-evidence-input-v1",
        {
            "problem": problem,
            "blueprint": blueprint,
            "solver_a": {
                key: solver_a.get(key)
                for key in (
                    "status",
                    "targets",
                    "stage_results",
                    "stage_interfaces",
                    "stage_transitions",
                    "option_verdicts",
                    "blueprint_audit",
                )
            },
            "policy": correctness_policy.policy_snapshot(),
        },
    )
    snapshot = solution_reasoning.project_claim_ledger(
        solver_a,
        blueprint,
        input_fingerprint=input_fingerprint,
    )
    target_ids = {
        str(item.get("id", "")).strip()
        for item in blueprint.get("question_targets", [])
        if str(item.get("id", "")).strip()
    }
    obligation_ids = {
        str(item.get("id", "")).strip()
        for item in blueprint.get("verification_obligations", [])
        if str(item.get("id", "")).strip()
    }
    snapshot, source_certificate = _attach_approved_problem_root(snapshot, problem, target_ids=target_ids)
    active = claim_ledger.active_claims(snapshot["claims"])
    audit_plan = semantic_audit_plan(snapshot)
    semantic_claim_ids = set(audit_plan["semantic_claim_ids"])
    semantic_requests = []
    aggregation_certificates = []
    for claim_id in claim_ledger.topological_claim_ids(snapshot["claims"]):
        item = active[claim_id]
        dependencies = [active[parent] for parent in item["depends_on"]]
        check_type = str(item["check_spec"].get("type", "")).strip() if isinstance(item["check_spec"], dict) else ""
        if check_type == "aggregation":
            aggregation_certificates.append(
                claim_ledger.normalize_certificate({
                    "claim_id": item["id"],
                    "claim_version": item["version"],
                    "verifier_kind": "deterministic",
                    "check_type": "aggregation",
                    "verdict": "pass",
                    "normalized_result": "dependency and obligation links are structurally complete",
                    "decisive_checks": ["All declared dependencies exist in the current acyclic graph."],
                    "input_fingerprint": (claim_ledger.claim_verification_input_fingerprint(item, dependencies)),
                    "verifier_identity": {
                        "model_id": "proof-link-check-v1",
                        "provider": "local",
                        "context_isolated": True,
                    },
                })
            )
            # A deterministic dependency-link certificate closes the base
            # aggregation route. High/critical final Claims still need an
            # independent semantic confirmation, so they must also be present
            # in the isolated verifier view.
        if claim_id in semantic_claim_ids:
            semantic_requests.append({
                "claim": item,
                "dependencies": dependencies,
            })

    deterministic_interface_report = cognitive_loop.check_stage_interfaces(
        solver_a.get("stage_interfaces", []),
        solver_a.get("stage_transitions", []),
    )
    source_facts = [
        {
            "id": "approved-problem",
            "statement": problem,
            "conditions": [],
        }
    ]
    audit_batches, semantic_certificates, concurrency, audit_duration_seconds = run_claim_verification_batches(
        semantic_requests,
        source_facts,
        stage_runner,
        stage_interface_view={
            "interfaces": solver_a.get("stage_interfaces", []),
            "transitions": solver_a.get("stage_transitions", []),
            "deterministic_report": deterministic_interface_report,
        },
        claim_verifier_concurrency=claim_verifier_concurrency,
    )
    audit = {
        "status": (
            "completed"
            if audit_batches and all(item.get("status") == "completed" for item in audit_batches)
            else "unsupported"
        ),
        "message": "；".join(
            str(item.get("message", "")).strip() for item in audit_batches if str(item.get("message", "")).strip()
        )[:1000],
        "claim_audits": [claim_audit for item in audit_batches for claim_audit in item.get("claim_audits", [])],
        "interface_audit": next(
            (item.get("interface_audit") for item in audit_batches if isinstance(item.get("interface_audit"), dict)),
            None,
        ),
        "batch_count": len(audit_batches),
        "batch_concurrency": concurrency,
        "duration_seconds": audit_duration_seconds,
        "optimization_assessment": audit_plan,
    }
    certificates = [
        source_certificate,
        *aggregation_certificates,
        *semantic_certificates,
    ]
    risk_by_target = {
        str(item.get("target_id", "")): (
            "critical"
            if float(item.get("risk", 0.0)) >= 0.9
            else "high"
            if float(item.get("risk", 0.0)) >= 0.7
            else "medium"
        )
        for item in risk_decisions
    }
    risks = {
        claim_id: (
            max(
                (risk_by_target.get(target_id, "medium") for target_id in item["target_ids"]),
                key=lambda value: {
                    "medium": 0,
                    "high": 1,
                    "critical": 2,
                }[value],
            )
            if item["kind"] == "final"
            else "medium"
        )
        for claim_id, item in active.items()
        if item["kind"] != "premise"
    }
    generator_identity = solver_a.get("_runtime_identity")
    generator_identities = (
        {
            claim_id: {
                "model_id": str(generator_identity.get("model_id", "")),
                "provider": str(generator_identity.get("provider", "")),
            }
            for claim_id, item in active.items()
            if item["kind"] != "premise"
        }
        if isinstance(generator_identity, dict)
        else {}
    )
    interface_report = dict(deterministic_interface_report)
    interface_audit = audit.get("interface_audit")
    if interface_report.get("status") == "provisional" and isinstance(interface_audit, dict):
        if interface_audit.get("verdict") == "pass":
            interface_report["status"] = "pass"
            interface_report["semantic_verification"] = interface_audit
            logger.info("stage=w3_shadow interface_status=upgraded_to_pass")
        elif interface_audit.get("verdict") == "conflict":
            interface_report["status"] = "conflict"
            interface_report["semantic_verification"] = interface_audit
            logger.warning("stage=w3_shadow interface_status=downgraded_to_conflict")
        else:
            interface_report["semantic_verification"] = interface_audit
    aggregation = proof_aggregation.aggregate_proof(
        snapshot["claims"],
        certificates,
        expected_target_ids=target_ids,
        expected_obligation_ids=obligation_ids,
        interface_report=interface_report,
        risks=risks,
        generator_identities=generator_identities,
    )
    logger.info(
        "stage=w3_shadow aggregation_status=%s verified_count=%d",
        aggregation["status"],
        len(aggregation["claim_evidence"]["verified_claim_ids"]),
    )
    if cognitive_loop_enabled:
        challenges = _challenge_tickets_from_evidence(
            aggregation,
            snapshot_version=snapshot["snapshot_version"],
        )
        if challenges:
            logger.info(
                "stage=w3_shadow challenge_count=%d re-aggregating",
                len(challenges),
            )
            aggregation = proof_aggregation.aggregate_proof(
                snapshot["claims"],
                certificates,
                expected_target_ids=target_ids,
                expected_obligation_ids=obligation_ids,
                interface_report=interface_report,
                challenges=challenges,
                risks=risks,
                generator_identities=generator_identities,
            )
        loop = _loop_snapshot(aggregation, certificates, challenges)
        logger.info(
            "stage=w3_shadow loop_action=%s fuse_triggered=%s",
            loop["transition"]["action"] if isinstance(loop.get("transition"), dict) else "N/A",
            loop["fuse_triggered"],
        )
    else:
        challenges = []
        loop = {
            "status": "disabled-for-ablation",
            "policy": None,
            "progress": None,
            "transition": None,
            "transitions": [],
            "provider_calls_reused": 0,
            "repeated_task_count": 0,
            "fuse_triggered": False,
        }
        logger.info("stage=w3_shadow cognitive_loop=disabled")
    return {
        "status": "completed",
        "policy": correctness_policy.CORRECTNESS_POLICY_VERSION,
        "ledger": snapshot,
        "certificates": certificates,
        "semantic_audit": audit,
        "stage_interface_report": interface_report,
        "aggregation": aggregation,
        "challenges": challenges,
        "hypotheses": [],
        "loop": loop,
        "metrics": {
            "claim_count": len(active),
            "certificate_count": len(certificates),
            "verified_claim_count": len(aggregation["claim_evidence"]["verified_claim_ids"]),
            "critical_certificate_coverage": aggregation["claim_evidence"]["critical_certificate_coverage"],
            "unresolved_claim_count": len(active) - len(aggregation["claim_evidence"]["verified_claim_ids"]),
            "challenge_count": len(challenges),
            "loop_transition_count": (
                loop["transition"]["control"]["transition_count"] if isinstance(loop.get("transition"), dict) else 0
            ),
            "repeated_task_count": loop["repeated_task_count"],
            "fuse_triggered": loop["fuse_triggered"],
        },
    }


def solution_target_map(solution: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id", "")): item
        for item in solution.get("targets", [])
        if isinstance(item, dict) and item.get("id")
    }


def _without_runtime_identity(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return payload
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


def answer_signature(value: Any) -> str:
    text = str(value or "").lower()
    text = text.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉²³", "012345678923"))
    text = text.replace("\\pi", "π").replace("sqrt", "√")
    text = re.sub(r"\\(?:left|right|,|;|!|quad|qquad)", "", text)
    return re.sub(r"[\s，。；;：:、（）()]", "", text)


def augment_mandatory_physics_obligations(
    problem: str,
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    """Add source-triggered hard obligations that generic decomposition may omit."""
    text = str(problem)
    is_multifluid_plate = all(marker in text for marker in ("油", "水", "密度", "板", "下边缘", "合力")) and (
        "不再有水" in text or "完全排开" in text
    )
    obligations = [dict(item) for item in blueprint.get("verification_obligations", []) if isinstance(item, dict)]
    existing_ids = {str(item.get("id", "")) for item in obligations}
    if is_multifluid_plate:
        for target in blueprint.get("question_targets", []):
            target_id = str(target.get("id", "")).strip()
            obligation_id = f"{MULTIFLUID_COLUMN_OBLIGATION_PREFIX}{target_id}_domain"
            if not target_id or obligation_id in existing_ids:
                continue
            obligations.append({
                "id": obligation_id,
                "target_id": target_id,
                "check": (
                    "多流体静力必须先由同一深度压强连续确定各自由液面的实际高度，"
                    "再分别覆盖每种流体对板的完整润湿区间；不得只在外侧液面以下"
                    "积分压强差而漏掉较轻流体高出外侧液面的板段。最终合力表达式"
                    "必须保留由实际轻流体柱高度带来的密度比，不能退化为简单密度差。"
                ),
                "risk": "critical",
                "source": "deterministic-physics-guard",
            })
            existing_ids.add(obligation_id)

    source_rules = (
        (
            "event_order",
            re.compile(r"第一次|首次|最早"),
            re.compile(r"第一次|首次|最早|更早|事件顺序|先后"),
            "必须证明所选事件是首次/最早事件，并显式排除所有更早可行事件。",
        ),
        (
            "branch_completeness",
            re.compile(r"所有可能|全部可能|所有|全部|各个|每个"),
            re.compile(r"所有|全部|完整|分支|解支|枚举|遗漏"),
            "必须枚举全部物理可行分支，并说明每个代数根、周期或路径为何保留或排除。",
        ),
        (
            "domain_boundary",
            re.compile(r"范围|区间|进入.*区域|离开.*区域|边界|端点"),
            re.compile(r"定义域|适用域|范围|区间|边界|端点|场区|区域"),
            "必须核对物理定义域、区域归属和端点取舍，不能只验证区间内部。",
        ),
        (
            "reference_frame",
            re.compile(r"参考系|坐标系|相对.*(?:速度|位移|运动)"),
            re.compile(r"参考系|坐标系|正方向|相对速度|相对位移"),
            "必须固定参考系、坐标正方向和状态量变换，并在跨阶段时保持一致。",
        ),
    )
    for target in blueprint.get("question_targets", []):
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("id", "")).strip()
        prompt = str(target.get("prompt", "")).strip()
        if not target_id or not prompt:
            continue
        source_view = f"{prompt}\n{text}"
        for suffix, trigger, coverage, check in source_rules:
            if not trigger.search(source_view):
                continue
            if any(
                str(item.get("target_id", "")).strip() == target_id and coverage.search(str(item.get("check", "")))
                for item in obligations
            ):
                continue
            obligation_id = (f"{SOURCE_DOMAIN_OBLIGATION_PREFIX}{target_id}_{suffix}")[:40]
            if obligation_id in existing_ids:
                continue
            obligations.append({
                "id": obligation_id,
                "target_id": target_id,
                "check": check,
                "risk": "critical",
                "source": "deterministic-source-domain-guard",
            })
            existing_ids.add(obligation_id)
    if obligations == blueprint.get("verification_obligations", []):
        return blueprint
    return {**blueprint, "verification_obligations": obligations}


def answers_equivalent(left: Any, right: Any) -> bool:
    first, second = answer_signature(left), answer_signature(right)
    if not first or not second:
        return False
    final_first = first.rsplit("=", 1)[-1]
    final_second = second.rsplit("=", 1)[-1]
    return (
        first == second
        or final_first == final_second
        or (min(len(first), len(second)) >= 8 and (first in second or second in first))
    )


def project_blueprint(blueprint: dict[str, Any], target_ids: set[str]) -> dict[str, Any]:
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
            item for item in blueprint.get("question_targets", []) if str(item.get("id", "")) in target_ids
        ],
        "reasoning_steps": [
            {
                **item,
                "target_ids": [target_id for target_id in item.get("target_ids", []) if str(target_id) in target_ids],
            }
            for item in blueprint.get("reasoning_steps", [])
            if set(map(str, item.get("target_ids", []))) & target_ids
        ],
        "retrieval_needs": [
            {
                **item,
                "target_ids": [target_id for target_id in item.get("target_ids", []) if str(target_id) in target_ids],
            }
            for item in blueprint.get("retrieval_needs", [])
            if set(map(str, item.get("target_ids", []))) & target_ids
        ],
        "verification_obligations": [
            item for item in blueprint.get("verification_obligations", []) if str(item.get("id", "")) in obligation_ids
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


def selected_targets(solver_a: dict[str, Any], adjudication: dict[str, Any] | None) -> list[dict[str, Any]]:
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
        if alternative_result and not answers_equivalent(target.get("final_answer", ""), alternative_result):
            sections.append(section("交叉候选结论", alternative_result, actionable=True))
        sections.extend([
            section("仲裁采用结论", decision.get("selected_result"), actionable=True),
            section(
                "决定性关系",
                "\n".join(f"• {item}" for item in audit.get("decisive_checks", [])[:4] if str(item).strip()),
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


def claim_evidence_teacher_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    """Return the complete Claim evidence needed for private teacher review.

    Runtime identities, fingerprints, task metadata and raw semantic-audit
    payloads are deliberately excluded from this API projection.
    """
    evidence = report.get("claim_evidence_shadow", {}) if isinstance(report.get("claim_evidence_shadow"), dict) else {}
    run_status = str(evidence.get("status", "not-run"))
    empty = {
        "schema_version": 1,
        "status": run_status,
        "aggregation_status": "not-available",
        "final_answers": [],
        "claims": [],
        "certificates": [],
        "unresolved_obligations": [],
        "metrics": {},
    }
    if run_status != "completed":
        return empty

    aggregation = evidence.get("aggregation", {}) if isinstance(evidence.get("aggregation"), dict) else {}
    graph_evidence = (
        aggregation.get("claim_evidence", {}) if isinstance(aggregation.get("claim_evidence"), dict) else {}
    )
    assessments = {
        str(item.get("claim_id", "")): item
        for item in graph_evidence.get("assessments", [])
        if isinstance(item, dict) and item.get("claim_id")
    }
    claims = []
    unresolved = []
    for raw in graph_evidence.get("claims", []):
        if not isinstance(raw, dict):
            continue
        claim_id = str(raw.get("id", ""))
        version = int(raw.get("version", 1))
        status = str(raw.get("status", "candidate"))
        obligation_ids = [str(item) for item in raw.get("obligation_ids", []) if str(item)]
        claims.append({
            "id": claim_id,
            "version": version,
            "kind": str(raw.get("kind", "")),
            "statement": str(raw.get("statement", ""))[:2_000],
            "target_ids": [str(item) for item in raw.get("target_ids", []) if str(item)],
            "stage_ids": [str(item) for item in raw.get("stage_ids", []) if str(item)],
            "depends_on": [str(item) for item in raw.get("depends_on", []) if str(item)],
            "conditions": [str(item)[:300] for item in raw.get("conditions", []) if str(item)],
            "obligation_ids": obligation_ids,
            "status": status,
        })
        if status != "verified":
            assessment = assessments.get(claim_id, {})
            unresolved.append({
                "id": f"verify-{claim_id}-v{version}",
                "type": "claim-verification",
                "claim_id": claim_id,
                "claim_version": version,
                "obligation_ids": obligation_ids,
                "status": status,
                "issues": [str(item)[:500] for item in assessment.get("issues", []) if str(item)],
            })

    certificates = []
    for raw in evidence.get("certificates", []):
        if not isinstance(raw, dict):
            continue
        certificates.append({
            "claim_id": str(raw.get("claim_id", "")),
            "claim_version": int(raw.get("claim_version", 1)),
            "verifier_kind": str(raw.get("verifier_kind", "")),
            "check_type": str(raw.get("check_type", "")),
            "verdict": str(raw.get("verdict", "")),
            "normalized_result": str(raw.get("normalized_result", ""))[:2_000],
            "decisive_checks": [str(item)[:500] for item in raw.get("decisive_checks", []) if str(item)],
        })

    for index, raw in enumerate(aggregation.get("interface_issues", []), start=1):
        if not isinstance(raw, dict):
            continue
        unresolved.append({
            "id": f"interface-{index}",
            "type": "stage-interface",
            "status": str(aggregation.get("interface_status", "provisional")),
            "code": str(raw.get("code", "interface-review")),
            "message": str(raw.get("message", ""))[:500],
        })
    challenge_by_id = {
        str(item.get("id", "")): item
        for item in evidence.get("challenges", [])
        if isinstance(item, dict) and item.get("id")
    }
    for challenge_id in aggregation.get("open_challenge_ids", []):
        challenge = challenge_by_id.get(str(challenge_id), {})
        unresolved.append({
            "id": str(challenge_id),
            "type": "challenge",
            "status": "open",
            "claim_ids": [str(item) for item in challenge.get("claim_ids", []) if str(item)],
            "message": str(challenge.get("specific_doubt", ""))[:500],
            "falsification_test": str(challenge.get("falsification_test", ""))[:500],
        })
    hypothesis_by_id = {
        str(item.get("id", "")): item
        for item in evidence.get("hypotheses", [])
        if isinstance(item, dict) and item.get("id")
    }
    for hypothesis_id in aggregation.get("active_hypothesis_ids", []):
        hypothesis = hypothesis_by_id.get(str(hypothesis_id), {})
        unresolved.append({
            "id": str(hypothesis_id),
            "type": "hypothesis",
            "status": "active",
            "message": str(hypothesis.get("proposal", ""))[:500],
            "falsification_test": str(
                hypothesis.get("falsification", {}).get("procedure", "")
                if isinstance(hypothesis.get("falsification"), dict)
                else ""
            )[:500],
        })
    for index, issue in enumerate(aggregation.get("root_path_issues", []), start=1):
        unresolved.append({
            "id": f"root-path-{index}",
            "type": "root-path",
            "status": "provisional",
            "message": str(issue)[:500],
        })

    metrics = evidence.get("metrics", {})
    return {
        "schema_version": 1,
        "status": run_status,
        "aggregation_status": str(aggregation.get("status", "not-available")),
        "final_answers": [
            {
                "claim_id": str(item.get("claim_id", "")),
                "claim_version": int(item.get("claim_version", 1)),
                "target_ids": [str(target_id) for target_id in item.get("target_ids", []) if str(target_id)],
                "statement": str(item.get("statement", ""))[:2_000],
                "conditions": [str(condition)[:300] for condition in item.get("conditions", []) if str(condition)],
                "status": str(item.get("status", "candidate")),
                "obligation_ids": [
                    str(obligation_id) for obligation_id in item.get("obligation_ids", []) if str(obligation_id)
                ],
            }
            for item in aggregation.get("final_claims", [])
            if isinstance(item, dict)
        ],
        "claims": claims,
        "certificates": certificates,
        "unresolved_obligations": unresolved,
        "metrics": {
            key: metrics[key]
            for key in (
                "claim_count",
                "certificate_count",
                "verified_claim_count",
                "critical_certificate_coverage",
                "unresolved_claim_count",
                "challenge_count",
                "loop_transition_count",
                "repeated_task_count",
                "fuse_triggered",
            )
            if key in metrics and isinstance(metrics[key], (int, float, bool))
        },
    }


def attach_w3r_shadow(
    report: dict[str, Any],
    *,
    problem: str,
    method_profile: str = "high_school_standard",
) -> dict[str, Any]:
    """Attach a non-solving W3R experiment without changing the W3 candidate.

    W3R consumes only the private, bounded teacher snapshot.  A provisional
    proof, missing skeleton material, or render-gate failure stays inside this
    shadow field and never falls back to a Solver.
    """
    try:
        import w3_rendering
        import w3r_contract

        proof_package = claim_evidence_teacher_snapshot(report)
        projection = w3r_contract.build_w3r_brief(
            problem,
            report.get("blueprint", {}),
            proof_package,
            method_profile=method_profile,
        )
        if projection.get("status") != "completed":
            report["w3r_shadow"] = {
                "schema_version": 1,
                "mode": "shadow",
                "status": "needs_render_material",
                "violations": projection.get("violations", []),
                "production_candidate_unchanged": True,
                "solver_fallback_allowed": False,
            }
            return report
        render = w3_rendering.render_w3r(projection["brief"])
        report["w3r_shadow"] = {
            "schema_version": 1,
            "mode": "shadow",
            "status": render["status"],
            "brief_fingerprint": render["brief_fingerprint"],
            "brief": projection["brief"],
            "render_result": render,
            "production_candidate_unchanged": True,
            "solver_fallback_allowed": False,
        }
    except (ImportError, ValueError) as exc:
        report["w3r_shadow"] = {
            "schema_version": 1,
            "mode": "shadow",
            "status": "rejected",
            "violations": [
                {
                    "code": "w3r-shadow-contract-failure",
                    "message": str(exc)[:1_000],
                }
            ],
            "production_candidate_unchanged": True,
            "solver_fallback_allowed": False,
        }
    return report


def render_recommended_student_solution(
    blueprint: dict[str, Any],
    solver_a: dict[str, Any],
    adjudication: dict[str, Any] | None,
) -> str:
    """Deterministically render one concise teacher-reviewable answer candidate."""

    def high_school_student_text(text: str) -> str:
        """Apply condition-preserving rewrites for known high-school equivalents.

        W3 blueprints may use derivatives in their private audit layer.  For a
        release-from-rest instant, the equivalent student-layer proof uses
        ``s = at²/2`` over the same short interval.  Only this exact,
        condition-preserving rewrite is safe.  Likewise, a linear hydrostatic
        pressure resultant is the area of a triangular pressure-depth graph;
        no integration is needed in the student layer.  Other advanced methods
        remain visible and are rejected by the downstream method gate.
        """
        replacements = {
            "二次微分绳长约束并消去速度平方项": "用极短时间位移 s=at²/2 建立释放瞬间的绳长约束",
            "检查正方向约定与绳长微分符号一致": "检查正方向约定与绳长变化符号一致",
            "确认二次微分中的速度平方项只因释放瞬间初速度为零而消失，"
            "不能把所得加速度关系误当作全过程恒成立的常比例关系。": "确认用 s=at²/2 建立的位移关系依赖释放瞬间初速度为零，"  # noqa: E501
            "不能把所得加速度关系误当作全过程恒成立的常比例关系。",
            "分别积分得到水侧和油侧对右板的水平压力合力": "分别用三角形压强-深度图的面积得到水侧和油侧对右板的水平压力合力",  # noqa: E501
            "表压积分": "表压-深度图像面积",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return text

    targets = selected_targets(solver_a, adjudication)
    lines = ["## 答案速览", ""]
    for index, target in enumerate(targets, 1):
        lines.append(f"- （{index}）{target.get('final_answer', '')}")
    operations = [
        high_school_student_text(str(item.get("operation", "")).strip())
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
    stage_results = [item for item in solver_a.get("stage_results", []) if str(item.get("result", "")).strip()]
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
            high_school_student_text(str(stage.get("result", "")).strip()),
            "",
        ])
    obligations = sorted(
        blueprint.get("verification_obligations", []),
        key=lambda item: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(item.get("risk", "")), 4),
            str(item.get("id", "")),
        ),
    )
    lines.extend(["## 易错点", ""])
    for item in obligations[:3]:
        lines.append(f"- {high_school_student_text(str(item.get('check', '')).strip())}")
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
    evidence_status = str(evidence.get("evidence_set", {}).get("status") or evidence.get("status") or "unavailable")
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
    default_obligation_suggestions: list[dict[str, Any]] | None = None,
    claim_evidence_shadow: bool = False,
    method_profile: str = "high_school_standard",
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

        method_errors = student_method_errors(recommended, method_profile) if recommended else []
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
    summary = {
        "schema_version": 1,
        "policy": W3_POLICY,
        "mode": "shadow",
        "method_profile": method_profile,
        "screen": screen,
        "blueprint": blueprint,
        "evidence": evidence,
        "solver_a": _without_runtime_identity(solver_a),
        "risk_decisions": risk_decisions or [],
        "verifier": verifier,
        "solver_b": _without_runtime_identity(solver_b),
        "adjudication": _without_runtime_identity(adjudication),
        "stage_warnings": stage_warnings or [],
        "default_obligation_suggestions": default_obligation_suggestions or [],
        "recommended_student_solution": recommended,
        "teacher_review_focus": focus[:2],
        "metrics": {
            "target_count": len((solver_a or {}).get("targets", [])),
            "verified_target_count": sum(1 for item in (risk_decisions or []) if item.get("decision") == "verify"),
            "solver_b_used": solver_b is not None,
            "conflict_target_count": len(conflict_target_ids(solver_a or {}, verifier, solver_b)),
            "teacher_focus_count": len(focus[:2]),
        },
    }
    if claim_evidence_shadow:
        summary["claim_evidence_shadow"] = {
            "status": "enabled-not-run",
            "policy": correctness_policy.CORRECTNESS_POLICY_VERSION,
        }
    return summary


def run_shadow(
    problem: str,
    *,
    stage_runner: Callable[[str, dict[str, Any]], dict[str, Any]],
    evidence_builder: Callable[[dict[str, Any]], dict[str, Any]],
    has_physics_model: bool = False,
    calibration: dict[str, Any] | None = None,
    claim_evidence_shadow_enabled: bool | None = None,
    method_profile: str = "high_school_standard",
) -> dict[str, Any]:
    """Run W3 internal stages without writing canonical answer files.

    ``stage_runner`` is deliberately provider-neutral. It receives a stage name
    and a minimal context object, allowing the teacher Gateway to keep one
    external ``analysis.generate`` job while tests use deterministic fixtures.
    """
    calibration = {**DEFAULT_CALIBRATION, **(calibration or {})}
    import teaching_method_policy

    method_profile = teaching_method_policy.normalize_profile(method_profile)
    if claim_evidence_shadow_enabled is None:
        claim_evidence_shadow_enabled = correctness_policy.claim_evidence_shadow_enabled()
    screen = complexity_screen(problem, has_physics_model=has_physics_model)
    if screen["decision"] == "w2":
        return build_shadow_summary(
            screen=screen,
            claim_evidence_shadow=claim_evidence_shadow_enabled,
            method_profile=method_profile,
        )

    blueprint = stage_runner("decompose", {"problem": problem, "screen": screen})
    if blueprint.get("status") != "completed":
        return build_shadow_summary(
            screen=screen,
            blueprint=blueprint,
            claim_evidence_shadow=claim_evidence_shadow_enabled,
            method_profile=method_profile,
        )
    blueprint = augment_mandatory_physics_obligations(problem, blueprint)
    default_obligation_suggestions = infer_default_obligation_suggestions(problem, blueprint)
    evidence = evidence_builder(blueprint)
    solver_a = stage_runner(
        "solver-a",
        {"problem": problem, "blueprint": blueprint, "evidence": evidence},
    )
    if solver_a.get("status") != "completed":
        return build_shadow_summary(
            screen=screen,
            blueprint=blueprint,
            evidence=evidence,
            solver_a=solver_a,
            default_obligation_suggestions=default_obligation_suggestions,
            claim_evidence_shadow=claim_evidence_shadow_enabled,
            method_profile=method_profile,
        )

    decisions = risk_plan(blueprint, solver_a, evidence, calibration=calibration)
    verify_ids = {str(item.get("target_id", "")) for item in decisions if item.get("decision") == "verify"}
    verifier = None
    stage_warnings: list[dict[str, Any]] = []
    if verify_ids and not claim_evidence_shadow_enabled:
        try:
            verifier = stage_runner(
                "verifier",
                {
                    "problem": problem,
                    "blueprint": blueprint,
                    "evidence": evidence,
                    "candidate_targets": [item for item in solver_a.get("targets", []) if item.get("id") in verify_ids],
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

    challenge = set()
    if not claim_evidence_shadow_enabled:
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
    summary = build_shadow_summary(
        screen=screen,
        blueprint=blueprint,
        evidence=evidence,
        solver_a=solver_a,
        risk_decisions=decisions,
        verifier=verifier,
        solver_b=solver_b,
        adjudication=adjudication,
        stage_warnings=stage_warnings,
        default_obligation_suggestions=default_obligation_suggestions,
        claim_evidence_shadow=claim_evidence_shadow_enabled,
        method_profile=method_profile,
    )
    if claim_evidence_shadow_enabled:
        try:
            summary["claim_evidence_shadow"] = run_claim_evidence_shadow(
                problem,
                blueprint,
                solver_a,
                stage_runner=stage_runner,
                risk_decisions=decisions,
            )
        except Exception as exc:
            summary["claim_evidence_shadow"] = {
                "status": "failed",
                "policy": correctness_policy.CORRECTNESS_POLICY_VERSION,
                "message": str(exc)[:1_000],
            }
        attach_w3r_shadow(summary, problem=problem, method_profile=method_profile)
        aggregation_status = str(
            summary.get("claim_evidence_shadow", {}).get("aggregation", {}).get("status", "missing")
        )
        render_status = str(summary.get("w3r_shadow", {}).get("status", "missing"))
        method_errors = []
        try:
            from analysis_artifacts import student_method_errors

            method_errors = student_method_errors(
                str(summary.get("recommended_student_solution", "")),
                method_profile,
            )
        except ImportError:
            pass
        summary["evaluation_layers"] = {
            "solution_execution": {
                "status": ("completed" if summary.get("solver_a", {}).get("status") == "completed" else "failed"),
            },
            "proof_fidelity": {"status": aggregation_status},
            "teaching_method": {
                "profile": method_profile,
                "status": "pass" if not method_errors else "failed",
                "errors": method_errors,
            },
            "teaching_render": {"status": render_status},
        }
    return summary


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
        w2_correct = sum(int(value) for value in w2_values if value is not None) if w2_complete else None
        supplements = sum(int(item.get("validated_supplement_target_count", 0)) for item in cohort)
        revised_references = sum(1 for item in cohort if item.get("reference_revised_after_shadow"))
        calls = sum(int(item.get("agent_call_count", 0)) for item in cohort)
        focus = sum(int(item.get("teacher_focus_count", 0)) for item in cohort)
        return {
            "case_count": len(cohort),
            "target_count": targets,
            "target_accuracy": round(correct / targets, 4) if targets else None,
            "w2_target_accuracy": (round(w2_correct / targets, 4) if targets and w2_correct is not None else None),
            "accuracy_delta": (
                round((correct - w2_correct) / targets, 4) if targets and w2_correct is not None else None
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
        holdout_ready and holdout_metrics["accuracy_delta"] is not None and holdout_metrics["accuracy_delta"] >= 0
    )
    workload_safe = (
        holdout_ready
        and holdout_metrics["average_teacher_focus"] is not None
        and holdout_metrics["average_teacher_focus"] <= 2
    )
    independent_holdout_intact = holdout_ready and int(holdout_metrics["reference_revision_case_count"] or 0) == 0
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
            "production_eligible": bool(accuracy_safe and workload_safe and independent_holdout_intact),
        },
    }
