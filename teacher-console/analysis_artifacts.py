#!/usr/bin/env python3
"""Structured analysis contract and deterministic answer artifact materialization."""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ANALYSIS_CONTRACT = "wuli.analysis.v1"
EXPLANATION_PATH = "assets/explanatory.svg"
METADATA_FIELDS = ("knowledge_points", "error_types", "difficulty", "grade", "title")
LIST_METADATA_FIELDS = {"knowledge_points", "error_types"}
REQUIRED_STUDENT_HEADINGS = ("答案速览", "详细解答", "易错点")

ANALYSIS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "student_solution": {"type": "string"},
        "teacher_audit": {"type": "string"},
        "metadata": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "knowledge_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 12,
                },
                "error_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 12,
                },
                "difficulty": {"type": "string"},
                "grade": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": list(METADATA_FIELDS),
        },
        "diagram": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "nodes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 6,
                },
            },
            "required": ["title", "nodes"],
        },
    },
    "required": ["status", "message"],
    "allOf": [
        {
            "if": {"properties": {"status": {"const": "completed"}}},
            "then": {
                "required": [
                    "student_solution",
                    "teacher_audit",
                    "metadata",
                    "diagram",
                ]
            },
        }
    ],
}


def output_contract() -> dict[str, Any]:
    return {
        "name": ANALYSIS_CONTRACT,
        "schema": ANALYSIS_OUTPUT_SCHEMA,
        "instructions": (
            "只输出符合 JSON Schema 的对象。student_solution 是完整学生版 Markdown；"
            "不要重复题目原图，不要写教师审计，不要引用尚不存在的图片。"
            "teacher_audit 只写教师复核内容，不要复制学生版。"
            "diagram.nodes 用 2–6 个短语概括解题逻辑链，程序会确定性生成 SVG。"
        ),
    }


def _clean_text(value: Any, *, field: str, minimum: int = 1, maximum: int = 80_000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if len(text) < minimum:
        raise ValueError(f"{field} is too short")
    if len(text) > maximum:
        raise ValueError(f"{field} is too long")
    return text


def _clean_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    cleaned: list[str] = []
    for item in value:
        text = _clean_text(item, field=field, maximum=120)
        if text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        raise ValueError(f"{field} must not be empty")
    return cleaned[:12]


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("structured analysis output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = _clean_text(payload.get("message", ""), field="message", maximum=1000)
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("status must be completed or unsupported")

    student = _clean_text(payload.get("student_solution"), field="student_solution", minimum=100)
    missing = [heading for heading in REQUIRED_STUDENT_HEADINGS if heading not in student]
    if missing:
        raise ValueError("student_solution missing heading: " + ", ".join(missing))
    audit = _clean_text(payload.get("teacher_audit"), field="teacher_audit", minimum=30)

    raw_metadata = payload.get("metadata")
    if not isinstance(raw_metadata, dict):
        raise ValueError("metadata must be an object")
    metadata: dict[str, Any] = {}
    for field in METADATA_FIELDS:
        if field in LIST_METADATA_FIELDS:
            metadata[field] = _clean_list(raw_metadata.get(field), field=f"metadata.{field}")
        else:
            metadata[field] = _clean_text(
                raw_metadata.get(field),
                field=f"metadata.{field}",
                maximum=120,
            )

    raw_diagram = payload.get("diagram")
    if not isinstance(raw_diagram, dict):
        raw_diagram = {}
    diagram_title = str(raw_diagram.get("title", "")).strip() or "解题逻辑"
    try:
        diagram_nodes = _clean_list(raw_diagram.get("nodes"), field="diagram.nodes")[:6]
    except ValueError:
        diagram_nodes = ["识别题型", "建立关键关系", "计算并检查", "得到结论"]
    if len(diagram_nodes) < 2:
        diagram_nodes = ["识别题型", "建立关系", "得到结论"]

    return {
        "status": status,
        "message": message,
        "student_solution": student,
        "teacher_audit": audit,
        "metadata": metadata,
        "diagram": {"title": diagram_title[:120], "nodes": diagram_nodes},
    }


def _ensure_heading(text: str, heading: str) -> str:
    lines = text.strip().splitlines()
    if not lines or not lines[0].lstrip().startswith("#"):
        lines.insert(0, heading)
    else:
        lines[0] = heading
    return "\n".join(lines).strip()


def _insert_explanation_reference(student: str) -> str:
    reference = f"![关键关系示意图]({EXPLANATION_PATH})"
    if reference in student:
        return student
    lines = student.splitlines()
    insertion = 1 if lines and lines[0].lstrip().startswith("#") else 0
    lines[insertion:insertion] = ["", reference, ""]
    return "\n".join(lines).strip()


def _render_diagram(diagram: dict[str, Any]) -> str:
    nodes = [str(item)[:80] for item in diagram["nodes"]]
    width = 960
    margin = 36
    gap = 24
    node_width = max(110, (width - 2 * margin - gap * (len(nodes) - 1)) // len(nodes))
    title = html.escape(str(diagram["title"]))
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="170" viewBox="0 0 960 170">',
        '<rect width="960" height="170" rx="18" fill="#f8fafc"/>',
        f'<text x="480" y="30" text-anchor="middle" font-size="20" font-family="sans-serif" '
        f'font-weight="700" fill="#0f172a">{title}</text>',
        '<defs><marker id="arrow" markerWidth="9" markerHeight="7" refX="8" refY="3.5" '
        'orient="auto"><path d="M0,0 L9,3.5 L0,7 Z" fill="#64748b"/></marker></defs>',
    ]
    y = 58
    for index, label in enumerate(nodes):
        x = margin + index * (node_width + gap)
        parts.append(
            f'<rect x="{x}" y="{y}" width="{node_width}" height="72" rx="12" '
            'fill="#e0f2fe" stroke="#0284c7" stroke-width="2"/>'
        )
        words = re.findall(r".{1,12}", label)[:3]
        first_y = 88 - 10 * (len(words) - 1)
        for offset, word in enumerate(words):
            parts.append(
                f'<text x="{x + node_width / 2:.1f}" y="{first_y + offset * 21}" '
                'text-anchor="middle" font-size="15" font-family="sans-serif" '
                f'fill="#0f172a">{html.escape(word)}</text>'
            )
        if index < len(nodes) - 1:
            x1 = x + node_width + 4
            x2 = x + node_width + gap - 5
            parts.append(
                f'<line x1="{x1}" y1="94" x2="{x2}" y2="94" stroke="#64748b" '
                'stroke-width="2" marker-end="url(#arrow)"/>'
            )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def materialize(staging: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one structured result and deterministically write answer artifacts."""
    normalized = normalize_payload(payload)
    if normalized["status"] != "completed":
        raise ValueError(f"provider reported unsupported: {normalized['message']}")

    record_path = staging / "record.json"
    if not record_path.is_file():
        raise ValueError("record.json is missing")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("record.json must be an object")
    for field, value in normalized["metadata"].items():
        record[field] = value

    student = _insert_explanation_reference(
        _ensure_heading(normalized["student_solution"], "# 解析（学生版）")
    )
    audit = normalized["teacher_audit"]
    if audit.startswith("#"):
        audit = re.sub(r"^#+\s*", "", audit, count=1).strip()
    teacher = f"{student}\n\n## 教师审计\n\n{audit}\n"
    diagram = _render_diagram(normalized["diagram"])

    artifacts = {
        "record.json": json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "student-solution.md": student + "\n",
        "teacher-solution.md": teacher,
        "solution.md": teacher,
        EXPLANATION_PATH: diagram,
    }
    for relative, content in artifacts.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.analysis-materialize")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    digest = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "contract": ANALYSIS_CONTRACT,
        "payload_digest": digest,
        "stages": [
            {"name": "answer-materialization", "status": "completed"},
            {"name": "diagram-materialization", "status": "completed"},
        ],
    }


def stage_records(gateway: dict[str, Any]) -> list[dict[str, Any]]:
    """Build compact persisted stage telemetry without raw model output."""
    attempts = gateway.get("attempts")
    attempts = attempts if isinstance(attempts, list) else []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    resumed = gateway.get("resumed_from_checkpoint") is True
    generated = gateway.get("status") == "completed" or isinstance(gateway.get("materialization"), dict)
    generation: dict[str, Any] = {
        "name": "structured-generation",
        "status": "reused" if resumed else ("completed" if generated else "failed"),
    }
    for source, target in (
        ("provider", "provider"),
        ("duration_seconds", "duration_seconds"),
        ("failure_type", "failure_type"),
        ("token_usage", "usage"),
    ):
        value = last.get(source)
        if value not in (None, "", [], {}):
            generation[target] = value
    materialization = gateway.get("materialization")
    materialized_stages = (
        materialization.get("stages", [])
        if isinstance(materialization, dict) and isinstance(materialization.get("stages"), list)
        else []
    )
    return [generation, *materialized_stages]


def input_fingerprint(
    entry: Path,
    *,
    instruction: str,
    model_id: str,
    routing_tier: str,
) -> str:
    """Fingerprint inputs that make a generated analysis safe to replay."""
    digest = hashlib.sha256()
    for name in ("problem.md", "record.json"):
        path = entry / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"")
        digest.update(b"\0")
    for value in (instruction, model_id, routing_tier, ANALYSIS_CONTRACT):
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def checkpoint_path(entry: Path) -> Path:
    return entry.parent.parent / ".cache" / "analysis-checkpoints" / f"{entry.name}.json"


def save_generation_checkpoint(
    entry: Path,
    *,
    fingerprint: str,
    payload: dict[str, Any],
) -> Path:
    """Persist a valid provider response before deterministic materialization."""
    normalized = normalize_payload(payload)
    if normalized["status"] != "completed":
        raise ValueError("unsupported output cannot become a replay checkpoint")
    checkpoint = {
        "schema_version": 1,
        "contract": ANALYSIS_CONTRACT,
        "entry_id": entry.name,
        "input_fingerprint": fingerprint,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "stage": "structured-generation",
        "payload": normalized,
    }
    encoded = json.dumps(checkpoint, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if len(encoded) > 400_000:
        raise ValueError("analysis checkpoint exceeds 400000 characters")
    path = checkpoint_path(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    return path


def load_generation_checkpoint(entry: Path, *, fingerprint: str) -> dict[str, Any] | None:
    path = checkpoint_path(entry)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(checkpoint, dict):
        return None
    if checkpoint.get("contract") != ANALYSIS_CONTRACT:
        return None
    if checkpoint.get("entry_id") != entry.name:
        return None
    if checkpoint.get("input_fingerprint") != fingerprint:
        return None
    payload = checkpoint.get("payload")
    if not isinstance(payload, dict):
        return None
    try:
        normalized = normalize_payload(payload)
    except ValueError:
        return None
    return normalized if normalized["status"] == "completed" else None


def clear_generation_checkpoint(entry: Path) -> None:
    checkpoint_path(entry).unlink(missing_ok=True)
