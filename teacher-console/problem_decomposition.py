#!/usr/bin/env python3
"""W3 complexity screening and structured two-layer physics blueprint contract."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import structured_text

BLUEPRINT_CONTRACT = "wuli.problem-decompose.v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_UNIT_REGISTRY_PATH = (
    PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts" / "difficulty_knowledge_units.json"
)
KNOWLEDGE_UNIT_REGISTRY = json.loads(KNOWLEDGE_UNIT_REGISTRY_PATH.read_text(encoding="utf-8"))
KNOWLEDGE_UNIT_IDS = {str(item["id"]) for item in KNOWLEDGE_UNIT_REGISTRY["units"]}
COGNITIVE_OPERATION_IDS = {
    "identify",
    "apply",
    "explain",
    "audit",
    "reconstruct",
    "algebra_only",
}
DEFAULT_OBLIGATION_POLICY = "wuli.default-obligation-rules.v1"

_DEFAULT_SOLVE_PATTERN = re.compile(r"(?:求|求出|计算|确定|给出).{0,24}(?:时刻|时间|位置|坐标|速度|范围|取值|结果|解)")
_ALL_SOLUTIONS_SUPPRESSOR = re.compile(
    r"第一次|首次|最早|最后|唯一|最小正值|最小|最大|任一|一个可能|取一个|只求|只需|是否存在|判断是否|证明"
)
_EXPLICIT_ALL_SOLUTIONS = re.compile(r"所有|全部|各个|每个|所有可能|全部可能|可能的|解集|范围")
_BRANCH_COMPLETENESS_CHECK = re.compile(r"所有|全部|可能|分支|解支|解集|枚举|完整")

STRONG_PATTERNS = {
    "completeness-language": re.compile(r"第一次|首次|唯一|所有可能|全部|至少|至多|最大|最小|临界"),
    "piecewise-process": re.compile(r"先.{0,20}(?:再|然后)|当.{0,20}时|直到|随后|接着|每隔|交替"),
    "multi-region": re.compile(r"区域|边界|上半平面|下半平面|圆内|圆外|场区|分区"),
    "multi-object": re.compile(r"甲、?乙|两(?:个|种|粒子|物体)|分别|同时"),
    "inverse-modeling": re.compile(r"轨道方程|待定系数|反推|随.{0,10}(?:位置|时间|x).{0,10}(?:变化|关系)"),
}
ORDINARY_PATTERNS = {
    "multiple-targets": re.compile(r"(?:（|\()[1-9一二三四五六七八九十](?:）|\))|第[一二三四五六七八九十]+问"),
    "classification": re.compile(r"分类讨论|可能|不同情况|若|否则"),
    "graph-inference": re.compile(r"图像|图线|斜率|面积|函数图"),
    "compound-domain": re.compile(r"电场.{0,40}磁场|磁场.{0,40}电场|感应.{0,40}电路|电路.{0,40}感应"),
}


def complexity_screen(problem: str, *, has_physics_model: bool = False) -> dict[str, Any]:
    """Deterministically decide whether W3 decomposition is warranted."""
    text = str(problem or "").strip()
    signals: list[dict[str, Any]] = []
    for name, pattern in STRONG_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            signals.append({"id": name, "weight": 2, "count": len(matches)})
    for name, pattern in ORDINARY_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            weight = 2 if name == "multiple-targets" and len(matches) >= 3 else 1
            signals.append({"id": name, "weight": weight, "count": len(matches)})
    if has_physics_model:
        signals.append({"id": "existing-physics-model", "weight": 2, "count": 1})
    score = sum(int(item["weight"]) for item in signals)
    strong = any(int(item["weight"]) >= 2 for item in signals)
    decision = "decompose" if strong or score >= 2 else "w2"
    return {
        "schema_version": 1,
        "decision": decision,
        "score": score,
        "risk_signals": signals,
        "reason": (
            "strong structural risk or multiple ordinary risks detected"
            if decision == "decompose"
            else "no structural evidence that a separate blueprint would repay its cost"
        ),
    }


ID_ARRAY = {"type": "array", "items": {"type": "string"}, "maxItems": 16}
TEXT_ARRAY = {"type": "array", "items": {"type": "string"}, "maxItems": 12}
BLUEPRINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "question_targets": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "answer_type": {"type": "string"},
                },
                "required": ["id", "prompt", "answer_type"],
            },
            "maxItems": 12,
        },
        "physical_stages": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "entry_conditions": TEXT_ARRAY,
                    "exit_conditions": TEXT_ARRAY,
                    "state_carried": TEXT_ARRAY,
                },
                "required": ["id", "label", "entry_conditions", "exit_conditions", "state_carried"],
            },
            "maxItems": 16,
        },
        "stage_transitions": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "from_stage": {"type": "string"},
                    "to_stage": {"type": "string"},
                    "event": {"type": "string"},
                },
                "required": ["from_stage", "to_stage", "event"],
            },
            "maxItems": 20,
        },
        "reasoning_steps": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "operation": {"type": "string"},
                    "depends_on": ID_ARRAY,
                    "target_ids": ID_ARRAY,
                    "decisive_relations": TEXT_ARRAY,
                },
                "required": [
                    "id",
                    "operation",
                    "depends_on",
                    "target_ids",
                    "decisive_relations",
                ],
            },
            "maxItems": 16,
        },
        "stage_step_links": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stage_id": {"type": "string"},
                    "step_id": {"type": "string"},
                },
                "required": ["stage_id", "step_id"],
            },
            "maxItems": 32,
        },
        "retrieval_needs": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "purpose": {"type": "string"},
                    "query": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                    "target_ids": ID_ARRAY,
                    "stage_ids": ID_ARRAY,
                },
                "required": ["id", "purpose", "query", "priority", "target_ids", "stage_ids"],
            },
            "maxItems": 8,
        },
        "verification_obligations": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "target_id": {"type": "string"},
                    "check": {"type": "string"},
                    "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                },
                "required": ["id", "target_id", "check", "risk"],
            },
            "maxItems": 16,
        },
    },
    "required": [
        "status",
        "message",
        "question_targets",
        "physical_stages",
        "stage_transitions",
        "reasoning_steps",
        "stage_step_links",
        "retrieval_needs",
        "verification_obligations",
    ],
}


def _assessment_annotated_schema() -> dict[str, Any]:
    """Build the opt-in shadow contract without changing the solve contract."""
    schema = deepcopy(BLUEPRINT_SCHEMA)
    step_schema = schema["properties"]["reasoning_steps"]["items"]
    step_schema["properties"]["cognitive_operation"] = {
        "type": "string",
        "enum": sorted(COGNITIVE_OPERATION_IDS),
    }
    step_schema["properties"]["knowledge_units"] = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "enum": sorted(KNOWLEDGE_UNIT_IDS)},
                "relation_indexes": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "minItems": 1,
                    "maxItems": 12,
                },
            },
            "required": ["id", "relation_indexes"],
        },
        "maxItems": 12,
    }
    step_schema["required"].extend(["cognitive_operation", "knowledge_units"])
    schema["properties"]["type_distance"] = {
        "type": ["object", "null"],
        "additionalProperties": False,
        "properties": {
            "mode": {
                "type": "string",
                "enum": [
                    "direct_archetype",
                    "routine_variant",
                    "standard_transfer",
                    "model_reconstruction",
                    "non_obvious_bridge",
                    "novel_construction",
                ],
            },
            "archetype": {"type": "string"},
            "recognition_barrier": {"type": "string"},
            "novel_bridge": {"type": "string"},
        },
        "required": ["mode", "archetype", "recognition_barrier", "novel_bridge"],
    }
    schema["required"].append("type_distance")
    return schema


ASSESSMENT_ANNOTATED_SCHEMA = _assessment_annotated_schema()


def output_contract() -> dict[str, Any]:
    return {
        "name": BLUEPRINT_CONTRACT,
        "schema": BLUEPRINT_SCHEMA,
        "instructions": (
            "只输出结构化双层蓝图，不求最终数值答案，不写教学 Markdown。"
            "physical_stages 描述真实物理过程，reasoning_steps 描述求解操作，二者不得混用。"
            "reasoning_steps 应绑定可复算的 decisive_relations；纯代数整理可留空。"
            "每个题目目标必须被至少一个 reasoning_step 和一个 verification_obligation 覆盖。"
            "retrieval_needs 合并相似方法需求并按 priority 排序；不要复制历史答案。"
            "status=unsupported 时其余集合字段设为 null。"
        ),
    }


def assessment_output_contract() -> dict[str, Any]:
    """Opt-in shadow contract for paired non-regression experiments only."""
    return {
        "name": f"{BLUEPRINT_CONTRACT}.difficulty-shadow-v1",
        "schema": ASSESSMENT_ANNOTATED_SCHEMA,
        "instructions": (
            output_contract()["instructions"]
            + "这是影子评分实验：每个 reasoning_step 额外使用 cognitive_operation 标记"
            " identify/apply/explain/audit/reconstruct/algebra_only，并用 knowledge_units"
            " 将独立且不可绕过的规律绑定到 relation_indexes；同一定律的分量式、重复"
            "使用和代数变形使用同一 id。type_distance 按固定枚举标注。"
            "这些标注会在进入检索、Solver 和验证器前剥离。"
        ),
    }


def _text(value: Any, field: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return structured_text.reject_unsupported_controls(value, field).strip()[:maximum]


def _default_obligation_id(target_id: str, suffix: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", target_id).strip("_")[:24] or "target"
    return f"default_{safe}_{suffix}"


def infer_default_obligation_suggestions(
    problem: str,
    blueprint: dict[str, Any],
) -> list[dict[str, Any]]:
    """Suggest default-convention obligations without changing solver gates.

    These suggestions are diagnostic only. They are not appended to
    verification_obligations, because doing so would change the solve contract
    and could break already-correct answer paths.
    """
    problem_text = structured_text.reject_unsupported_controls(str(problem or ""), "problem").strip()
    existing_obligations = [item for item in blueprint.get("verification_obligations", []) if isinstance(item, dict)]
    suggestions: list[dict[str, Any]] = []
    for target in blueprint.get("question_targets", []) or []:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("id", "")).strip()
        prompt = structured_text.reject_unsupported_controls(
            str(target.get("prompt", "")), "question_targets.prompt"
        ).strip()
        if not target_id or not prompt:
            continue
        combined = f"{prompt}\n{problem_text[:600]}"
        if not _DEFAULT_SOLVE_PATTERN.search(prompt):
            continue
        explicit_all = bool(_EXPLICIT_ALL_SOLUTIONS.search(prompt))
        suppressors = sorted(set(_ALL_SOLUTIONS_SUPPRESSOR.findall(combined)))
        if suppressors and not explicit_all:
            continue
        already_covered = any(
            str(item.get("target_id", "")).strip() == target_id
            and _BRANCH_COMPLETENESS_CHECK.search(str(item.get("check", "")))
            for item in existing_obligations
        )
        if already_covered:
            continue
        suggestions.append({
            "id": _default_obligation_id(target_id, "all_physical_solutions"),
            "target_id": target_id,
            "type": "branch-completeness",
            "source": "default-convention",
            "policy": DEFAULT_OBLIGATION_POLICY,
            "rule_id": "default.solve.all-physical-solutions.v1",
            "trigger_text": prompt[:160],
            "suppressed_by": [
                "第一次",
                "首次",
                "最早",
                "唯一",
                "最小正值",
                "只求一个",
                "只需判断是否存在",
                "指定区间",
            ],
            "check": "题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。",
            "risk": "high",
            "status": "suggested",
        })
    return suggestions[:12]


def _texts(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result = []
    for item in value:
        text = _text(item, field, 240)
        if text not in result:
            result.append(text)
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    return result


def _acyclic(steps: list[dict[str, Any]]) -> bool:
    dependencies = {item["id"]: set(item["depends_on"]) for item in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        for dependency in dependencies.get(node, set()):
            if not visit(dependency):
                return False
        visiting.remove(node)
        visited.add(node)
        return True

    return all(visit(node) for node in dependencies)


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("blueprint output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = _text(payload.get("message", ""), "message", 1000)
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("status must be completed or unsupported")

    targets = []
    for raw in payload.get("question_targets") or []:
        targets.append({
            "id": _text(raw.get("id"), "question_targets.id", 40),
            "prompt": _text(raw.get("prompt"), "question_targets.prompt", 300),
            "answer_type": _text(raw.get("answer_type"), "question_targets.answer_type", 80),
        })
    if not targets or len(targets) > 12:
        raise ValueError("question_targets must contain 1-12 items")
    target_ids = [item["id"] for item in targets]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError("question target ids must be unique")

    stages = []
    for raw in payload.get("physical_stages") or []:
        stages.append({
            "id": _text(raw.get("id"), "physical_stages.id", 40),
            "label": _text(raw.get("label"), "physical_stages.label", 160),
            "entry_conditions": _texts(raw.get("entry_conditions"), "physical_stages.entry_conditions"),
            "exit_conditions": _texts(raw.get("exit_conditions"), "physical_stages.exit_conditions"),
            "state_carried": _texts(raw.get("state_carried"), "physical_stages.state_carried"),
        })
    stage_ids = [item["id"] for item in stages]
    if len(set(stage_ids)) != len(stage_ids):
        raise ValueError("physical stage ids must be unique")

    transitions = []
    for raw in payload.get("stage_transitions") or []:
        item = {
            "from_stage": _text(raw.get("from_stage"), "stage_transitions.from_stage", 40),
            "to_stage": _text(raw.get("to_stage"), "stage_transitions.to_stage", 40),
            "event": _text(raw.get("event"), "stage_transitions.event", 200),
        }
        if item["from_stage"] not in stage_ids or item["to_stage"] not in stage_ids:
            raise ValueError("stage transition references an unknown stage")
        transitions.append(item)

    steps = []
    for raw in payload.get("reasoning_steps") or []:
        decisive_relations = _texts(
            raw.get("decisive_relations"),
            "reasoning_steps.decisive_relations",
        )
        item: dict[str, Any] = {
            "id": _text(raw.get("id"), "reasoning_steps.id", 40),
            "operation": _text(raw.get("operation"), "reasoning_steps.operation", 240),
            "depends_on": _texts(raw.get("depends_on"), "reasoning_steps.depends_on"),
            "target_ids": _texts(raw.get("target_ids"), "reasoning_steps.target_ids", allow_empty=False),
            "decisive_relations": decisive_relations,
        }
        if not set(item["target_ids"]).issubset(target_ids):
            raise ValueError("reasoning step references an unknown target")
        steps.append(item)
    if not steps or len(steps) > 16:
        raise ValueError("reasoning_steps must contain 1-16 items")
    step_ids = [item["id"] for item in steps]
    if len(set(step_ids)) != len(step_ids):
        raise ValueError("reasoning step ids must be unique")
    if any(not set(item["depends_on"]).issubset(step_ids) for item in steps):
        raise ValueError("reasoning step depends on an unknown step")
    if not _acyclic(steps):
        raise ValueError("reasoning step dependencies must be acyclic")
    covered_targets = {target for item in steps for target in item["target_ids"]}
    if set(target_ids) - covered_targets:
        raise ValueError("every question target must be covered by a reasoning step")

    links = []
    for raw in payload.get("stage_step_links") or []:
        item = {
            "stage_id": _text(raw.get("stage_id"), "stage_step_links.stage_id", 40),
            "step_id": _text(raw.get("step_id"), "stage_step_links.step_id", 40),
        }
        if item["stage_id"] not in stage_ids or item["step_id"] not in step_ids:
            raise ValueError("stage-step link references an unknown id")
        links.append(item)

    needs = []
    for raw in payload.get("retrieval_needs") or []:
        target_refs = _texts(raw.get("target_ids"), "retrieval_needs.target_ids")
        stage_refs = _texts(raw.get("stage_ids"), "retrieval_needs.stage_ids")
        if not set(target_refs).issubset(target_ids) or not set(stage_refs).issubset(stage_ids):
            raise ValueError("retrieval need references an unknown target or stage")
        priority = raw.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
            raise ValueError("retrieval_needs.priority must be an integer from 1 to 5")
        needs.append({
            "id": _text(raw.get("id"), "retrieval_needs.id", 40),
            "purpose": _text(raw.get("purpose"), "retrieval_needs.purpose", 240),
            "query": _text(raw.get("query"), "retrieval_needs.query", 300),
            "priority": priority,
            "target_ids": target_refs,
            "stage_ids": stage_refs,
        })
    if not needs:
        raise ValueError("retrieval_needs must not be empty")
    if len({item["id"] for item in needs}) != len(needs):
        raise ValueError("retrieval need ids must be unique")

    obligations = []
    for raw in payload.get("verification_obligations") or []:
        target_id = _text(raw.get("target_id"), "verification_obligations.target_id", 40)
        if target_id not in target_ids:
            raise ValueError("verification obligation references an unknown target")
        risk = str(raw.get("risk", "")).strip().lower()
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("verification obligation risk is invalid")
        obligations.append({
            "id": _text(raw.get("id"), "verification_obligations.id", 40),
            "target_id": target_id,
            "check": _text(raw.get("check"), "verification_obligations.check", 240),
            "risk": risk,
        })
    if not obligations or len({item["id"] for item in obligations}) != len(obligations):
        raise ValueError("verification obligations must be non-empty with unique ids")
    obligation_targets = {item["target_id"] for item in obligations}
    if set(target_ids) - obligation_targets:
        raise ValueError("every question target must have a verification obligation")

    return {
        "status": "completed",
        "message": message,
        "question_targets": targets,
        "physical_stages": stages,
        "stage_transitions": transitions,
        "reasoning_steps": steps,
        "stage_step_links": links,
        "retrieval_needs": sorted(needs, key=lambda item: (-item["priority"], item["id"])),
        "verification_obligations": obligations,
    }


def split_assessment_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate shadow annotations and return a clean solve blueprint separately.

    The first return value is byte-shape compatible with the production solve
    contract and is the only value allowed to reach retrieval, Solver,
    verification, or adjudication.
    """
    solve_payload = deepcopy(payload)
    raw_steps = [item for item in payload.get("reasoning_steps") or [] if isinstance(item, dict)]
    for item in solve_payload.get("reasoning_steps") or []:
        if isinstance(item, dict):
            item.pop("cognitive_operation", None)
            item.pop("knowledge_units", None)
    solve_payload.pop("type_distance", None)
    solve_blueprint = normalize_payload(solve_payload)

    annotations = []
    for raw, normalized in zip(raw_steps, solve_blueprint["reasoning_steps"]):
        cognitive_operation = str(raw.get("cognitive_operation", "")).strip()
        if cognitive_operation not in COGNITIVE_OPERATION_IDS:
            raise ValueError("reasoning_steps.cognitive_operation is invalid")
        decisive_relations = normalized["decisive_relations"]
        knowledge_units = []
        for unit in raw.get("knowledge_units") or []:
            unit_id = str(unit.get("id", "")).strip()
            if unit_id not in KNOWLEDGE_UNIT_IDS:
                raise ValueError("reasoning_steps.knowledge_units.id is invalid")
            indexes = unit.get("relation_indexes")
            if (
                not isinstance(indexes, list)
                or not indexes
                or any(
                    isinstance(index, bool)
                    or not isinstance(index, int)
                    or index < 0
                    or index >= len(decisive_relations)
                    for index in indexes
                )
            ):
                raise ValueError("knowledge unit must bind valid decisive relation indexes")
            knowledge_units.append({
                "id": unit_id,
                "relation_indexes": sorted(set(indexes)),
            })
        if cognitive_operation != "algebra_only" and not knowledge_units:
            raise ValueError("non-algebra assessment annotation must bind a knowledge unit")
        if cognitive_operation == "algebra_only" and knowledge_units:
            raise ValueError("algebra-only assessment annotation cannot bind knowledge units")
        annotations.append({
            "step_id": normalized["id"],
            "cognitive_operation": cognitive_operation,
            "knowledge_units": knowledge_units,
        })

    raw_type_distance = payload.get("type_distance")
    if not isinstance(raw_type_distance, dict):
        raise ValueError("type_distance must be an object")
    mode = str(raw_type_distance.get("mode", "")).strip()
    allowed_modes = {
        "direct_archetype",
        "routine_variant",
        "standard_transfer",
        "model_reconstruction",
        "non_obvious_bridge",
        "novel_construction",
    }
    if mode not in allowed_modes:
        raise ValueError("type_distance.mode is invalid")
    return solve_blueprint, {
        "schema_version": 1,
        "registry_version": str(KNOWLEDGE_UNIT_REGISTRY["registry_version"]),
        "reasoning_steps": annotations,
        "type_distance": {
            "mode": mode,
            "archetype": _text(
                raw_type_distance.get("archetype"),
                "type_distance.archetype",
                160,
            ),
            "recognition_barrier": _text(
                raw_type_distance.get("recognition_barrier"),
                "type_distance.recognition_barrier",
                240,
            ),
            "novel_bridge": str(raw_type_distance.get("novel_bridge", "")).strip()[:240],
        },
    }
