#!/usr/bin/env python3
"""Structured analysis contract and deterministic answer artifact materialization."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import diagram_plugins
import teaching_method_policy

ANALYSIS_CONTRACT = "wuli.analysis.v2"
EXPLANATION_PATH = "assets/explanatory.svg"
METADATA_FIELDS = ("knowledge_points", "error_types", "difficulty", "grade", "title")
LIST_METADATA_FIELDS = {"knowledge_points", "error_types"}
REQUIRED_STUDENT_HEADINGS = ("答案速览", "一眼识别", "详细解答", "易错点", "30 秒自测")
STUDENT_STEP_PATTERN = re.compile(r"^###\s+第\s*[一二三四五六七八九十0-9]+\s*步", re.MULTILINE)
ADVANCED_STUDENT_METHODS = (
    (re.compile(r"\\int\b|积分"), "积分"),
    (re.compile(r"导数|求导|微分(?:方程)?"), "导数或微分"),
    (re.compile(r"矩阵|行列式"), "矩阵或行列式"),
    (re.compile(r"复数法|欧拉公式"), "复数法"),
    (re.compile(r"拉格朗日|哈密顿"), "大学力学方法"),
)

ANALYSIS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "student_solution": {"type": ["string", "null"]},
        "teacher_audit": {"type": ["string", "null"]},
        "method_check": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "selected_path": {"type": "string"},
                "high_school_basis": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 8,
                },
                "discarded_methods": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "physical_stages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 12,
                },
                "reasoning_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 5,
                },
                "decisive_relations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 12,
                },
                "representation_transforms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "condition_checks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 12,
                },
                "type_distance": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "mode": {
                            "enum": [
                                "direct_archetype",
                                "routine_variant",
                                "standard_transfer",
                                "model_reconstruction",
                                "non_obvious_bridge",
                                "novel_construction",
                            ]
                        },
                        "archetype": {"type": "string"},
                        "recognition_barrier": {"type": "string"},
                        "novel_bridge": {"type": "string"},
                    },
                    "required": ["mode", "archetype", "recognition_barrier", "novel_bridge"],
                },
                "student_step_count": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": [
                "selected_path",
                "high_school_basis",
                "discarded_methods",
                "physical_stages",
                "reasoning_steps",
                "decisive_relations",
                "representation_transforms",
                "condition_checks",
                "type_distance",
                "student_step_count",
            ],
        },
        "metadata": {
            "type": ["object", "null"],
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
            "type": ["object", "null"],
            "additionalProperties": False,
            # Deprecated transport slot retained for old adapters.  Analysis no
            # longer designs images; the separate physics-diagram scene task owns
            # that work and callers must send null here.
            "properties": {},
        },
    },
    # Codex/OpenAI structured outputs reject conditional ``allOf`` schemas and
    # require every declared property. Nullable branch fields keep
    # ``unsupported`` compact; normalize_payload() enforces non-null completed
    # content deterministically after transport validation.
    "required": [
        "status",
        "message",
        "student_solution",
        "teacher_audit",
        "method_check",
        "metadata",
        "diagram",
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
            "先比较可行路径并填写 method_check：selected_path 写最终最短主线，"
            "high_school_basis 列出所用高中结论，discarded_methods 记录已舍弃的冗长或超纲方法，"
            "physical_stages 列出题目客观存在的物理阶段，reasoning_steps 列出最短高中主线的"
            "必要推理步骤，decisive_relations 列出可复算的决定性关系，"
            "representation_transforms 列出题干到图像、几何、等效模型等必要表征转换，"
            "condition_checks 列出分类、临界、首次、唯一、边界或完备性检查。"
            "type_distance 评估学生在未见答案时识别最短高中母题的距离：direct_archetype 为教材母题，"
            "routine_variant 为常规变式，standard_transfer 为标准迁移，model_reconstruction 为模型重构，"
            "non_obvious_bridge 为存在隐蔽桥梁，novel_construction 为需要非常规构造；只选枚举，不填写分数，"
            "并用 archetype、recognition_barrier、novel_bridge 给出简短可复核依据。"
            "reasoning_steps 数量、student_step_count 与学生版“第 N 步”标题数必须一致且不超过 5；"
            "high_school_basis 只列物理定律或高中结论，不混入量纲检查等校验操作。"
            "学生版优先几何、守恒、图像面积、平均值和标准二级结论；禁止使用积分、导数、"
            "微分方程、矩阵、复数法或大学力学方法。能一式完成的关系不要拆成多步代数。"
            "证据优先于层级模板：详细解答必须占主体篇幅；每个小问和每个选项判断至少保留"
            "一条可核验的等式、几何关系或事件链。不得用“并不对应”“显然可得”“角度账本可得”"
            "代替关键推导；若删去某行会使结论只能靠猜，该行必须保留。"
            "一眼识别最多三条短句，易错点最多三条，30 秒自测只写一个问题；这些辅助区不得"
            "重复详细解答或挤占关键证明。"
            "公式只使用网页可稳定渲染的 LaTeX；不要使用 \\notag、\\tag 或编号控制命令，"
            "并在提交前检查是否出现 otag、aqquad、gqquad、rac{…}、rac34 等反斜杠丢失残片。"
            "diagram 是兼容旧适配器的废弃字段，必须设为 null；本任务不设计图片。"
            "物理示意图由独立的强类型场景任务依据已完成答案和 MiMo 视觉事实生成。"
            "若上下文包含 physics-model.json，它是已建模的物理事件真源：答案选项、"
            "事件分支和返回后的后续运动必须与其一致，且不得用逻辑流程图覆盖已有物理示意图。"
            "status=unsupported 时将 student_solution、teacher_audit、method_check、metadata、diagram 设为 null。"
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


def _clean_list(value: Any, *, field: str, maximum: int = 120) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    cleaned: list[str] = []
    for item in value:
        text = _clean_text(item, field=field, maximum=maximum)
        if text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        raise ValueError(f"{field} must not be empty")
    return cleaned[:12]


def _clean_optional_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    cleaned: list[str] = []
    for item in value:
        text = _clean_text(item, field=field, maximum=160)
        if text not in cleaned:
            cleaned.append(text)
    return cleaned[:8]


def _repair_latex_fragments(text: str) -> str:
    """Repair unambiguous JSON/Markdown escape remnants without changing physics."""
    # CLI providers may emit terminal colour resets inside an otherwise valid
    # JSON string. They are never meaningful teaching content.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\\(?:dfrac|tfrac)(?![A-Za-z])", r"\\frac", text)
    # A single JSON backslash turns ``\frac`` into form-feed + ``rac`` and
    # ``\tfrac`` into tab + ``frac``. Replace the complete damaged token so no
    # hidden control character survives the visible LaTeX repair.
    text = re.sub(r"\x0crac(?=[{\d])", r"\\frac", text)
    text = re.sub(r"\tfrac(?=[{\d])", r"\\frac", text)
    # Models occasionally lose the leading backslash and prepend one stray
    # letter (for example ``aqquad`` or ``gqquad``).  No valid prose token in
    # these Chinese solutions uses a bare word ending in ``qquad``.
    text = re.sub(r"(?<!\\)\b[a-z]*qquad\b", r"\\qquad", text)
    # A provider that emits ``\frac`` with only one JSON escape can have ``\f``
    # decoded as a form-feed and later stripped, leaving the unmistakable
    # fragments such as ``rac{...}`` or the valid compact-LaTeX form's damaged
    # counterpart ``rac34`` (originally ``\frac34``).
    text = re.sub(r"(?<![\\A-Za-z])rac(?=[{\d])", r"\\frac", text)
    return re.sub(r"(?m)^[ \t]*otag[ \t]*\n?", "", text)


def student_method_errors(
    student: str,
    method_profile: str = teaching_method_policy.DEFAULT_PROFILE,
) -> list[str]:
    """Return deterministic student-layer method and cognitive-load violations."""
    errors: list[str] = []
    if "最短主线" not in student:
        errors.append("student_solution must state 最短主线 in 一眼识别")
    step_count = len(STUDENT_STEP_PATTERN.findall(student))
    if step_count < 1:
        errors.append("student_solution must use numbered 第 N 步 headings")
    elif step_count > 5:
        errors.append(f"student_solution main line has {step_count} steps; maximum is 5")
    errors.extend(teaching_method_policy.method_errors(student, method_profile))
    if re.search(r"(?m)^\s*otag\s*$", student):
        errors.append("student_solution contains broken LaTeX fragment: otag")
    if re.search(r"(?<!\\)\b[a-z]*qquad\b", student):
        errors.append("student_solution contains broken LaTeX fragment: bare qquad")
    if re.search(r"(?<![\\A-Za-z])rac(?=[{\d])", student):
        errors.append("student_solution contains broken LaTeX fragment: bare frac")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", student):
        errors.append("student_solution contains unsupported control characters")
    return errors


def _option_verdicts(text: str) -> dict[str, bool]:
    """Extract explicit A-H verdicts from compact Chinese answer summaries."""
    verdicts: dict[str, bool] = {}
    for segment in re.split(r"[；;\n]", text.upper()):
        match = re.search(
            r"([A-H](?:\s*[、,，]\s*[A-H])*)[^；;\n]{0,32}?(正确|错误|对|错)",
            segment,
        )
        if not match:
            continue
        labels = re.findall(r"[A-H]", match.group(1))
        positive = match.group(2) in {"正确", "对"}
        verdicts.update({label: positive for label in labels})
    return verdicts


def physics_model_consistency_errors(staging: Path, student: str) -> list[str]:
    """Keep a regenerated answer aligned with an existing reviewed physics model."""
    model_path = staging / "physics-model.json"
    if not model_path.is_file():
        return []
    try:
        model = json.loads(model_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["physics-model.json is unreadable"]
    quick_answers = model.get("student_solution", {}).get("quick_answers", [])
    if not isinstance(quick_answers, list):
        return []
    expected = _option_verdicts("；".join(str(item) for item in quick_answers))
    actual = _option_verdicts(student[:2000])
    if expected and all(label in actual for label in expected) and actual != expected:
        differences = [label for label, verdict in expected.items() if actual.get(label) is not verdict]
        return ["student_solution contradicts physics-model option verdicts: " + ", ".join(differences)]
    return []


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("structured analysis output must be an object")
    status = str(payload.get("status", "")).strip().lower()
    message = _clean_text(payload.get("message", ""), field="message", maximum=1000)
    if status == "unsupported":
        return {"status": status, "message": message}
    if status != "completed":
        raise ValueError("status must be completed or unsupported")

    student = _repair_latex_fragments(
        _clean_text(payload.get("student_solution"), field="student_solution", minimum=100)
    )
    missing = [heading for heading in REQUIRED_STUDENT_HEADINGS if heading not in student]
    if missing:
        raise ValueError("student_solution missing heading: " + ", ".join(missing))
    method_errors = student_method_errors(student)
    if method_errors:
        raise ValueError("; ".join(method_errors))
    audit = _repair_latex_fragments(_clean_text(payload.get("teacher_audit"), field="teacher_audit", minimum=30))

    raw_method_check = payload.get("method_check")
    if not isinstance(raw_method_check, dict):
        raise ValueError("method_check must be an object")
    selected_path = _clean_text(
        raw_method_check.get("selected_path"),
        field="method_check.selected_path",
        maximum=500,
    )
    high_school_basis = _clean_list(
        raw_method_check.get("high_school_basis"),
        field="method_check.high_school_basis",
    )[:8]
    discarded_methods = _clean_optional_list(
        raw_method_check.get("discarded_methods"),
        field="method_check.discarded_methods",
    )
    physical_stages = _clean_list(
        raw_method_check.get("physical_stages"),
        field="method_check.physical_stages",
        maximum=240,
    )[:12]
    reasoning_steps = _clean_list(
        raw_method_check.get("reasoning_steps"),
        field="method_check.reasoning_steps",
        maximum=240,
    )[:5]
    decisive_relations = _clean_list(
        raw_method_check.get("decisive_relations"),
        field="method_check.decisive_relations",
        maximum=360,
    )[:12]
    representation_transforms = _clean_optional_list(
        raw_method_check.get("representation_transforms"),
        field="method_check.representation_transforms",
    )
    condition_checks = _clean_optional_list(
        raw_method_check.get("condition_checks"),
        field="method_check.condition_checks",
    )[:12]
    raw_type_distance = raw_method_check.get("type_distance")
    if not isinstance(raw_type_distance, dict):
        raise ValueError("method_check.type_distance must be an object")
    type_distance_mode = str(raw_type_distance.get("mode", "")).strip()
    allowed_type_distance_modes = {
        "direct_archetype",
        "routine_variant",
        "standard_transfer",
        "model_reconstruction",
        "non_obvious_bridge",
        "novel_construction",
    }
    if type_distance_mode not in allowed_type_distance_modes:
        raise ValueError("method_check.type_distance.mode is invalid")
    type_distance = {
        "mode": type_distance_mode,
        "archetype": _clean_text(
            raw_type_distance.get("archetype"),
            field="method_check.type_distance.archetype",
            maximum=160,
        ),
        "recognition_barrier": _clean_text(
            raw_type_distance.get("recognition_barrier"),
            field="method_check.type_distance.recognition_barrier",
            maximum=240,
        ),
        "novel_bridge": str(raw_type_distance.get("novel_bridge", "")).strip()[:240],
    }
    reported_step_count = raw_method_check.get("student_step_count")
    actual_step_count = len(STUDENT_STEP_PATTERN.findall(student))
    if isinstance(reported_step_count, bool) or not isinstance(reported_step_count, int):
        raise ValueError("method_check.student_step_count must be an integer")
    if reported_step_count != actual_step_count:
        raise ValueError(
            "method_check.student_step_count does not match student_solution: "
            f"{reported_step_count} != {actual_step_count}"
        )
    if len(reasoning_steps) != reported_step_count:
        raise ValueError(
            "method_check.reasoning_steps does not match student_step_count: "
            f"{len(reasoning_steps)} != {reported_step_count}"
        )

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

    if payload.get("diagram") is not None:
        raise ValueError("diagram is deprecated and must be null")

    return {
        "status": status,
        "message": message,
        "student_solution": student,
        "teacher_audit": audit,
        "method_check": {
            "selected_path": selected_path,
            "high_school_basis": high_school_basis,
            "discarded_methods": discarded_methods,
            "physical_stages": physical_stages,
            "reasoning_steps": reasoning_steps,
            "decisive_relations": decisive_relations,
            "representation_transforms": representation_transforms,
            "condition_checks": condition_checks,
            "type_distance": type_distance,
            "student_step_count": reported_step_count,
        },
        "metadata": metadata,
        "diagram": None,
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


def render_explanation_diagram(title: str, nodes: list[str], *, plugin_id: str) -> str:
    """Compatibility entry point for an explicitly selected optional plugin."""
    return diagram_plugins.render_optional_diagram(
        plugin_id,
        {"title": title, "nodes": nodes},
    )


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
    record["standard_solution_path"] = {
        "schema_version": 1,
        "source": ANALYSIS_CONTRACT,
        **normalized["method_check"],
    }

    student = _insert_explanation_reference(_ensure_heading(normalized["student_solution"], "# 解析（学生版）"))
    consistency_errors = physics_model_consistency_errors(staging, student)
    if consistency_errors:
        raise ValueError("; ".join(consistency_errors))
    audit = normalized["teacher_audit"]
    if audit.startswith("#"):
        audit = re.sub(r"^#+\s*", "", audit, count=1).strip()
    teacher = f"{student}\n\n## 教师审计\n\n{audit}\n"
    artifacts = {
        "record.json": json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "student-solution.md": student + "\n",
        "teacher-solution.md": teacher,
        "solution.md": teacher,
    }
    for relative, content in artifacts.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.analysis-materialize")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
    digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "contract": ANALYSIS_CONTRACT,
        "payload_digest": digest,
        "stages": [
            {"name": "answer-materialization", "status": "completed"},
            {"name": "diagram-materialization", "status": "not-owned"},
        ],
    }


def stage_records(gateway: dict[str, Any]) -> list[dict[str, Any]]:
    """Build compact persisted stage telemetry without raw model output."""
    attempts = gateway.get("attempts")
    attempts = attempts if isinstance(attempts, list) else []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    resumed = gateway.get("resumed_from_checkpoint") is True
    materialization = gateway.get("materialization")
    generated = gateway.get("status") == "completed" or isinstance(materialization, dict)
    # Work-tree A3: the provider can succeed while the local materializer or a
    # domain gate rejects the payload. That failure belongs to the
    # materialization stages, not to structured-generation.
    materializer_failed = not generated and bool(last.get("materializer_error"))
    generation: dict[str, Any] = {
        "name": "structured-generation",
        "status": "reused" if resumed else ("completed" if (generated or materializer_failed) else "failed"),
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
    materialized_stages = (
        materialization.get("stages", [])
        if isinstance(materialization, dict) and isinstance(materialization.get("stages"), list)
        else []
    )
    if materialized_stages or not materializer_failed:
        return [generation, *materialized_stages]
    gate_rejected = "physics quality gate rejected" in str(last.get("error", ""))
    return [
        generation,
        {"name": "core-materialization", "status": "rejected"},
        {"name": "physics-quality-gate", "status": "failed" if gate_rejected else "not-run"},
        {"name": "canonical-promotion", "status": "not-run"},
    ]


def input_fingerprint(
    entry: Path,
    *,
    instruction: str,
    model_id: str,
    routing_tier: str,
    evidence_digest: str = "",
) -> str:
    """Fingerprint inputs that make a generated analysis safe to replay."""
    digest = hashlib.sha256()
    for name in ("problem.md", "record.json", "physics-model.json"):
        path = entry / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"")
        digest.update(b"\0")
    for value in (instruction, model_id, routing_tier, ANALYSIS_CONTRACT, evidence_digest):
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
