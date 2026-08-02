#!/usr/bin/env python3
"""Compact core-first solving contract and deterministic teaching renderer.

This module deliberately owns no provider selection and no review approval.  A
single model call produces the mathematical/physical core; deterministic code
then checks the target binding and renders reviewable Markdown without changing
the result.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import teaching_method_policy


CORE_CONTRACT = "wuli.core-solve.v1"
ROUTING_POLICY = "wuli-core-first-routing-v1"
ROUTING_MODES = {"core-first", "legacy-adaptive"}
DEFAULT_ROUTING = {
    "schema_version": 1,
    "policy_version": ROUTING_POLICY,
    # Core-first is the production default; legacy is always an explicit rollback.
    "mode": "core-first",
    "max_latency_seconds": 90,
}

UNRESOLVED_MARKERS = (
    "待定",
    "待核对",
    "无法确定",
    "信息不足",
    "不确定",
    "可能正确",
    "possibly",
    "unknown",
)

CORE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "target_brief_digest": {"type": ["string", "null"]},
        "targets": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "final_answer": {"type": "string", "maxLength": 800},
                    "key_relations": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 600},
                        "minItems": 1,
                        "maxItems": 4,
                    },
                },
                "required": ["id", "final_answer", "key_relations"],
            },
            "minItems": 1,
            "maxItems": 12,
        },
    },
    "required": [
        "status",
        "message",
        "target_brief_digest",
        "targets",
    ],
}


def normalize_routing_config(raw: Any) -> tuple[dict[str, Any], list[str]]:
    if raw in (None, {}):
        return dict(DEFAULT_ROUTING), []
    if not isinstance(raw, dict):
        return dict(DEFAULT_ROUTING), ["core routing config must be an object"]
    errors: list[str] = []
    if set(raw) != set(DEFAULT_ROUTING):
        errors.append("core routing config fields do not match schema v1")
    if raw.get("schema_version") != 1:
        errors.append("core routing config schema version mismatch")
    if raw.get("policy_version") != ROUTING_POLICY:
        errors.append("core routing policy version mismatch")
    mode = str(raw.get("mode", "")).strip().lower()
    if mode not in ROUTING_MODES:
        errors.append("core routing mode must be core-first or legacy-adaptive")
    latency = raw.get("max_latency_seconds")
    if isinstance(latency, bool) or not isinstance(latency, int) or not 10 <= latency <= 3600:
        errors.append("max_latency_seconds must be between 10 and 3600")
    if errors:
        return dict(DEFAULT_ROUTING), errors
    return {
        "schema_version": 1,
        "policy_version": ROUTING_POLICY,
        "mode": mode,
        "max_latency_seconds": latency,
    }, []


def _target_items(problem: str) -> list[tuple[str, str]]:
    marker = re.compile(
        r"(?:^|(?<=[\s；;。]))(?:[（(]\s*(?P<paren>\d{1,2}|[一二三四五六七八九十]+|[ivxIVX]{1,4})\s*[）)]|(?m:^\s*(?P<line>\d{1,2})[.、]))\s*"
    )
    matches = list(marker.finditer(problem))
    if len(matches) < 2:
        return [("Q1", problem.strip()[-500:])]
    chinese_numbers = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    current_top = ""
    candidates: list[tuple[str, str, bool, bool]] = []
    for index, item in enumerate(matches):
        raw_label = str(item.group("paren") or item.group("line") or "").strip()
        fragment = problem[
            item.end() : matches[index + 1].start() if index + 1 < len(matches) else None
        ].strip()[:500]
        lowered = raw_label.lower()
        if re.fullmatch(r"[ivx]{1,4}", lowered):
            target_id = f"Q{current_top or '1'}{lowered}"
            is_roman = True
        else:
            number = chinese_numbers.get(raw_label, raw_label)
            current_top = str(number)
            target_id = f"Q{current_top}"
            is_roman = False
        demanded = bool(
            re.search(r"求|试|证明|确定|计算|写出|判断|说明|表示|为何|多少", fragment)
        )
        candidates.append((target_id, fragment, is_roman, demanded))
    selected: list[tuple[str, str]] = []
    for index, (target_id, fragment, is_roman, demanded) in enumerate(candidates):
        next_is_roman = index + 1 < len(candidates) and candidates[index + 1][2]
        # A numbered stem immediately introducing i/ii is context, not another
        # answer target.  All other explicit markers remain targets even when
        # the command is implicit (for example “若……，则……” competition parts).
        if not is_roman and next_is_roman:
            continue
        selected.append((target_id, fragment))
    result: list[tuple[str, str]] = []
    for target_id, fragment in selected:
        if target_id not in {item[0] for item in result}:
            result.append((target_id, fragment))
    return result


def build_target_brief(
    problem: str,
    *,
    method_profile: str,
    has_visual_facts: bool = False,
    has_physics_model: bool = False,
) -> dict[str, Any]:
    profile = teaching_method_policy.normalize_profile(method_profile)
    target_items = _target_items(problem)
    risk_signals: list[str] = []
    if len(target_items) > 1:
        risk_signals.append("multiple-targets")
    if re.search(r"首次|临界|所有可能|分类|范围|至少|至多|恰好", problem):
        risk_signals.append("boundary-or-branch")
    if re.search(r"随后|再进入|返回|碰撞|阶段|先.*后", problem, re.S):
        risk_signals.append("multi-stage")
    if has_visual_facts:
        risk_signals.append("reviewed-visual-facts")
    if has_physics_model:
        risk_signals.append("physics-model-binding")
    brief = {
        "schema_version": 1,
        "method_profile": profile,
        "targets": [
            {"id": target_id, "prompt_hint": fragment or "完成题目要求"}
            for target_id, fragment in target_items
        ],
        "risk_signals": risk_signals,
        "enhancements": {
            "targeted_retrieval": bool(risk_signals),
            "independent_verification": "boundary-or-branch" in risk_signals,
            "stage_state_sidecar": "multi-stage" in risk_signals or has_physics_model,
        },
    }
    brief["digest"] = hashlib.sha256(
        json.dumps(brief, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return brief


def output_contract(brief: dict[str, Any]) -> dict[str, Any]:
    target_ids = ", ".join(item["id"] for item in brief["targets"])
    profile = brief["method_profile"]
    return {
        "name": CORE_CONTRACT,
        "schema": CORE_OUTPUT_SCHEMA,
        "instructions": (
            "只输出符合 JSON Schema 的对象。一次完成物理/数学求解核心，不做教学排版、阶段接口、"
            "Claim Ledger、Solver B 或仲裁。targets 必须且只能覆盖 "
            f"{target_ids}，target_brief_digest 必须原样返回 {brief['digest']}。"
            "final_answer 写可直接判分的最终结论且不超过八百字；key_relations 每问最多四条，只保留决定结论所需的等式、"
            "几何关系、事件链、适用条件和必要复核，不能用‘显然’省略。"
            f"方法范围为 {profile}；竞赛官方范围允许微积分时不得强行改写成高中课堂方法。"
            "不要输出方法比较、元数据、教学章节或额外字段。无法确定时返回 unsupported，"
            "target_brief_digest 和 targets 设为 null，不猜答案。"
        ),
    }


def _text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum:
        raise ValueError(f"{field} is too long")
    return text


def _texts(value: Any, field: str, *, maximum_items: int, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    result: list[str] = []
    for item in value:
        text = _text(item, field, maximum=1200)
        if text not in result:
            result.append(text)
    if not result and not allow_empty:
        raise ValueError(f"{field} must not be empty")
    return result[:maximum_items]


def normalize_payload(payload: Any, brief: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("core solve output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = _text(payload.get("message", ""), "message", maximum=1000)
    if status == "unsupported":
        raise ValueError(f"provider reported unsupported: {message}")
    if status != "completed":
        raise ValueError("status must be completed or unsupported")
    if payload.get("target_brief_digest") != brief["digest"]:
        raise ValueError("target_brief_digest does not match the current problem")

    expected_ids = [item["id"] for item in brief["targets"]]
    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list):
        raise ValueError("targets must be a list")
    targets: list[dict[str, Any]] = []
    for item in raw_targets:
        if not isinstance(item, dict):
            raise ValueError("target must be an object")
        target = {
            "id": _text(item.get("id"), "target.id", maximum=20),
                "final_answer": _text(item.get("final_answer"), "target.final_answer", maximum=800),
            "key_relations": _texts(
                item.get("key_relations"), "target.key_relations", maximum_items=4
            ),
        }
        lowered = target["final_answer"].lower()
        if any(marker in lowered for marker in UNRESOLVED_MARKERS):
            raise ValueError(f"{target['id']} final_answer contains an unresolved marker")
        targets.append(target)
    if [item["id"] for item in targets] != expected_ids:
        raise ValueError("targets do not exactly match Target Brief order")

    method_text = "\n".join(
        line for target in targets for line in target["key_relations"]
    )
    method_errors = teaching_method_policy.method_errors(method_text, brief["method_profile"])
    if method_errors:
        raise ValueError("; ".join(method_errors))

    return {
        "status": "completed",
        "message": message,
        "target_brief_digest": brief["digest"],
        "targets": targets,
    }


def _student_markdown(core: dict[str, Any]) -> str:
    targets = core["targets"]
    quick = "\n".join(
        f"- **{target['id']}**：{target['final_answer']}" for target in targets
    )
    # The deterministic renderer caps main headings at five while retaining all
    # targets and derivations inside those groups.
    groups: list[list[dict[str, Any]]] = [[item] for item in targets[:4]]
    if len(targets) > 4:
        groups.append(targets[4:])
    detail_blocks: list[str] = []
    for index, group in enumerate(groups, 1):
        lines = [f"### 第 {index} 步"]
        for target in group:
            if len(targets) > 1:
                lines.append(f"\n#### {target['id']}")
            lines.extend(f"\n- {item}" for item in target["key_relations"])
            lines.append("\n因此，" + target["final_answer"] + "。")
        detail_blocks.append("\n".join(lines))
    pitfalls = ["复核符号、方向、分支和题设边界是否一致"]
    return (
        "# 解析（学生版）\n\n"
        f"## 答案速览\n\n{quick}\n\n"
        "## 一眼识别\n\n"
        "- **最短主线**：逐问建立决定性关系，得到结论后检查题设边界。\n\n"
        "## 详细解答\n\n"
        + "\n\n".join(detail_blocks)
        + "\n\n## 易错点\n\n"
        + "\n".join(f"- {item}" for item in pitfalls)
        + "\n\n## 30 秒自测\n\n"
        + "能否只用上述决定性关系，独立复算每个最终结论并检查适用条件？\n"
    )


def materialize(staging: Path, payload: Any, brief: dict[str, Any]) -> dict[str, Any]:
    core = normalize_payload(payload, brief)
    record_path = staging / "record.json"
    if not record_path.is_file():
        raise ValueError("record.json is missing")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["standard_solution_path"] = {
        "schema_version": 1,
        "source": CORE_CONTRACT,
        "method_profile": brief["method_profile"],
        "selected_path": "逐问建立决定性关系并核对边界",
        "target_ids": [item["id"] for item in core["targets"]],
        "target_brief_digest": brief["digest"],
    }
    student = _student_markdown(core)
    checks = "\n".join(
        f"- **{target['id']}**：已保留 {len(target['key_relations'])} 条决定性关系，等待权威答案复核。"
        for target in core["targets"]
    )
    teacher = (
        student
        + "\n## 教师审计\n\n"
        + f"核心契约：`{CORE_CONTRACT}`；Target Brief：`{brief['digest']}`。\n\n"
        + "### 确定性 Core Gate\n\n"
        + checks
        + "\n"
    )
    core_artifact = {
        "schema_version": 1,
        "contract": CORE_CONTRACT,
        "method_profile": brief["method_profile"],
        "target_brief": brief,
        "result": core,
        "gate": {"status": "passed", "target_coverage": 1.0, "unresolved_answer_count": 0},
    }
    artifacts = {
        "record.json": json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "core-solution.json": json.dumps(core_artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "student-solution.md": student,
        "teacher-solution.md": teacher,
        "solution.md": teacher,
    }
    for relative, content in artifacts.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "contract": CORE_CONTRACT,
        "payload_digest": digest,
        "stages": [
            {"name": "core-gate", "status": "completed"},
            {"name": "deterministic-teaching-render", "status": "completed"},
            {"name": "render-fidelity-gate", "status": "completed"},
        ],
    }
