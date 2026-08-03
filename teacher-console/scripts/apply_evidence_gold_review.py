#!/usr/bin/env python3
"""Validate a browser review and materialize a teacher-approved Gold dataset."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import evidence_evaluation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-output", type=Path)
    parser.add_argument("--evidence-overlay", type=Path)
    parser.add_argument("--overlay-output", type=Path)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    if bool(args.evidence_overlay) != bool(args.overlay_output):
        parser.error("--evidence-overlay and --overlay-output must be used together")
    approved_review = evidence_evaluation.normalize_gold_review(review, dataset)
    approved_overlay = None
    if args.evidence_overlay:
        overlay = json.loads(args.evidence_overlay.read_text(encoding="utf-8"))
        approved, approved_review, approved_overlay = evidence_evaluation.apply_gold_review_with_overlay(
            dataset, review, overlay
        )
    elif dataset.get("evidence_snapshot_fingerprint"):
        parser.error("dataset declares an evidence snapshot; --evidence-overlay and --overlay-output are required")
    else:
        approved = evidence_evaluation.apply_gold_review(dataset, review)

    # All validation above must succeed before any member of the approval set is written.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(approved, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.review_output:
        args.review_output.parent.mkdir(parents=True, exist_ok=True)
        args.review_output.write_text(
            json.dumps(approved_review, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.overlay_output and approved_overlay:
        args.overlay_output.parent.mkdir(parents=True, exist_ok=True)
        args.overlay_output.write_text(
            json.dumps(approved_overlay, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": "teacher_approved",
                "dataset_id": approved["dataset_id"],
                "dataset_fingerprint": approved["dataset_fingerprint"],
                "reviewer": approved["reviewer"],
                "output": str(args.output.resolve()),
                "review_output": (str(args.review_output.resolve()) if args.review_output else None),
                "overlay_output": (str(args.overlay_output.resolve()) if args.overlay_output else None),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
