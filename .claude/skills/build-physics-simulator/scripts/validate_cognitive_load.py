#!/usr/bin/env python3
"""Deterministic cognitive-load gates for student-facing physics solutions."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("student_markdown", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=5)
    parser.add_argument("--max-lines", type=int, default=150)
    args = parser.parse_args()
    body = args.student_markdown.read_text(encoding="utf-8")
    model = json.loads(args.model.read_text(encoding="utf-8"))
    errors, warnings = [], []
    for heading in ["## 答案速览", "## 一眼识别", "## 详细解答", "## 易错点", "## 30 秒自测"]:
        if heading not in body:
            errors.append(f"missing section: {heading}")
    steps = model.get("student_solution", {}).get("main_steps", [])
    if len(steps) > args.max_steps:
        errors.append(f"main steps {len(steps)} > {args.max_steps}")
    for index, step in enumerate(steps, 1):
        if len(step.get("formulae", [])) > 4:
            warnings.append(f"step {index} contains more than 4 display formulae")
    line_count = len(body.splitlines())
    if line_count > args.max_lines:
        warnings.append(f"student solution has {line_count} lines > {args.max_lines}")
    if "## 教师审计" in body:
        errors.append("teacher audit leaked into student layer")
    techniques = model.get("technique_ids", [])
    if len(techniques) > 6:
        warnings.append("more than 6 active techniques")
    report = {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "main_steps": len(steps),
            "lines": line_count,
            "display_formulae": len(re.findall(r"^\$\$$", body, flags=re.MULTILINE)) // 2,
            "techniques": len(techniques)
        }
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
