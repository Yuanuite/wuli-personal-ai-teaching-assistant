#!/usr/bin/env python3
"""Derive the bounded MVP-F repair replay from the revealed MVP-E holdout.

This output is calibration-only diagnostic evidence.  It must never be
presented as a fresh or independent holdout.
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

SOURCE_FINGERPRINT = "sha256:49b525482c65f507371efcf98a1ea02888338510e59fce2898e4dee6cdd4ed0f"
CIRCUIT_NODE = "EU-bcf841d2038df8417c913c32"
AFFECTED_CASES = (
    "holdout-angle-ledger-sign-conflict",
    "holdout-circuit-path-valid",
    "holdout-angle-ledger-position-conflict",
)


def build(source_payload: dict) -> dict:
    source = evidence_evaluation.normalize_gold_dataset(source_payload)
    if source["dataset_fingerprint"] != SOURCE_FINGERPRINT:
        raise ValueError("source holdout fingerprint drifted; refuse repair replay")
    by_id = {item["gold_case"]["case_id"]: item for item in source["cases"]}
    if set(AFFECTED_CASES) - set(by_id):
        raise ValueError("source holdout is missing an affected case")

    cases = [deepcopy(by_id[case_id]) for case_id in AFFECTED_CASES]
    for item in cases:
        gold = item["gold_case"]
        gold["evaluation_split"] = "calibration"
        gold["batch_id"] = ""
        if gold["case_id"] == "holdout-angle-ledger-sign-conflict":
            gold["retrieval_need"]["diagnostic_targets"] = ["中途改变角度正方向并直接相加"]
        elif gold["case_id"] == "holdout-circuit-path-valid":
            gold["required_evidence_ids"] = [CIRCUIT_NODE]
            gold["acceptable_evidence_ids"] = [
                CIRCUIT_NODE,
                "EU-4a36df71d3e05cc730ea9aae",
            ]
            gold["teacher_rationale"] = (
                "新增节点拓扑证据直接覆盖连接节点、实际电流通路和等效电路；旧边界证据仍可作为补充，但不能替代节点判据。"
            )

    return evidence_evaluation.normalize_gold_dataset({
        "schema": "wuli.evidence-gold-dataset.v1",
        "dataset_id": "wuli-evidence-mvp-f-repair-replay",
        "dataset_version": "2026-07-30-repair-v1",
        "review_status": "draft",
        "label_origin": "post_holdout_repair_replay",
        "source_scope": source["source_scope"],
        "reviewer": "",
        "reviewed_at": "",
        "cases": cases,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = build(json.loads(args.source.read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
