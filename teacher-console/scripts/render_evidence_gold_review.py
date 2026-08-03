#!/usr/bin/env python3
"""Render a reviewable Evidence Gold dataset as a local, single-file HTML UI."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import evidence_evaluation  # noqa: E402

PURPOSE_LABELS = {
    "method_candidate": "method_candidate（方法候选）",
    "applicability_check": "applicability_check（适用性检查）",
    "exception_check": "exception_check（例外检查）",
    "boundary_check": "boundary_check（边界检查）",
    "verification_support": "verification_support（验证支持）",
    "false_friend_check": "false_friend_check（错误朋友检查）",
}


def review_cases(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in dataset["cases"]:
        gold = item["gold_case"]
        need = gold["retrieval_need"]
        result.append({
            "id": gold["case_id"],
            "title": gold["case_id"],
            "problem": item["problem"],
            "purpose": PURPOSE_LABELS.get(need["purpose"], need["purpose"]),
            "question": need["question"],
            "facets": need["required_facets"],
            "conflicts": need["forbidden_conflicts"],
            "diagnostics": need.get("diagnostic_targets", []),
            "authority": need["minimum_authority"],
            "expected": gold["expected_status"],
            "required": gold["required_evidence_ids"],
            "acceptable": gold["acceptable_evidence_ids"],
            "forbidden": gold["forbidden_evidence_ids"],
            "rationale": gold["teacher_rationale"],
        })
    return result


def evidence_texts(
    database: Path,
    evidence_ids: set[str],
    *,
    overlay_units: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    if not evidence_ids:
        return {}
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT evidence_id, text, applicability_json, exceptions_json
            FROM evidence_unit
            WHERE evidence_id IN ({})
            """.format(",".join("?" for _ in evidence_ids)),
            sorted(evidence_ids),
        ).fetchall()
    result = {}
    for evidence_id, text, applicability_json, exceptions_json in rows:
        applicability = "；".join(json.loads(applicability_json))
        exceptions = "；".join(json.loads(exceptions_json))
        result[evidence_id] = f"{text} 适用：{applicability}。例外：{exceptions}。"
    for item in overlay_units or []:
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id not in evidence_ids:
            continue
        result[evidence_id] = (
            f"{item['text']} 适用：{'；'.join(item['applicability'])}。例外：{'；'.join(item['exceptions'])}。"
        )
    missing = evidence_ids - set(result)
    if missing:
        raise ValueError(f"Evidence Units missing from store: {sorted(missing)}")
    return result


def replace_script_data(
    html: str,
    dataset_js: dict[str, Any],
    evidence_js: dict[str, str],
) -> str:
    start = html.index("    const DATASET = ")
    end_marker = "\n\n    const STORAGE_KEY"
    end = html.index(end_marker, start)
    replacement = (
        "    const DATASET = "
        + json.dumps(dataset_js, ensure_ascii=False, indent=6)
        + ";\n\n    const EVIDENCE = "
        + json.dumps(evidence_js, ensure_ascii=False, indent=6)
        + ";"
    )
    return html[:start] + replacement + html[end:]


def render(
    dataset_path: Path,
    *,
    template_path: Path,
    database: Path,
    output: Path,
    prior_dataset_path: Path | None = None,
    prior_review_path: Path | None = None,
    evidence_overlay_path: Path | None = None,
) -> None:
    dataset = evidence_evaluation.normalize_gold_dataset(json.loads(dataset_path.read_text(encoding="utf-8")))
    count = len(dataset["cases"])
    splits = {item["gold_case"]["evaluation_split"] for item in dataset["cases"]}
    split = next(iter(splits)) if len(splits) == 1 else "mixed"
    kind = "Fresh Holdout" if split == "holdout" else "Calibration"
    kind_lower = "holdout" if split == "holdout" else "calibration"
    warning = (
        f"这 {count} 条是 fresh holdout 候选，在教师批准前不是真值。"
        "全部同意只会生成审核记录；批准后只允许运行影子评测，不会自动接入生产 W3。"
        if split == "holdout"
        else f"这 {count} 条是 calibration，不是独立 holdout。全部同意只会生成审核记录，仍不会自动接入生产 W3。"
    )
    cases = review_cases(dataset)
    initial_decisions: dict[str, dict[str, str]] = {}
    carried_count = 0
    if prior_dataset_path and prior_review_path:
        prior_dataset = evidence_evaluation.normalize_gold_dataset(
            json.loads(prior_dataset_path.read_text(encoding="utf-8"))
        )
        prior_review = evidence_evaluation.normalize_gold_review(
            json.loads(prior_review_path.read_text(encoding="utf-8")),
            prior_dataset,
        )
        old_cases = {
            item["gold_case"]["case_id"]: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in prior_dataset["cases"]
        }
        new_cases = {
            item["gold_case"]["case_id"]: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in dataset["cases"]
        }
        for decision in prior_review["decisions"]:
            case_id = decision["case_id"]
            if decision["decision"] == "approved" and old_cases.get(case_id) == new_cases.get(case_id):
                initial_decisions[case_id] = {
                    "decision": "approved",
                    "note": "从上一版逐条审核结转；本条内容未变化。",
                }
        carried_count = len(initial_decisions)
        warning += f" 已结转 {carried_count} 条内容未变化的同意决定；只需复核其余 {count - carried_count} 条。"
    used_ids = {
        evidence_id
        for item in cases
        for field in ("required", "acceptable", "forbidden")
        for evidence_id in item[field]
    }
    overlay_units = None
    if evidence_overlay_path:
        overlay = json.loads(evidence_overlay_path.read_text(encoding="utf-8"))
        if overlay.get("schema") != "wuli.evidence-overlay.v1":
            raise ValueError("evidence overlay schema mismatch")
        overlay_units = overlay.get("units")
        if not isinstance(overlay_units, list):
            raise ValueError("evidence overlay units must be an array")
    html = template_path.read_text(encoding="utf-8")
    html = re.sub(
        r"<title>.*?</title>",
        f"<title>悟理 Evidence Gold {kind} 审核</title>",
        html,
        count=1,
    )
    html = re.sub(
        r"<h1>.*?</h1>",
        f"<h1>Evidence Gold {kind} 审核</h1>",
        html,
        count=1,
    )
    html = re.sub(
        r'<div class="warning">.*?</div>',
        f'<div class="warning">{warning}</div>',
        html,
        count=1,
    )
    html = re.sub(r"已审核 0 / \d+", f"已审核 0 / {count}", html, count=1)
    html = re.sub(
        r'<progress id="progress" max="\d+"',
        f'<progress id="progress" max="{count}"',
        html,
        count=1,
    )
    html = re.sub(r"待审核 \d+", f"待审核 {count}", html, count=1)
    html = re.sub(
        r"一键同意全部 \d+ 条",
        f"一键同意全部 {count} 条",
        html,
    )
    html = html.replace(
        "evidence-calibration-review-approved.json",
        f"evidence-{kind_lower}-review-approved.json",
    ).replace(
        "evidence-calibration-review-changes-requested.json",
        f"evidence-{kind_lower}-review-changes-requested.json",
    )
    html = re.sub(
        r"我已审核并同意全部 \d+ 条 Evidence calibration。",
        f"我已审核并同意全部 {count} 条 Evidence {kind_lower}。",
        html,
    )
    html = html.replace(
        "Evidence calibration 有退回修改项。",
        f"Evidence {kind_lower} 有退回修改项。",
    )
    html = re.sub(
        r"确认同意全部 \d+ 条 calibration\?",
        f"确认同意全部 {count} 条 {kind_lower}？",
        html,
    )
    html = re.sub(
        r"已同意全部 \d+ 条，请填写审核人并下载审核结果。",
        f"已同意全部 {count} 条，请填写审核人并下载审核结果。",
        html,
    )
    html = replace_script_data(
        html,
        {
            "id": dataset["dataset_id"],
            "version": dataset["dataset_version"],
            "fingerprint": dataset["dataset_fingerprint"],
            "cases": cases,
        },
        evidence_texts(database, used_ids, overlay_units=overlay_units),
    )
    html = html.replace(
        "    let decisions = {};\n"
        '    try { decisions = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); }'
        " catch (_) { decisions = {}; }",
        "    const INITIAL_DECISIONS = " + json.dumps(initial_decisions, ensure_ascii=False, indent=6) + ";\n"
        "    let decisions = { ...INITIAL_DECISIONS };\n"
        "    try { decisions = { ...INITIAL_DECISIONS, "
        '                    ...JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") }; }'
        " catch (_) { decisions = { ...INITIAL_DECISIONS }; }",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--template",
        type=Path,
        default=ROOT / "output" / "evidence-calibration-review.html",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "student-error-library" / "indexes" / "wuli-memory.db",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prior-dataset", type=Path)
    parser.add_argument("--prior-review", type=Path)
    parser.add_argument("--evidence-overlay", type=Path)
    args = parser.parse_args()
    render(
        args.dataset,
        template_path=args.template,
        database=args.database,
        output=args.output,
        prior_dataset_path=args.prior_dataset,
        prior_review_path=args.prior_review,
        evidence_overlay_path=args.evidence_overlay,
    )
    print(json.dumps({"status": "created", "path": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
