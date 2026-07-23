#!/usr/bin/env python3
"""Prepare, run, and approve source-image review independently of the reasoning model."""

from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import kb

REVIEW_SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def source_images(entry: Path, record: dict[str, Any]) -> list[Path]:
    images: list[Path] = []
    for relative in record.get("source", {}).get("stored_files", []):
        path = entry / relative
        if path.exists() and path.suffix.lower() in kb.IMAGE_EXTENSIONS and path not in images:
            images.append(path)
    return images


def input_digest(entry: Path, record: dict[str, Any], ocr: dict[str, Any]) -> str:
    payload = {
        "entry_id": entry.name,
        "source_sha256": record.get("source", {}).get("sha256", ""),
        "ocr_text": ocr.get("text", ""),
        "images": [str(path.relative_to(entry)) for path in source_images(entry, record)],
    }
    return kb.sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def review_payload(entry: Path) -> dict[str, Any]:
    record = kb.load_json(entry / "record.json", {})
    ocr = kb.load_json(entry / "ocr.json", {})
    images = source_images(entry, record)
    if not images:
        raise FileNotFoundError("no rendered source image is available for visual review")
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "entry_id": entry.name,
        "subject": record.get("subject", ""),
        "source_sha256": record.get("source", {}).get("sha256", ""),
        "images": [str(path.resolve()) for path in images],
        "ocr": {
            "engine": ocr.get("engine", "unknown"),
            "average_confidence": ocr.get("average_confidence", 0.0),
            "text": ocr.get("text", ""),
        },
        "required_checks": [
            "printed and handwritten text",
            "formula structure, subscripts, superscripts, signs, and units",
            "diagram labels, arrows, regions, charge signs, and field directions",
            "question subparts and stopping/deadline wording",
        ],
    }


def build_problem(entry: Path, reviewed_text: str, diagram_facts: list[str]) -> str:
    record = kb.load_json(entry / "record.json", {})
    refs = [str(path.relative_to(entry)) for path in source_images(entry, record)]
    images = "\n".join(f"![原始题图 {index}]({ref})" for index, ref in enumerate(refs, 1))
    facts = "\n".join(f"- {item}" for item in diagram_facts) or "- 无独立图形信息。"
    return (
        "# 题目（已复核）\n\n"
        f"题目编号：`{entry.name}`\n\n{images}\n\n"
        f"## 复核题干\n\n{reviewed_text.strip()}\n\n"
        f"## 图形事实\n\n{facts}\n"
    )


def prepare_review(entry: Path, reason: str = "visual review required") -> dict[str, Any]:
    payload = review_payload(entry)
    record = kb.load_json(entry / "record.json", {})
    ocr = kb.load_json(entry / "ocr.json", {})
    refs = [str(path.relative_to(entry)) for path in source_images(entry, record)]
    images = "\n".join(f"![待复核原图 {index}]({ref})" for index, ref in enumerate(refs, 1))
    review_path = entry / "source-review.md"
    kb.write_text(
        review_path,
        "# 题目原图复核单\n\n"
        f"题目编号：`{entry.name}`\n\n"
        f"复核原因：{reason}\n\n{images}\n\n"
        "## OCR 草稿\n\n"
        f"{ocr.get('text', '') or '[OCR 未得到文本]'}\n\n"
        "## 必查项目\n\n"
        "- [ ] 题干、选项和所有小问完整；\n"
        "- [ ] 正负号、上下标、指数、根号、分式和单位正确；\n"
        "- [ ] 图中区域、箭头、方向、电性和边界条件正确；\n"
        "- [ ] 已把校对后的正式题干写入 `problem.md`；\n"
        "- [ ] 已明确所有仍无法判断之处。\n\n"
        "确认原图无歧义后，执行 `process_uploads.py --library <library> approve-source <entry-id> --reviewer <姓名或角色>`。\n",
    )
    report = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "entry_id": entry.name,
        "status": "needs-review",
        "method": "human",
        "reason": reason,
        "input_digest": input_digest(entry, record, ocr),
        "review_packet": str(review_path.resolve()),
        "created_at": now_iso(),
        "payload": payload,
    }
    record["source_review"] = {"status": "needs-review", "method": "human", "reason": reason}
    record.setdefault("ocr", {})["review_required"] = True
    record["updated_at"] = now_iso()
    kb.write_json(entry / "record.json", record)
    kb.write_json(entry / "source-review.json", report)
    return report


def parse_adapter_output(raw: str) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"visual review adapter returned invalid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError("visual review adapter output must be a JSON object")
    status = result.get("review_status")
    if status not in {"passed", "needs-review"}:
        raise ValueError("review_status must be passed or needs-review")
    for field in ("diagram_facts", "uncertainties"):
        if not isinstance(result.get(field, []), list) or not all(
            isinstance(item, str) for item in result.get(field, [])
        ):
            raise ValueError(f"{field} must be an array of strings")
    if status == "passed":
        if not str(result.get("reviewed_text", "")).strip():
            raise ValueError("passed review requires non-empty reviewed_text")
        if result.get("uncertainties"):
            raise ValueError("passed review cannot contain uncertainties")
    return result


def run_adapter(entry: Path, command_text: str, locality: str, allow_remote: bool) -> dict[str, Any]:
    if locality not in {"local", "remote"}:
        raise ValueError("adapter locality must be local or remote")
    if locality == "remote" and not allow_remote:
        raise PermissionError("remote visual review is disabled; explicit authorization is required")
    command = shlex.split(command_text)
    if not command:
        raise ValueError("visual review command is empty")
    payload = review_payload(entry)
    replacements = {"{entry}": str(entry.resolve()), "{problem}": str((entry / "problem.md").resolve())}
    command = [replacements.get(token, token) for token in command]
    completed = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "visual review adapter failed")
    result = parse_adapter_output(completed.stdout)
    record = kb.load_json(entry / "record.json", {})
    ocr = kb.load_json(entry / "ocr.json", {})
    report = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "entry_id": entry.name,
        "status": result["review_status"],
        "method": "visual-adapter",
        "locality": locality,
        "engine": str(result.get("engine", "external-visual-review")),
        "reviewer": str(result.get("reviewer", "visual-adapter")),
        "reviewed_at": now_iso(),
        "input_digest": input_digest(entry, record, ocr),
        "reviewed_text": str(result.get("reviewed_text", "")),
        "diagram_facts": result.get("diagram_facts", []),
        "uncertainties": result.get("uncertainties", []),
        "notes": str(result.get("notes", "")),
    }
    if report["status"] == "passed":
        kb.write_text(entry / "problem.md", build_problem(entry, report["reviewed_text"], report["diagram_facts"]))
        record.setdefault("ocr", {})["review_required"] = False
        record["source_review"] = {
            "status": "passed",
            "method": report["method"],
            "engine": report["engine"],
            "locality": locality,
            "reviewed_at": report["reviewed_at"],
            "input_digest": report["input_digest"],
        }
        record["updated_at"] = now_iso()
        kb.write_json(entry / "record.json", record)
    else:
        record.setdefault("ocr", {})["review_required"] = True
        record["source_review"] = {"status": "needs-review", "method": report["method"], "engine": report["engine"]}
        record["updated_at"] = now_iso()
        kb.write_json(entry / "record.json", record)
    kb.write_json(entry / "source-review.json", report)
    if report["status"] != "passed":
        packet = prepare_review(entry, "visual adapter reported unresolved uncertainties")
        packet.update({
            "method": report["method"],
            "engine": report["engine"],
            "locality": locality,
            "uncertainties": report["uncertainties"],
            "adapter_report": report,
        })
        kb.write_json(entry / "source-review.json", packet)
        return packet
    return report


def approve_source(entry: Path, reviewer: str, note: str = "") -> dict[str, Any]:
    if not reviewer.strip():
        raise ValueError("reviewer is required")
    record = kb.load_json(entry / "record.json", {})
    if not record:
        raise FileNotFoundError(entry.name)
    problem_path = entry / "problem.md"
    problem = problem_path.read_text(encoding="utf-8") if problem_path.exists() else ""
    if len(problem.strip()) < 30:
        raise ValueError("problem.md is missing or too short")
    unresolved = [marker for marker in ("[待核对]", "（待核对）") if marker in problem]
    if unresolved:
        raise ValueError(f"problem.md still contains unresolved markers: {', '.join(unresolved)}")
    ocr = kb.load_json(entry / "ocr.json", {})
    reviewed_at = now_iso()
    report = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "entry_id": entry.name,
        "status": "passed",
        "method": "human",
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "input_digest": input_digest(entry, record, ocr),
        "problem_sha256": kb.sha256_text(problem),
        "note": note.strip(),
    }
    record.setdefault("ocr", {})["review_required"] = False
    record["source_review"] = {
        "status": "passed",
        "method": "human",
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "input_digest": report["input_digest"],
    }
    record["updated_at"] = reviewed_at
    kb.write_json(entry / "record.json", record)
    kb.write_json(entry / "source-review.json", report)
    return report
