#!/usr/bin/env python3
"""Generate deterministic evaluation reports for one wrong-question entry.

This is the first thin slice of Wuli's Memory-Evolve layer.  It does not
approve, publish, or call a model; it only reads lifecycle artifacts and writes
an auditable ``evaluation.json`` describing what passed, what needs teacher
review, and which evidence files support that judgment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import kb

SCORE_MAX = 5
SCORE_MIN = 0
EVALUATION_SCHEMA_VERSION = 1


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _status_rank(status: str) -> int:
    return {"passed": 0, "skipped": 1, "warning": 2, "failed": 3}.get(status, 2)


def _overall_status(checks: list[dict[str, Any]]) -> str:
    worst = max((_status_rank(check.get("status", "warning")) for check in checks), default=0)
    if worst >= _status_rank("failed"):
        return "failed"
    if worst >= _status_rank("warning"):
        return "warning"
    return "passed"


def _check(
    checks: list[dict[str, Any]],
    check_id: str,
    label: str,
    status: str,
    *,
    evidence: list[str] | None = None,
    details: str = "",
) -> None:
    checks.append({
        "id": check_id,
        "label": label,
        "status": status,
        "evidence": evidence or [],
        "details": details,
    })


def _score_from_checks(checks: list[dict[str, Any]], check_ids: set[str]) -> int:
    selected = [item for item in checks if item["id"] in check_ids]
    if not selected:
        return SCORE_MIN
    if any(item["status"] == "failed" for item in selected):
        return 1
    warnings = sum(1 for item in selected if item["status"] == "warning")
    skipped = sum(1 for item in selected if item["status"] == "skipped")
    score = SCORE_MAX - warnings - max(0, skipped - 1)
    return max(SCORE_MIN, min(SCORE_MAX, score))


def _visualization_digest(entry: Path) -> str:
    digest = hashlib.sha256()
    paths: list[Path] = []
    model = entry / "physics-model.json"
    if model.exists():
        paths.append(model)
    visual_dir = entry / "visualization"
    for name in ("physics-simulator.html", "physics-simulator.zip", "runtime-check.png", "simulation-build.json"):
        path = visual_dir / name
        if path.exists():
            paths.append(path)
    for path in paths:
        digest.update(_relative(path, entry).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _delivery_manifest(entry: Path, output_dir: Path | None) -> tuple[dict[str, Any], Path | None]:
    delivery = kb.load_json(entry / "delivery.json", {}) or {}
    manifest_path: Path | None = None
    if output_dir:
        candidate = output_dir / "delivery-manifest.json"
        if candidate.exists():
            manifest_path = candidate
    if manifest_path is None and delivery.get("output"):
        candidate = Path(delivery["output"]) / "delivery-manifest.json"
        if candidate.exists():
            manifest_path = candidate
    if manifest_path:
        return kb.load_json(manifest_path, {}) or {}, manifest_path
    return delivery, None


def evaluate_entry(root: Path, entry_id: str, output_dir: Path | None = None, *, write: bool = True) -> dict[str, Any]:
    """Evaluate one entry and optionally persist ``evaluation.json``.

    The evaluator is intentionally deterministic and conservative.  Teacher
    approval remains the authority for content correctness; this report only
    records whether the current artifacts satisfy known lifecycle gates.
    """
    entry = root / "entries" / entry_id
    if not entry.exists():
        report = {
            "schema_version": EVALUATION_SCHEMA_VERSION,
            "entry_id": entry_id,
            "status": "failed",
            "generated_at": kb.now_iso(),
            "failure_reasons": [f"entry not found: {entry_id}"],
            "teacher_review_required": True,
            "checks": [],
            "scores": {},
            "evidence_sources": [],
        }
        return report

    checks: list[dict[str, Any]] = []
    evidence_sources: list[dict[str, str]] = []
    record = kb.load_json(entry / "record.json", {}) or {}
    problem = _read_text(entry / "problem.md")
    solution = _read_text(entry / "solution.md")
    student_solution = _read_text(entry / "student-solution.md")
    teacher_solution = _read_text(entry / "teacher-solution.md")
    manifest, manifest_path = _delivery_manifest(entry, output_dir)
    output_path = Path(manifest.get("output", output_dir or "")) if manifest.get("output") or output_dir else None

    for name in ("record.json", "problem.md", "solution.md", "student-solution.md", "teacher-solution.md"):
        path = entry / name
        if path.exists():
            evidence_sources.append({"kind": "entry", "path": _relative(path, root)})
    if (entry / "answer-review.json").exists():
        evidence_sources.append({"kind": "review", "path": _relative(entry / "answer-review.json", root)})
    if (entry / "visualization-review.json").exists():
        evidence_sources.append({"kind": "review", "path": _relative(entry / "visualization-review.json", root)})
    if manifest_path:
        evidence_sources.append({"kind": "delivery", "path": _relative(manifest_path, root.parent)})

    structural_errors = kb.validate_entry(root, entry, ready_rules=True, require_answer_review=False)
    structural_status = "passed" if not structural_errors else "failed"
    _check(
        checks,
        "entry_structure",
        "题目、答案、图片引用与记录结构",
        structural_status,
        evidence=[
            _relative(entry / "record.json", root),
            _relative(entry / "problem.md", root),
            _relative(entry / "solution.md", root),
        ],
        details="；".join(structural_errors),
    )

    source_review = record.get("source_review", {})
    source_status = (
        "passed"
        if source_review.get("status") == "passed" or not record.get("ocr", {}).get("review_required")
        else "failed"
    )
    _check(
        checks,
        "source_review",
        "原图/题干来源复核",
        source_status,
        evidence=[_relative(entry / "record.json", root)],
        details=f"source_review.status={source_review.get('status', 'legacy-not-recorded')}; ocr.review_required={record.get('ocr', {}).get('review_required')}",
    )

    pending_markers = [marker for marker in kb.PENDING_MARKERS if marker in problem or marker in solution]
    _check(
        checks,
        "pending_markers",
        "未解决占位符",
        "failed" if pending_markers else "passed",
        evidence=[_relative(entry / "problem.md", root), _relative(entry / "solution.md", root)],
        details="，".join(pending_markers),
    )

    answer_review = kb.load_json(entry / "answer-review.json", record.get("answer_review", {})) or {}
    answer_digest = kb.answer_artifact_digest(entry)
    if answer_review.get("status") != "passed":
        answer_status = "failed"
        answer_details = f"answer_review.status={answer_review.get('status', 'not-ready')}"
    elif answer_review.get("answer_digest") != answer_digest:
        answer_status = "failed"
        answer_details = "答案、题干、模型或引用图像在教师批准后发生变化"
    else:
        answer_status = "passed"
        answer_details = "当前答案摘要与教师批准一致"
    _check(
        checks,
        "answer_review_current",
        "答案复核仍对应当前版本",
        answer_status,
        evidence=[_relative(entry / "answer-review.json", root)],
        details=answer_details,
    )

    required_headings = [heading for heading in kb.REQUIRED_SOLUTION_HEADINGS if heading in solution]
    answer_layers_status = "passed" if len(required_headings) == len(kb.REQUIRED_SOLUTION_HEADINGS) else "failed"
    _check(
        checks,
        "layered_answer",
        "学生可读分层答案",
        answer_layers_status,
        evidence=[
            _relative(entry / "solution.md", root),
            _relative(entry / "student-solution.md", root),
            _relative(entry / "teacher-solution.md", root),
        ],
        details=f"已包含栏目：{', '.join(required_headings)}",
    )

    total_answer_chars = len((student_solution or solution).strip())
    cognitive_status = "passed"
    cognitive_details = f"学生版答案约 {total_answer_chars} 字；启发式检查，仅作教师复核提示"
    if total_answer_chars < 100:
        cognitive_status = "failed"
        cognitive_details += "；答案过短，可能缺少步骤"
    elif total_answer_chars > 6000:
        cognitive_status = "warning"
        cognitive_details += "；答案较长，建议检查认知负担"
    _check(
        checks,
        "student_cognitive_load_hint",
        "学生认知负担启发式提示",
        cognitive_status,
        evidence=[_relative(entry / "student-solution.md", root)],
        details=cognitive_details,
    )

    model_path = entry / "physics-model.json"
    if model_path.exists():
        visual_build = (
            kb.load_json(entry / "visualization" / "simulation-build.json", record.get("visualization_build", {})) or {}
        )
        visual_review = kb.load_json(entry / "visualization-review.json", record.get("visualization_review", {})) or {}
        visual_digest = _visualization_digest(entry)
        visual_errors: list[str] = []
        if visual_build.get("status") != "ok":
            visual_errors.append(f"visualization build status={visual_build.get('status', 'missing')}")
        if not (entry / "visualization" / "physics-simulator.html").is_file():
            visual_errors.append("physics-simulator.html missing")
        if visual_review.get("status") != "passed":
            visual_errors.append(f"visualization_review.status={visual_review.get('status', 'not-ready')}")
        elif visual_review.get("artifact_digest") != visual_digest:
            visual_errors.append("可视化产物在教师批准后发生变化")
        runtime_status = (visual_build.get("runtime_check") or {}).get("status")
        if runtime_status == "failed":
            visual_errors.append("runtime_check.status=failed")
        visual_status = "passed" if not visual_errors else "failed"
        _check(
            checks,
            "interactive_visualization",
            "交互可视化构建与复核",
            visual_status,
            evidence=[
                _relative(model_path, root),
                _relative(entry / "visualization" / "simulation-build.json", root),
                _relative(entry / "visualization-review.json", root),
            ],
            details="；".join(visual_errors)
            if visual_errors
            else f"runtime_check.status={runtime_status or 'not-recorded'}",
        )
    else:
        _check(
            checks,
            "interactive_visualization",
            "交互可视化构建与复核",
            "skipped",
            evidence=[],
            details="未生成 physics-model.json；标准解析不强制交互可视化",
        )

    if manifest:
        files = set(manifest.get("files", []))
        missing: list[str] = []
        if manifest.get("status") != "delivered":
            missing.append(f"manifest.status={manifest.get('status', 'missing')}")
        if "带答案错题.md" not in files:
            missing.append("带答案错题.md missing from manifest")
        if "student-package.zip" not in files and not (output_path and (output_path / "student-package.zip").exists()):
            missing.append("student-package.zip missing")
        pdf = manifest.get("pdf", {})
        delivery_status = "passed" if not missing else "failed"
        if delivery_status == "passed" and pdf.get("status") not in {"generated", "copied"}:
            delivery_status = "warning"
            missing.append(f"pdf.status={pdf.get('status', 'skipped')}")
        _check(
            checks,
            "delivery_artifacts",
            "交付 Markdown/PDF/学生包",
            delivery_status,
            evidence=[_relative(manifest_path, root.parent)]
            if manifest_path
            else [_relative(entry / "delivery.json", root)],
            details="；".join(missing) if missing else "交付清单包含必要学生成品",
        )
    else:
        _check(
            checks,
            "delivery_artifacts",
            "交付 Markdown/PDF/学生包",
            "skipped",
            evidence=[],
            details="尚未生成 delivery-manifest.json；可在 finish 后重新评价",
        )

    private_refs = []
    for name, text in (
        ("problem.md", problem),
        ("solution.md", solution),
        ("student-solution.md", student_solution),
        ("teacher-solution.md", teacher_solution),
    ):
        if re.search(r"(/Users/|student-error-library/entries/|error-collection/)", text):
            private_refs.append(name)
    _check(
        checks,
        "local_reference_safety",
        "答案文本中的本地私有路径提示",
        "failed" if private_refs else "passed",
        evidence=[_relative(entry / "problem.md", root), _relative(entry / "solution.md", root)],
        details="发现疑似私有引用：" + "，".join(private_refs) if private_refs else "未发现常见私有路径模式",
    )

    status = _overall_status(checks)
    failure_reasons = [f"{item['label']}：{item['details']}" for item in checks if item["status"] == "failed"]
    warning_reasons = [f"{item['label']}：{item['details']}" for item in checks if item["status"] == "warning"]
    teacher_review_required = status != "passed" or any(item["id"].endswith("_hint") for item in checks)
    scores = {
        "completeness": _score_from_checks(checks, {"entry_structure", "layered_answer", "delivery_artifacts"}),
        "correctness": _score_from_checks(
            checks, {"source_review", "answer_review_current", "interactive_visualization"}
        ),
        "student_cognitive_load": _score_from_checks(checks, {"layered_answer", "student_cognitive_load_hint"}),
        "safety": _score_from_checks(checks, {"source_review", "local_reference_safety", "delivery_artifacts"}),
        "deliverability": _score_from_checks(checks, {"delivery_artifacts", "interactive_visualization"}),
    }
    report = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "entry_id": entry_id,
        "status": status,
        "generated_at": kb.now_iso(),
        "score_scale": {"min": SCORE_MIN, "max": SCORE_MAX},
        "scores": scores,
        "summary": {
            "passed": sum(1 for item in checks if item["status"] == "passed"),
            "warning": sum(1 for item in checks if item["status"] == "warning"),
            "failed": sum(1 for item in checks if item["status"] == "failed"),
            "skipped": sum(1 for item in checks if item["status"] == "skipped"),
        },
        "teacher_review_required": teacher_review_required,
        "failure_reasons": failure_reasons,
        "warning_reasons": warning_reasons,
        "checks": checks,
        "evidence_sources": evidence_sources,
        "artifacts": {
            "entry": str(entry.resolve()),
            "output": str(output_path.resolve()) if output_path else None,
            "delivery_manifest": str(manifest_path.resolve()) if manifest_path else None,
        },
        "notes": [
            "Evaluator 只记录可验证事实与启发式提示，不替代教师复核。",
            "认知负担与物理语义正确性需要教师或后续专门 verifier 复核。",
        ],
    }
    if write:
        kb.write_json(entry / "evaluation.json", report)
        if output_path and output_path.exists():
            kb.write_json(output_path / "evaluation.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    parser.add_argument("entry_id")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    root = args.library.expanduser().resolve()
    report = evaluate_entry(
        root, args.entry_id, args.output.expanduser().resolve() if args.output else None, write=not args.no_write
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
