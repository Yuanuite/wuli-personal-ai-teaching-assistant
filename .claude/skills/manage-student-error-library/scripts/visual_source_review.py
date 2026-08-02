"""Stage registry-routed visual facts for teacher review without approval."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import kb
import source_review
from visual_facts import normalize_payload

VISUAL_SCHEMA = "wuli.visual-facts.v1"
GATE_SCHEMA = "wuli.visual-facts-gate-result.v1"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _validate_extraction(entry: Path, extraction: dict) -> tuple[dict, dict, dict, dict, dict]:
    if not isinstance(extraction, dict) or set(extraction) != {
        "visual_facts",
        "gate_result",
        "trace",
    }:
        raise ValueError("extraction must contain exactly visual_facts, gate_result and trace")
    visual_facts = extraction["visual_facts"]
    gate_result = extraction["gate_result"]
    trace = extraction["trace"]
    if not isinstance(visual_facts, dict) or visual_facts.get("schema") != VISUAL_SCHEMA:
        raise ValueError("visual_facts schema mismatch")
    source_fingerprint = visual_facts.get("source_fingerprint")
    if not SHA256_RE.fullmatch(str(source_fingerprint or "")):
        raise ValueError("visual_facts source_fingerprint is invalid")
    canonical = normalize_payload(visual_facts, source_fingerprint)
    if canonical != visual_facts:
        raise ValueError("visual_facts is not canonical")
    if not isinstance(gate_result, dict) or gate_result.get("schema") != GATE_SCHEMA:
        raise ValueError("gate_result schema mismatch")
    if gate_result.get("status") not in {"passed", "needs-source-review"}:
        raise ValueError("gate_result status is invalid")
    if gate_result.get("visual_facts_fingerprint") != canonical["fingerprint"]:
        raise ValueError("gate_result fingerprint mismatch")
    if not isinstance(gate_result.get("reasons"), list):
        raise ValueError("gate_result reasons must be a list")
    if not isinstance(trace, dict):
        raise ValueError("trace must be an object")
    for field in ("model_id", "provider"):
        if not isinstance(trace.get(field), str) or not trace[field].strip():
            raise ValueError(f"trace {field} is required")
    record = kb.load_json(entry / "record.json", {})
    ocr = kb.load_json(entry / "ocr.json", {})
    expected_digest = source_review.input_digest(entry, record, ocr)
    expected_source = f"sha256:{expected_digest}"
    if trace.get("source_fingerprint") != expected_source:
        raise ValueError("trace source_fingerprint mismatch")
    if canonical["source_fingerprint"] != expected_source:
        raise ValueError("visual_facts source_fingerprint mismatch")
    return canonical, copy.deepcopy(gate_result), copy.deepcopy(trace), record, ocr


def _reason_text(reason: object) -> str:
    if not isinstance(reason, dict):
        return str(reason)
    code = str(reason.get("code", "unknown"))
    fact_id = str(reason.get("fact_id") or "")
    message = str(reason.get("message") or "")
    target = f" [{fact_id}]" if fact_id else ""
    return f"{code}{target}: {message}".strip()


def _review_packet(
    entry: Path,
    visual_facts: dict,
    gate_result: dict,
    trace: dict,
) -> str:
    uncertainties = visual_facts["uncertainties"]
    reasons = gate_result["reasons"]
    lines = [
        "# 题目原图复核单",
        "",
        f"题目编号：`{entry.name}`",
        "",
        f"MiMo 候选模型：`{trace['model_id']}`（{trace['provider']}）",
        "",
        f"视觉事实门控：`{gate_result['status']}`",
        "",
        "机器视觉结果只是待审核证据，不代表题干已经批准。",
        "",
        "## 不确定项",
        "",
        *([f"- {item}" for item in uncertainties] or ["- 无"]),
        "",
        "## 门控原因",
        "",
        *([f"- {_reason_text(item)}" for item in reasons] or ["- 无"]),
        "",
        "## 教师操作",
        "",
        "请对照原图检查并修正 `problem.md`，然后使用教师端正常的“确认题干”操作。",
        "",
    ]
    return "\n".join(lines)


def stage_visual_extraction(entry: Path, extraction: dict) -> dict:
    """Persist a visual candidate while preserving the human source gate."""
    entry = Path(entry)
    visual_facts, gate_result, trace, record, _ocr = _validate_extraction(
        entry, extraction
    )
    reviewed_text = visual_facts["reviewed_text"]
    diagram_statements = [fact["statement"] for fact in visual_facts["diagram_facts"]]
    if reviewed_text.strip():
        kb.write_text(
            entry / "problem.md",
            source_review.build_problem(entry, reviewed_text, diagram_statements),
        )
    kb.write_json(entry / "visual-facts.json", visual_facts)
    kb.write_json(entry / "visual-facts-gate.json", gate_result)
    kb.write_text(
        entry / "source-review.md",
        _review_packet(entry, visual_facts, gate_result, trace),
    )
    report = {
        "schema_version": 1,
        "entry_id": entry.name,
        "status": "needs-review",
        "method": "registry-visual-extract",
        "input_digest": visual_facts["source_fingerprint"][len("sha256:") :],
        "visual_gate_status": gate_result["status"],
        "visual_facts_fingerprint": visual_facts["fingerprint"],
        "engine": str(trace.get("upstream_model") or trace["model_id"]),
        "reviewer": trace["model_id"],
        "trace": trace,
        "uncertainties": copy.deepcopy(visual_facts["uncertainties"]),
        "gate_reasons": copy.deepcopy(gate_result["reasons"]),
        "reviewed_at": source_review.now_iso(),
    }
    kb.write_json(entry / "source-review.json", report)
    updated_record = copy.deepcopy(record)
    updated_record.setdefault("ocr", {})["review_required"] = True
    updated_record["source_review"] = {
        "status": "needs-review",
        "method": "registry-visual-extract",
        "engine": report["engine"],
        "visual_gate_status": gate_result["status"],
        "input_digest": report["input_digest"],
    }
    updated_record["updated_at"] = source_review.now_iso()
    kb.write_json(entry / "record.json", updated_record)
    return report
