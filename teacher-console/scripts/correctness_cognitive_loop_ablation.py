#!/usr/bin/env python3
"""Run the fixed-condition cognitive-loop off/on ablation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import correctness_ablation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-at")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()
    report = correctness_ablation.run_ablation(
        generated_at=args.generated_at
    )
    if args.markdown:
        print(correctness_ablation.render_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(
        value
        for key, value in report["gates"].items()
        if key != "production_authorized"
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
