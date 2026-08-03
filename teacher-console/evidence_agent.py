#!/usr/bin/env python3
"""Single-route Evidence Agent shadow task.

The model performs bounded semantic matching over a deterministic candidate
pool.  It cannot solve the problem or approve its own sufficiency decision:
``build_evidence_agent_run`` applies the final deterministic Coverage Gate.
"""

from __future__ import annotations

import re
import sys
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import evidence_contract
from log import TraceContext, get_logger

logger = get_logger("evidence_agent")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_SCRIPTS = (
    PROJECT_ROOT
    / ".claude"
    / "skills"
    / "manage-student-error-library"
    / "scripts"
)
if str(LIBRARY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(LIBRARY_SCRIPTS))

import kb  # noqa: E402
import knowledge_store  # noqa: E402


REFLECTION_CONTRACT = "wuli.evidence-reflection.v3"
FUSION_POLICY = "single-route-bypass"
PURPOSE_UNIT_KINDS = {
    "method_candidate": {"method_applicability", "secondary_conclusion"},
    "applicability_check": {"method_applicability", "false_friend_warning"},
    "exception_check": {"false_friend_warning", "method_applicability"},
    "boundary_check": {"verification_rule", "false_friend_warning"},
    "verification_support": {"verification_rule", "secondary_conclusion"},
    "false_friend_check": {"false_friend_warning", "method_applicability"},
}
PURPOSE_USAGE = {
    "method_candidate": "method_hint",
    "applicability_check": "condition_warning",
    "exception_check": "condition_warning",
    "boundary_check": "condition_warning",
    "verification_support": "secondary_conclusion_candidate",
    "false_friend_check": "false_friend_warning",
}

_TEXT_ARRAY = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 32,
}
REFLECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "coverage": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "need_id": {"type": "string"},
                    "covered_facets": _TEXT_ARRAY,
                    "evidence_bindings": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "evidence_id": {"type": "string"},
                                "covered_facets": _TEXT_ARRAY,
                                "reason": {"type": "string"},
                            },
                            "required": [
                                "evidence_id",
                                "covered_facets",
                                "reason",
                            ],
                        },
                    },
                    "hard_conflicts": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "evidence_id": {"type": "string"},
                                "conflict": {"type": "string"},
                            },
                            "required": ["evidence_id", "conflict"],
                        },
                    },
                    "condition_verdict": {
                        "type": "string",
                        "enum": ["compatible", "incompatible", "uncertain"],
                    },
                    "forbidden_conflict_checks": {
                        "type": "array",
                        "maxItems": 16,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "conflict": {"type": "string"},
                                "verdict": {
                                    "type": "string",
                                    "enum": ["present", "absent", "uncertain"],
                                },
                                "reason": {"type": "string"},
                            },
                            "required": ["conflict", "verdict", "reason"],
                        },
                    },
                    "not_required": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "need_id",
                    "covered_facets",
                    "evidence_bindings",
                    "hard_conflicts",
                    "condition_verdict",
                    "forbidden_conflict_checks",
                    "not_required",
                    "reason",
                ],
            },
            "maxItems": 16,
        },
    },
    "required": ["status", "message", "coverage"],
}


def reflection_output_contract() -> dict[str, Any]:
    return {
        "name": REFLECTION_CONTRACT,
        "schema": REFLECTION_SCHEMA,
        "instructions": (
            "只做候选证据语义验收，不求解当前题、不生成答案或当前题 Claim。"
            "逐项检查 RetrievalNeed：只能引用候选池中现有 evidence_id；"
            "covered_facets 只能填写该 need.required_facets 中被 EvidenceUnit 直接支持"
            "的项目；EvidenceUnit 的 text、physics_facets、applicability、exceptions"
            "都是带来源绑定的证据字段，均可用于直接支持或冲突判断，但检索 score 不可作证；"
            "每条入选证据必须在 evidence_bindings 中单独绑定至少一个它直接支持的"
            "required facet；covered_facets 必须恰好等于全部 binding facet 的并集。"
            "不得把未覆盖任何 facet 的候选塞入 binding，也不得罗列整个候选池。"
            "hard_conflicts 只能报告已进入 evidence_bindings 的证据冲突，并必须绑定"
            "对应 evidence_id；未入选候选的不适用性不应否决当前 need。"
            "必须先把当前题条件与候选 applicability 逐项比较，输出 condition_verdict；"
            "还必须对每一条 forbidden_conflict 原样输出一次 present/absent/uncertain。"
            "diagnostic_targets 是本次证据要诊断或纠正的错误对象；当前解答出现该错误，"
            "不等于纠错证据不适用，不得仅据此产生 hard_conflict 或 incompatible；"
            "该语义适用于全部 purpose，不只适用于 false_friend_check。"
            "forbidden_conflicts 才是适用性禁区；若当前题明确具有该禁区，必须判 present"
            "且 condition_verdict=incompatible。"
            "不得用相似度、摘要或常识补齐原文没有覆盖的 facet。"
            "required need 的 not_required 必须为 false。"
            "status=unsupported 时 coverage 设为 null。最终充分性由程序判定。"
        ),
    }


def _terms(text: str) -> set[str]:
    return {
        token
        for token in kb.tokenize(str(text or ""))
        if len(token) > 1 or re.fullmatch(r"[a-z0-9.]+", token)
    }


def _candidate_score(need: dict[str, Any], unit: dict[str, Any]) -> tuple[float, str]:
    query_terms = _terms(
        " ".join(
            [
                need["question"],
                *need["required_facets"],
                *need["forbidden_conflicts"],
            ]
        )
    )
    unit_terms = _terms(
        " ".join(
            [
                unit["text"],
                *unit["physics_facets"],
                *unit["applicability"],
                *unit["exceptions"],
            ]
        )
    )
    overlap = len(query_terms & unit_terms)
    facet_hits = sum(
        1
        for facet in need["required_facets"]
        if _terms(facet) & unit_terms
    )
    purpose_match = unit["unit_kind"] in PURPOSE_UNIT_KINDS[need["purpose"]]
    authority = evidence_contract.AUTHORITY_RANK[unit["authority_level"]]
    score = overlap + 2.5 * facet_hits + (2.0 if purpose_match else 0.0) + authority * 0.1
    return score, "lexical-overlap+facet+purpose+authority"


def retrieve_single_route_candidates(
    projection: dict[str, Any],
    retrieval_needs: list[dict[str, Any]],
    *,
    top_k_per_need: int = 8,
    max_candidates: int = 32,
) -> dict[str, Any]:
    """Create one deterministic candidate pool; no RRF is involved."""
    needs = [evidence_contract.normalize_retrieval_need(item) for item in retrieval_needs]
    if projection.get("status") != "ready":
        logger.warning(
            "stage=retrieve status=unavailable reason=%s",
            projection.get("reason") or "projection-unavailable",
        )
        return {
            "status": "unavailable",
            "reason": str(projection.get("reason") or "projection-unavailable"),
            "route": FUSION_POLICY,
            "candidates": [],
            "queries": [],
        }
    units = [
        evidence_contract.normalize_evidence_unit(item)
        for item in projection.get("units", [])
    ]
    selected: dict[str, dict[str, Any]] = {}
    queries: list[dict[str, Any]] = []
    bounded_top_k = max(1, min(int(top_k_per_need), 20))
    for need in needs:
        ranked = []
        for unit in units:
            score, explanation = _candidate_score(need, unit)
            if score <= 0:
                continue
            ranked.append((score, unit["evidence_id"], explanation, unit))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        picked = ranked[:bounded_top_k]
        queries.append(
            {
                "query_id": f"query-{need['need_id']}-1",
                "need_id": need["need_id"],
                "route": "evidence-unit-lexical",
                "candidate_evidence_ids": [item[1] for item in picked],
                "scores": [
                    {
                        "evidence_id": item[1],
                        "score": round(item[0], 4),
                        "scoring": item[2],
                    }
                    for item in picked
                ],
            }
        )
        for _, evidence_id, _, unit in picked:
            selected[evidence_id] = unit
    ordered = sorted(
        selected.values(),
        key=lambda item: (
            -evidence_contract.AUTHORITY_RANK[item["authority_level"]],
            item["evidence_id"],
        ),
    )[: max(1, min(int(max_candidates), 64))]
    logger.info(
        "stage=retrieve need_count=%d candidate_count=%d query_count=%d",
        len(needs),
        len(ordered),
        len(queries),
    )
    allowed = {item["evidence_id"] for item in ordered}
    for query in queries:
        query["candidate_evidence_ids"] = [
            item for item in query["candidate_evidence_ids"] if item in allowed
        ]
        query["scores"] = [
            item for item in query["scores"] if item["evidence_id"] in allowed
        ]
    return {
        "status": "ready",
        "route": FUSION_POLICY,
        "projection_version": projection.get("projection_version", ""),
        "candidates": ordered,
        "queries": queries,
    }


def knowledge_store_fingerprint(projection: dict[str, Any]) -> str:
    return evidence_contract.stable_fingerprint(
        "evidence-unit-projection-snapshot-v1",
        {
            "status": projection.get("status"),
            "reason": projection.get("reason"),
            "projection_version": projection.get("projection_version"),
            "units": [
                {
                    "evidence_id": item.get("evidence_id"),
                    "content_hash": item.get("content_hash"),
                }
                for item in projection.get("units", [])
                if isinstance(item, dict)
            ],
        },
    )


def build_shadow_task(
    entry: Path,
    *,
    problem: str,
    blueprint: dict[str, Any],
    retrieval_needs: list[dict[str, Any]],
    candidate_pool: dict[str, Any],
    routing_tier: str = "expert",
    model_config: dict[str, Any] | None = None,
    allow_remote: bool = False,
) -> dict[str, Any]:
    """Build an isolated Gateway task while retaining current model routing."""
    entry = entry.resolve()
    normalized_model_config = dict(model_config or {})
    # Semantic evidence acceptance is a bounded classification task.  Low
    # effort reduces the chance that a provider spends its budget elaborating
    # past the strict structured-output contract.
    normalized_model_config.setdefault("effort", "low")
    return {
        "schema_version": 1,
        "id": f"evidence-build-{uuid.uuid4().hex}",
        # evidence.build is the domain stage. analysis.generate remains the
        # provider runtime kind until the model registry gains a separate kind.
        "kind": "analysis.generate",
        "evidence_stage": "evidence.build",
        "entry_id": entry.name,
        "entry_dir": str(entry),
        "working_dir": str(entry),
        "prompt": (
            "读取当前题目、冻结蓝图、RetrievalNeed 与单路候选池，"
            "按输出契约完成一次证据语义验收。不得继续检索。"
        ),
        "allowed_paths": [],
        "input_paths": ["problem.md"],
        "denied_paths": [],
        "hidden_paths": [],
        "requires_change": False,
        "timeout_seconds": 600,
        "allow_remote": bool(allow_remote),
        "routing_tier": routing_tier,
        "model_config": normalized_model_config,
        "workspace_root": "/tmp/wuli-agent-workspaces",
        "context_files": {},
        "context_payloads": {
            ".agent-context/evidence-blueprint.json": blueprint,
            ".agent-context/evidence-needs.json": {
                "schema": evidence_contract.RETRIEVAL_NEED_SCHEMA,
                "items": [
                    evidence_contract.normalize_retrieval_need(item)
                    for item in retrieval_needs
                ],
            },
            ".agent-context/evidence-candidates.json": candidate_pool,
            ".agent-context/question-snapshot.json": {"problem": problem},
        },
        "structured_context_paths": [
            "problem.md",
            ".agent-context/evidence-blueprint.json",
            ".agent-context/evidence-needs.json",
            ".agent-context/evidence-candidates.json",
            ".agent-context/question-snapshot.json",
        ],
        "output_contract": reflection_output_contract(),
    }


def normalize_reflection(
    payload: dict[str, Any],
    *,
    retrieval_needs: list[dict[str, Any]],
    candidate_pool: dict[str, Any],
) -> dict[str, Any]:
    """Reject semantic output that escapes the frozen need/candidate universe."""
    if not isinstance(payload, dict):
        raise ValueError("evidence reflection must be an object")
    status = str(payload.get("status") or "").strip()
    if status not in {"completed", "unsupported"}:
        raise ValueError("evidence reflection status is invalid")
    if status == "unsupported":
        if payload.get("coverage") is not None:
            raise ValueError("unsupported evidence reflection must have null coverage")
        return {"status": status, "message": str(payload.get("message") or "")[:1000], "coverage": None}

    needs = [evidence_contract.normalize_retrieval_need(item) for item in retrieval_needs]
    need_by_id = {item["need_id"]: item for item in needs}
    allowed_ids = {
        item["evidence_id"] for item in candidate_pool.get("candidates", [])
    }
    rows = payload.get("coverage")
    if not isinstance(rows, list):
        raise ValueError("evidence reflection coverage must be an array")
    normalized = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"evidence reflection coverage[{index}] must be an object")
        need_id = str(raw.get("need_id") or "").strip()
        if need_id not in need_by_id or need_id in seen:
            raise ValueError("evidence reflection must contain each known need exactly once")
        seen.add(need_id)
        bindings_raw = raw.get("evidence_bindings")
        if not isinstance(bindings_raw, list):
            raise ValueError("evidence_bindings must be an array")
        bindings = []
        evidence_ids = []
        bound_facets: set[str] = set()
        for binding in bindings_raw:
            if not isinstance(binding, dict):
                raise ValueError("evidence binding must be an object")
            evidence_id = str(binding.get("evidence_id") or "").strip()
            if not evidence_id or evidence_id in evidence_ids:
                raise ValueError("evidence bindings require unique evidence ids")
            binding_facets = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in binding.get("covered_facets", [])
                    if str(item).strip()
                )
            )
            if not binding_facets:
                raise ValueError("each evidence binding must cover at least one facet")
            if set(binding_facets) - set(need_by_id[need_id]["required_facets"]):
                raise ValueError("evidence binding invents a facet outside RetrievalNeed")
            evidence_ids.append(evidence_id)
            bound_facets.update(binding_facets)
            bindings.append(
                {
                    "evidence_id": evidence_id,
                    "covered_facets": binding_facets,
                    "reason": str(binding.get("reason") or "").strip()[:1000],
                }
            )
        if set(evidence_ids) - allowed_ids:
            raise ValueError("evidence reflection references evidence outside candidate pool")
        facets = list(dict.fromkeys(str(item).strip() for item in raw.get("covered_facets", []) if str(item).strip()))
        if set(facets) - set(need_by_id[need_id]["required_facets"]):
            raise ValueError("evidence reflection invents a facet outside RetrievalNeed")
        if set(facets) != bound_facets:
            raise ValueError("covered_facets must equal the evidence binding facet union")
        conflicts_raw = raw.get("hard_conflicts")
        if not isinstance(conflicts_raw, list):
            raise ValueError("hard_conflicts must be an array")
        conflicts = []
        for conflict_raw in conflicts_raw:
            if not isinstance(conflict_raw, dict):
                raise ValueError("hard conflict must be an object")
            conflict_evidence_id = str(
                conflict_raw.get("evidence_id") or ""
            ).strip()
            conflict_text = str(conflict_raw.get("conflict") or "").strip()[:1000]
            if conflict_evidence_id not in evidence_ids or not conflict_text:
                raise ValueError("hard conflict must bind selected evidence")
            conflicts.append(
                {
                    "evidence_id": conflict_evidence_id,
                    "conflict": conflict_text,
                }
            )
        condition_verdict = str(raw.get("condition_verdict") or "").strip()
        if condition_verdict not in {"compatible", "incompatible", "uncertain"}:
            raise ValueError("evidence reflection condition_verdict is invalid")
        checks_raw = raw.get("forbidden_conflict_checks")
        if not isinstance(checks_raw, list):
            raise ValueError("forbidden_conflict_checks must be an array")
        checks = []
        checked_conflicts: set[str] = set()
        for check in checks_raw:
            if not isinstance(check, dict):
                raise ValueError("forbidden conflict check must be an object")
            conflict = str(check.get("conflict") or "").strip()
            verdict = str(check.get("verdict") or "").strip()
            if (
                conflict not in need_by_id[need_id]["forbidden_conflicts"]
                or conflict in checked_conflicts
            ):
                raise ValueError("forbidden conflict checks must bind each declared conflict")
            if verdict not in {"present", "absent", "uncertain"}:
                raise ValueError("forbidden conflict verdict is invalid")
            checked_conflicts.add(conflict)
            checks.append(
                {
                    "conflict": conflict,
                    "verdict": verdict,
                    "reason": str(check.get("reason") or "").strip()[:1000],
                }
            )
        if checked_conflicts != set(need_by_id[need_id]["forbidden_conflicts"]):
            raise ValueError("forbidden conflict checks must cover every declared conflict")
        not_required = bool(raw.get("not_required"))
        if not_required and need_by_id[need_id]["criticality"] == "required":
            raise ValueError("required RetrievalNeed cannot be marked not_required")
        normalized.append(
            {
                "need_id": need_id,
                "evidence_ids": evidence_ids,
                "evidence_bindings": bindings,
                "covered_facets": facets,
                "hard_conflicts": conflicts,
                "condition_verdict": condition_verdict,
                "forbidden_conflict_checks": checks,
                "not_required": not_required,
                "reason": str(raw.get("reason") or "").strip()[:1000],
            }
        )
    if seen != set(need_by_id):
        raise ValueError("evidence reflection must contain each known need exactly once")
    return {
        "status": status,
        "message": str(payload.get("message") or "").strip()[:1000],
        "coverage": normalized,
    }


def reflection_materializer(
    retrieval_needs: list[dict[str, Any]],
    candidate_pool: dict[str, Any],
):
    def materialize(_staging: Path, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "contract": REFLECTION_CONTRACT,
            "payload": normalize_reflection(
                payload,
                retrieval_needs=retrieval_needs,
                candidate_pool=candidate_pool,
            ),
        }

    return materialize


def _empty_coverage(needs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "need_id": item["need_id"],
            "status": "missing" if item["criticality"] == "required" else "not_required",
            "evidence_ids": [],
            "covered_facets": [],
            "missing_facets": list(item["required_facets"]),
            "hard_conflicts": [],
        }
        for item in needs
    ]


def _explicit_problem_conflicts(problem: str, need: dict[str, Any]) -> list[str]:
    """Conservative lexical sentinel for conflicts stated in the current problem."""
    problem_terms = _terms(problem)
    hits = []
    for conflict in need["forbidden_conflicts"]:
        conflict_terms = _terms(conflict)
        if conflict_terms and len(problem_terms & conflict_terms) / len(conflict_terms) >= 0.6:
            hits.append(f"explicit-current-problem-conflict:{conflict}")
    return hits


def _matches_diagnostic_target(text: str, need: dict[str, Any]) -> bool:
    """Bind a model conflict to an explicitly frozen diagnostic target."""
    text = str(text or "").strip()
    if not text:
        return False
    text_terms = _terms(text)
    for target in need.get("diagnostic_targets", []):
        target = str(target or "").strip()
        if not target:
            continue
        if target in text or text in target:
            return True
        target_terms = _terms(target)
        if (
            target_terms
            and len(text_terms & target_terms) / len(target_terms) >= 0.6
        ):
            return True
    return False


def build_evidence_agent_run(
    *,
    problem: str,
    blueprint: dict[str, Any],
    retrieval_needs: list[dict[str, Any]],
    projection: dict[str, Any],
    candidate_pool: dict[str, Any],
    reflection: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the deterministic Coverage Gate and create a replayable run."""
    needs = [evidence_contract.normalize_retrieval_need(item) for item in retrieval_needs]
    question_hash = evidence_contract.stable_fingerprint("question-snapshot-v1", problem)
    blueprint_hash = evidence_contract.stable_fingerprint("problem-blueprint-v1", blueprint)
    store_hash = knowledge_store_fingerprint(projection)
    task_hash = evidence_contract.evidence_build_task_fingerprint(
        question_snapshot_hash=question_hash,
        blueprint_fingerprint=blueprint_hash,
        knowledge_store_fingerprint=store_hash,
        retrieval_needs=needs,
        fusion_policy=FUSION_POLICY,
    )
    common = {
        "schema": evidence_contract.EVIDENCE_AGENT_RUN_SCHEMA,
        "task_fingerprint": task_hash,
        "question_snapshot_hash": question_hash,
        "blueprint_fingerprint": blueprint_hash,
        "knowledge_store_fingerprint": store_hash,
        "fusion_policy": FUSION_POLICY,
        "retrieval_needs": needs,
    }
    required = [item for item in needs if item["criticality"] == "required"]
    if not required:
        logger.info("stage=evidence_build status=not_needed need_count=%d", len(needs))
        return evidence_contract.normalize_evidence_agent_run(
            {
                **common,
                "status": "not_needed",
                "evidence_set": [],
                "coverage": _empty_coverage(needs),
                "usage_ledger": {"entries": []},
                "retrieval_trace": [],
                "insufficient_evidence": None,
            }
        )
    if candidate_pool.get("status") != "ready" or not reflection or reflection.get("status") != "completed":
        reason = (
            candidate_pool.get("reason")
            or (reflection or {}).get("message")
            or "semantic-reflection-unavailable"
        )
        logger.warning("stage=evidence_build status=unavailable reason=%s", str(reason)[:200])
        return evidence_contract.normalize_evidence_agent_run(
            {
                **common,
                "status": "unavailable",
                "evidence_set": [],
                "coverage": _empty_coverage(needs),
                "usage_ledger": {"entries": []},
                "retrieval_trace": [],
                "insufficient_evidence": str(reason)[:2000],
            }
        )

    normalized_reflection = normalize_reflection(
        reflection,
        retrieval_needs=needs,
        candidate_pool=candidate_pool,
    )
    candidate_by_id = {
        item["evidence_id"]: item for item in candidate_pool["candidates"]
    }
    need_by_id = {item["need_id"]: item for item in needs}
    coverage = []
    selected_ids: set[str] = set()
    usages: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in normalized_reflection["coverage"]:
        need = need_by_id[row["need_id"]]
        evidence_ids = row["evidence_ids"]
        missing = [
            facet
            for facet in need["required_facets"]
            if facet not in row["covered_facets"]
        ]
        below_authority = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_contract.AUTHORITY_RANK[
                candidate_by_id[evidence_id]["authority_level"]
            ]
            < evidence_contract.AUTHORITY_RANK[need["minimum_authority"]]
        ]
        model_conflicts = [
            item for item in row["hard_conflicts"]
            if not _matches_diagnostic_target(item["conflict"], need)
        ]
        ignored_diagnostic_conflicts = (
            len(row["hard_conflicts"]) - len(model_conflicts)
        )
        conflicts = [
            f"{item['evidence_id']}:{item['conflict']}"
            for item in model_conflicts
        ]
        forbidden_present = any(
            item["verdict"] != "absent"
            for item in row["forbidden_conflict_checks"]
        )
        diagnostic_only_incompatibility = (
            ignored_diagnostic_conflicts > 0
            and not model_conflicts
            and not forbidden_present
        )
        if (
            row["condition_verdict"] != "compatible"
            and not diagnostic_only_incompatibility
        ):
            conflicts.append(f"condition-{row['condition_verdict']}")
        conflicts.extend(
            f"forbidden-conflict-{item['verdict']}:{item['conflict']}"
            for item in row["forbidden_conflict_checks"]
            if item["verdict"] != "absent"
        )
        conflicts.extend(_explicit_problem_conflicts(problem, need))
        if below_authority:
            conflicts.append(
                "authority-below-minimum:" + ",".join(sorted(below_authority))
            )
        if row["not_required"] and need["criticality"] == "optional":
            status = "not_required"
            evidence_ids = []
        elif conflicts:
            status = "conflicted"
        elif evidence_ids and not missing:
            status = "covered"
        else:
            status = "missing"
        if status == "covered":
            selected_ids.update(evidence_ids)
            usage = PURPOSE_USAGE[need["purpose"]]
            for evidence_id in evidence_ids:
                usages[(evidence_id, usage)].add(need["need_id"])
        else:
            # Rejected candidates remain visible in retrieval_trace and the
            # conflict reason, but must not enter the accepted Evidence Set.
            evidence_ids = []
        coverage.append(
            {
                "need_id": need["need_id"],
                "status": status,
                "evidence_ids": evidence_ids,
                "covered_facets": row["covered_facets"],
                "missing_facets": missing,
                "hard_conflicts": conflicts,
            }
        )

    required_rows = [
        row for row in coverage if need_by_id[row["need_id"]]["criticality"] == "required"
    ]
    sufficient = all(row["status"] == "covered" for row in required_rows)
    logger.info(
        "stage=evidence_build status=%s required_count=%d covered_count=%d",
        "sufficient" if sufficient else "insufficient",
        len(required_rows),
        sum(1 for row in required_rows if row["status"] == "covered"),
    )
    evidence_set = [
        candidate_by_id[evidence_id] for evidence_id in sorted(selected_ids)
    ]
    ledger = {
        "entries": [
            {
                "evidence_id": evidence_id,
                "need_ids": sorted(need_ids),
                "usage": usage,
                "influenced_claim_ids": [],
                "verification_ids": [],
            }
            for (evidence_id, usage), need_ids in sorted(usages.items())
        ]
    }
    candidate_ids = [
        item["evidence_id"] for item in candidate_pool.get("candidates", [])
    ]
    newly_covered = [
        row["need_id"] for row in coverage if row["status"] == "covered"
    ]
    gap_text = "; ".join(
        f"{row['need_id']}:{row['status']}"
        for row in required_rows
        if row["status"] != "covered"
    )
    return evidence_contract.normalize_evidence_agent_run(
        {
            **common,
            "status": "sufficient" if sufficient else "insufficient",
            "evidence_set": evidence_set,
            "coverage": coverage,
            "usage_ledger": ledger,
            "retrieval_trace": [
                {
                    "round": 1,
                    "query_ids": [
                        item["query_id"] for item in candidate_pool.get("queries", [])
                    ],
                    "candidate_evidence_ids": candidate_ids,
                    "newly_covered_need_ids": newly_covered,
                    "stop_reason": (
                        "all-required-needs-covered"
                        if sufficient
                        else "single-route-shadow-budget-exhausted"
                    ),
                }
            ],
            "insufficient_evidence": None if sufficient else gap_text,
        }
    )


def load_projection_and_candidates(
    library_root: Path,
    retrieval_needs: list[dict[str, Any]],
    *,
    exclude_entry_id: str,
    top_k_per_need: int = 8,
    source_kinds: tuple[str, ...] = (),
    projection_overlay: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    projection = knowledge_store.load_evidence_unit_projection(
        library_root,
        exclude_entry_id=exclude_entry_id,
        limit=2000,
    )
    if projection_overlay and projection.get("status") == "ready":
        overlay_units = [
            evidence_contract.normalize_evidence_unit(item)
            for item in projection_overlay
        ]
        existing_ids = {
            item["evidence_id"] for item in projection.get("units", [])
        }
        collisions = existing_ids & {
            item["evidence_id"] for item in overlay_units
        }
        if collisions:
            raise ValueError(
                f"projection overlay collides with Knowledge Store: {sorted(collisions)}"
            )
        projection = {
            **projection,
            "overlay_unit_count": len(overlay_units),
            "units": [*projection.get("units", []), *overlay_units],
        }
        projection["unit_count"] = len(projection["units"])
    if source_kinds and projection.get("status") == "ready":
        allowed = set(source_kinds)
        unknown = allowed - set(evidence_contract.SOURCE_AUTHORITIES)
        if unknown:
            raise ValueError(f"unknown evidence source kinds: {sorted(unknown)}")
        projection = {
            **projection,
            "unit_count_before_source_filter": len(projection.get("units", [])),
            "source_kind_filter": sorted(allowed),
            "units": [
                item
                for item in projection.get("units", [])
                if item.get("source_kind") in allowed
            ],
        }
        projection["unit_count"] = len(projection["units"])
    return projection, retrieve_single_route_candidates(
        projection,
        retrieval_needs,
        top_k_per_need=top_k_per_need,
    )


def run_shadow(
    gateway: Any,
    *,
    library_root: Path,
    entry: Path,
    problem: str,
    blueprint: dict[str, Any],
    retrieval_needs: list[dict[str, Any]],
    top_k_per_need: int = 8,
    routing_tier: str = "expert",
    model_config: dict[str, Any] | None = None,
    allow_remote: bool = False,
    source_kinds: tuple[str, ...] = (),
    projection_overlay: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute one Evidence Agent shadow run through Agent Gateway."""
    trace_id = f"evidence-{entry.name}"
    with TraceContext(trace_id, log=logger) as ctx:
        ctx.info("stage=evidence_shadow status=started entry_id=%s", entry.name)
    projection, candidate_pool = load_projection_and_candidates(
        library_root,
        retrieval_needs,
        exclude_entry_id=entry.name,
        top_k_per_need=top_k_per_need,
        source_kinds=source_kinds,
        projection_overlay=projection_overlay,
    )
    normalized_needs = [
        evidence_contract.normalize_retrieval_need(item) for item in retrieval_needs
    ]
    required = [
        item for item in normalized_needs if item["criticality"] == "required"
    ]
    gateway_result: dict[str, Any] | None = None
    reflection = None
    if projection.get("status") == "ready" and required:
        task = build_shadow_task(
            entry,
            problem=problem,
            blueprint=blueprint,
            retrieval_needs=normalized_needs,
            candidate_pool=candidate_pool,
            routing_tier=routing_tier,
            model_config=model_config,
            allow_remote=allow_remote,
        )
        gateway_result = gateway.run(
            task,
            materializer=reflection_materializer(normalized_needs, candidate_pool),
        )
        if gateway_result.get("status") == "completed":
            materialization = gateway_result.get("materialization")
            if isinstance(materialization, dict):
                reflection = materialization.get("payload")
            logger.info("stage=evidence_shadow gateway=completed entry_id=%s", entry.name)
        elif gateway_result:
            logger.warning(
                "stage=evidence_shadow gateway=failed entry_id=%s failure_type=%s",
                entry.name,
                gateway_result.get("failure_type") or "unknown",
            )
            reflection = {
                "status": "unsupported",
                "message": str(
                    gateway_result.get("failure_type")
                    or gateway_result.get("message")
                    or "agent-gateway-failed"
                ),
                "coverage": None,
            }
    run = build_evidence_agent_run(
        problem=problem,
        blueprint=blueprint,
        retrieval_needs=normalized_needs,
        projection=projection,
        candidate_pool=candidate_pool,
        reflection=reflection,
    )
    logger.info(
        "stage=evidence_shadow status=completed entry_id=%s run_status=%s",
        entry.name,
        run["status"],
    )
    return {
        "schema": "wuli.evidence-build-shadow-execution.v1",
        "status": run["status"],
        "candidate_pool": candidate_pool,
        "gateway_result": gateway_result,
        "evidence_agent_run": run,
    }
