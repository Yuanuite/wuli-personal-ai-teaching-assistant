#!/usr/bin/env python3
"""Generate deterministic Claim Evidence metrics without calling a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import correctness_faults  # noqa: E402
import correctness_metrics  # noqa: E402


DEFAULT_FAULTS = (
    CONSOLE / "tests" / "fixtures" / "correctness_faults.v1.json"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--faults", type=Path, default=DEFAULT_FAULTS)
    parser.add_argument("--shadow-summary", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    parser.add_argument("--generated-at")
    args = parser.parse_args()

    cases = correctness_faults.load_fault_cases(args.faults)
    shadow = json.loads(args.shadow_summary.read_text(encoding="utf-8"))
    report = correctness_metrics.build_report(
        cases,
        shadow_summary=shadow,
        generated_at=args.generated_at,
    )
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown = correctness_metrics.render_markdown(report)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(encoded, encoding="utf-8")
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(markdown, encoding="utf-8")
    if not args.output_json and not args.output_md:
        print(encoded, end="")
    return 0 if all(
        value
        for key, value in report["gates"].items()
        if key != "production_authorized"
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
