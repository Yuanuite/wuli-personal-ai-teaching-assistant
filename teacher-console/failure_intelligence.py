#!/usr/bin/env python3
"""Deterministic failure evidence and bounded repair policy for Agent tasks.

This module never runs a provider and never promotes candidate files.  It only
decides whether one fresh, isolated retry is safe and builds a compact evidence
pack for that retry.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Callable

from log import logger

SCHEMA_VERSION = 1
AUTO_RETRY_FAILURES = {
    "candidate_validation_failed": "按领域校验错误修正候选后重新提交",
    "output_truncated": "缩短输出并确保所有必需结构完整闭合",
    "candidate_no_change": "检查允许路径并实际生成所需候选文件",
}
NEVER_RETRY_FAILURES = {
    "canonical_changed": "canonical 条目已变化，必须由教师刷新后重新发起",
    "unauthorized_change": "候选越过允许路径，禁止自动重试",
}
DEFERRED_FAILURES = {
    "provider_timeout": "Gateway 已完成 provider 降级；稍后重试可避免重复计费",
    "provider_rate_limited": "等待限流窗口结束后再提交",
    "provider_unavailable": "先修复或切换模型配置",
    "adapter_protocol_error": "先修复 provider adapter 协议",
    "provider_execution_failed": "先检查 provider 运行环境",
    "provider_failed": "需要更多诊断信息后再提交",
    "worker_interrupted": "教师工作台重启后需重新提交原任务",
    "simulation_build_failed": "需要检查仿真模型与构建器兼容性",
}
PROTECTED_RECORD_FIELDS = {
    "schema_version",
    "id",
    "kind",
    "status",
    "answer_status",
    "created_at",
    "library_folder",
    "source",
    "ocr",
    "source_review",
    "answer_review",
    "visualization_review",
    "generated_from",
    "review",
}
MAX_EVIDENCE_ITEMS = 5
MAX_TEXT = 600
MAX_PROMPT_APPEND = 3000


def repair_decision(failure_type: str) -> dict:
    """Return a stable, auditable policy decision for one failure code."""
    code = str(failure_type or "provider_failed").strip() or "provider_failed"
    if code in AUTO_RETRY_FAILURES:
        return {
            "policy": "single-corrective-retry",
            "auto_retry": True,
            "action": AUTO_RETRY_FAILURES[code],
            "max_retries": 1,
        }
    if code in NEVER_RETRY_FAILURES:
        return {
            "policy": "blocked-by-safety-boundary",
            "auto_retry": False,
            "action": NEVER_RETRY_FAILURES[code],
            "max_retries": 0,
        }
    return {
        "policy": "defer-or-manual-repair",
        "auto_retry": False,
        "action": DEFERRED_FAILURES.get(code, "保留诊断证据并由教师重新提交"),
        "max_retries": 0,
    }


def _short(value, limit: int = MAX_TEXT) -> str:
    text = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    # Remove common absolute paths and opaque entry/job identifiers.  Repair
    # evidence needs the failure pattern, not student identity or local layout.
    text = re.sub(r"/(?:Users|home|private|tmp)/\S+", "[local-path]", text)
    text = re.sub(r"\b[0-9a-f]{24,64}\b", "[opaque-id]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:sk|key|token)-[A-Za-z0-9_.-]{8,}\b", "[redacted-secret]", text, flags=re.IGNORECASE)
    text = re.sub(r"\bBearer\s+[A-Za-z0-9_.-]+", "Bearer [redacted]", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(api[_-]?key|token|password|secret)\s*[=:]\s*\S+", r"\1=[redacted]", text, flags=re.IGNORECASE)
    return text[:limit] + ("…" if len(text) > limit else "")


def _safe_strings(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_short(value) for value in values[:10] if str(value or "").strip()]


def _event_failure_type(event: dict) -> str:
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    repair = result.get("failure_repair") if isinstance(result.get("failure_repair"), dict) else {}
    return str(result.get("failure_type") or repair.get("initial_failure_type") or "").strip()


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def build_failure_evidence(library: Path, task_type: str, current_result: dict) -> dict:
    """Build a compact, privacy-safe evidence pack from current and prior runs."""
    failure_type = str(current_result.get("failure_type") or "provider_failed")
    logger.info("failure type=%s task_type=%s building evidence", failure_type, task_type)
    decision = repair_decision(failure_type)
    current = {
        "failure_type": failure_type,
        "validation_errors": _safe_strings(current_result.get("validation_errors")),
        "unauthorized_changes": _safe_strings(current_result.get("unauthorized_changes")),
        "message": _short(current_result.get("message") or current_result.get("stderr")),
    }
    matches: list[dict] = []
    events = _read_jsonl(library / "indexes" / "candidate-archive.jsonl")
    for event in reversed(events):
        if event.get("task_type") != task_type or _event_failure_type(event) != failure_type:
            continue
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        matches.append({
            "outcome": str(event.get("status", "recorded")),
            "failure_type": failure_type,
            "summary": _short(event.get("summary")),
            "failure_reasons": _safe_strings(event.get("failure_reasons")),
            "validation_errors": _safe_strings(result.get("validation_errors")),
            "repair_status": _short((result.get("failure_repair") or {}).get("status"))
            if isinstance(result.get("failure_repair"), dict)
            else "",
        })
        if len(matches) >= MAX_EVIDENCE_ITEMS:
            break
    return {
        "schema_version": SCHEMA_VERSION,
        "task_type": str(task_type),
        "failure_type": failure_type,
        "policy": decision,
        "current_failure": current,
        "similar_failure_patterns": matches,
        "reference_count": len(matches),
        "privacy": "No entry ids, prompts, source content, API keys, or absolute paths are included.",
    }


def task_with_repair_evidence(task: dict, evidence: dict) -> dict:
    """Return a copied task carrying bounded corrective context."""
    repaired = copy.deepcopy(task)
    repaired["id"] = f"{task.get('id', 'agent-task')}-repair-1"
    payloads = dict(repaired.get("context_payloads") or {})
    payloads[".agent-context/failure-evidence.json"] = evidence
    repaired["context_payloads"] = payloads
    current = evidence.get("current_failure") if isinstance(evidence.get("current_failure"), dict) else {}
    errors = current.get("validation_errors") or current.get("message") or ["候选未通过校验"]
    if isinstance(errors, str):
        errors = [errors]

    # Extract specific protected fields that were incorrectly changed.
    changed_fields: list[str] = []
    for error in errors:
        match = re.search(r"protected field changed:\s*(\S+)", error)
        if match:
            changed_fields.append(match.group(1))

    parts: list[str] = []
    if changed_fields:
        parts.append(
            "你修改了 record.json 的受保护字段"
            + "、".join(changed_fields)
            + "。Gateway 禁止修改这些字段。请读取 canonical 目录中的 record.json，"
            + "恢复以下字段的原值（只读不改）：\n"
            + "\n".join(f"  - 禁止修改：{field}" for field in sorted(PROTECTED_RECORD_FIELDS))
            + "\n可更新的字段：knowledge_points、error_types、difficulty、grade、title。"
        )
    parts.extend(_safe_strings(errors))
    corrective = (
        "\n\n【一次性失败修复】上轮候选没有写入正式条目。请读取 .agent-context/failure-evidence.json，保持原任务与允许路径不变，只修正下列问题：\n- "
        + "\n- ".join(parts)
    )
    corrective += "\n必须生成完整、可校验的候选；不要批准、发布或修改 denied_paths。"
    repaired["prompt"] = (str(repaired.get("prompt", "")) + corrective[:MAX_PROMPT_APPEND]).strip()
    return repaired


def run_with_failure_repair(
    task: dict,
    validator,
    *,
    library: Path,
    run_once: Callable[[dict, object], dict],
) -> dict:
    """Run once and, for content-shape failures only, make one fresh retry."""
    initial = run_once(task, validator)
    if initial.get("status") == "completed":
        return initial
    failure_type = str(initial.get("failure_type") or "provider_failed")
    logger.info(
        "failure_repair task=%s kind=%s initial_failure=%s", task.get("id", "?"), task.get("kind"), failure_type
    )
    evidence = build_failure_evidence(library, str(task.get("kind", "")), initial)
    decision = evidence["policy"]
    if not decision["auto_retry"]:
        initial["failure_repair"] = {
            "status": "not-retried",
            "initial_failure_type": failure_type,
            "final_failure_type": failure_type,
            "retry_count": 0,
            "policy": decision["policy"],
            "action": decision["action"],
            "evidence_reference_count": evidence["reference_count"],
        }
        return initial

    retried = run_once(task_with_repair_evidence(task, evidence), validator)
    initial_attempts = initial.get("attempts") if isinstance(initial.get("attempts"), list) else []
    retry_attempts = retried.get("attempts") if isinstance(retried.get("attempts"), list) else []
    retried["attempts"] = [*initial_attempts, *retry_attempts]
    final_failure = (
        "" if retried.get("status") == "completed" else str(retried.get("failure_type") or "provider_failed")
    )
    retried["failure_repair"] = {
        "status": "recovered" if retried.get("status") == "completed" else "exhausted",
        "initial_failure_type": failure_type,
        "final_failure_type": final_failure,
        "retry_count": 1,
        "policy": decision["policy"],
        "action": decision["action"],
        "evidence_reference_count": evidence["reference_count"],
    }
    return retried
