#!/usr/bin/env python3
"""Run one private W3 shadow analysis without replacing the W2 reviewed answer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

import server  # noqa: E402
import w3_shadow_benchmark  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry_id", nargs="+")
    parser.add_argument("--routing-tier", default="expert", choices=("auto", "economy", "expert"))
    parser.add_argument("--model-id", default="auto")
    parser.add_argument(
        "--experiment",
        type=Path,
        help="Fresh holdout directory; requires a valid teacher truth lock before replay.",
    )
    args = parser.parse_args()
    if args.experiment:
        manifest = w3_shadow_benchmark.load_json(args.experiment / "manifest.json")
        locked_targets, lock_errors = w3_shadow_benchmark.validate_truth_lock(
            PROJECT_ROOT / "student-error-library", args.experiment, manifest
        )
        if lock_errors:
            parser.error("; ".join(lock_errors))
        unknown = [entry_id for entry_id in args.entry_id if entry_id not in locked_targets]
        if unknown:
            parser.error(
                "entries are not part of the frozen holdout: " + ", ".join(unknown)
            )
    handler = object.__new__(server.Handler)
    summaries = []
    failed = False
    for entry_id in args.entry_id:
        entry = server.safe_entry(entry_id)
        result = handler.run_w3_shadow_analysis(entry, {
            "routing_tier": args.routing_tier,
            "model_id": args.model_id,
        })
        metrics = result.get("report", {}).get("metrics", {})
        summary = {
            "entry_id": entry_id,
            "status": result.get("status"),
            "message": result.get("message", ""),
            "stage_count": len(result.get("stages", [])),
            "metrics": metrics,
        }
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        failed = failed or result.get("status") != "completed"
    print(json.dumps({"runs": summaries}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
