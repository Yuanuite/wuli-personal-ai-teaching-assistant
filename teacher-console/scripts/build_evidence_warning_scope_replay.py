#!/usr/bin/env python3
"""Derive the bounded warning-scope replay from the revealed MVP-G holdout.

The output is draft calibration evidence. It cannot replace the original
teacher-approved independent holdout or reopen the production gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import evidence_evaluation  # noqa: E402

SOURCE_DATASET_FINGERPRINT = "sha256:a024c0e285c7a58c8de2f53fc0e39b5a46f020541e8c7c362139d9a0beb75d0e"
SOURCE_OVERLAY_FINGERPRINT = "sha256:91e2b444a069eac84a2f25dd653781f11ca03935d88092183dd57524868be869"
AFFECTED_CASE = "fresh2-relative-endpoint-valid"
CORRECTIVE_WARNING = "求得的垂直时刻不在区间内却不检查端点"


def build(source_payload: dict, overlay_payload: dict) -> dict:
    source = evidence_evaluation.normalize_gold_dataset(source_payload)
    overlay = evidence_evaluation.normalize_evidence_overlay(overlay_payload)
    if source["dataset_fingerprint"] != SOURCE_DATASET_FINGERPRINT:
        raise ValueError("source holdout fingerprint drifted; refuse repair replay")
    if source["review_status"] != "teacher_approved":
        raise ValueError("source holdout must be teacher approved")
    if overlay["overlay_fingerprint"] != SOURCE_OVERLAY_FINGERPRINT:
        raise ValueError("source overlay fingerprint drifted; refuse repair replay")
    if overlay["review_status"] != "teacher_approved":
        raise ValueError("source overlay must be teacher approved")
    if source.get("evidence_snapshot_fingerprint") != (overlay["overlay_fingerprint"]):
        raise ValueError("source holdout and overlay fingerprints do not match")

    by_id = {item["gold_case"]["case_id"]: item for item in source["cases"]}
    if AFFECTED_CASE not in by_id:
        raise ValueError("source holdout is missing the affected case")
    item = deepcopy(by_id[AFFECTED_CASE])
    gold = item["gold_case"]
    need = gold["retrieval_need"]
    if CORRECTIVE_WARNING not in need["forbidden_conflicts"]:
        raise ValueError("affected warning classification drifted")
    need["forbidden_conflicts"] = [
        conflict for conflict in need["forbidden_conflicts"] if conflict != CORRECTIVE_WARNING
    ]
    need["diagnostic_targets"] = list(dict.fromkeys([*need.get("diagnostic_targets", []), CORRECTIVE_WARNING]))
    gold["evaluation_split"] = "calibration"
    gold["batch_id"] = ""
    gold["teacher_rationale"] = (
        "该警示描述证据要纠正的漏检行为，而不是使端点判据失效的物理条件；"
        "因此归入 diagnostic_targets。证据仍须完整覆盖时间区间、垂直时刻和区间端点。"
    )

    return evidence_evaluation.normalize_gold_dataset({
        "schema": "wuli.evidence-gold-dataset.v1",
        "dataset_id": "wuli-evidence-mvp-g2-warning-scope-replay",
        "dataset_version": "2026-07-31-repair-v1",
        "review_status": "draft",
        "label_origin": "post_holdout_v2_repair_replay",
        "source_scope": source["source_scope"],
        "reviewer": "",
        "reviewed_at": "",
        "evidence_snapshot_fingerprint": overlay["overlay_fingerprint"],
        "cases": [item],
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence-overlay", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build(
        json.loads(args.source.read_text(encoding="utf-8")),
        json.loads(args.evidence_overlay.read_text(encoding="utf-8")),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
