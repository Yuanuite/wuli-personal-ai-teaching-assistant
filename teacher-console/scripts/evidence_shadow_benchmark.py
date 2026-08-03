#!/usr/bin/env python3
"""Run the frozen curated Evidence Agent calibration through Agent Gateway."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import evidence_benchmark  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--evidence-overlay",
        type=Path,
        help="evaluation-only Evidence Unit overlay; never loaded by production defaults",
    )
    parser.add_argument(
        "--ack-provider-dependent-data",
        action="store_true",
        help="required even though this benchmark restricts evidence to curated A-level sources",
    )
    args = parser.parse_args()
    if not args.ack_provider_dependent_data:
        parser.error(
            "live benchmark requires --ack-provider-dependent-data; "
            "the CLI process is local but provider data locality is not guaranteed"
        )
    environ = dict(os.environ)
    environ["TEACHER_CONSOLE_AGENT_PROVIDER"] = args.provider
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    overlay_units = None
    if args.evidence_overlay:
        overlay = json.loads(args.evidence_overlay.read_text(encoding="utf-8"))
        if overlay.get("schema") != "wuli.evidence-overlay.v1":
            parser.error("evidence overlay schema mismatch")
        overlay_units = overlay.get("units")
        if not isinstance(overlay_units, list):
            parser.error("evidence overlay units must be an array")
    report = evidence_benchmark.run_paired_benchmark(
        dataset,
        library_root=args.library.expanduser().resolve(),
        gateway=AgentGateway(environ=environ),
        top_k=args.top_k,
        max_cases=args.max_cases,
        allow_provider_dependent_data=args.ack_provider_dependent_data,
        projection_overlay=overlay_units,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
