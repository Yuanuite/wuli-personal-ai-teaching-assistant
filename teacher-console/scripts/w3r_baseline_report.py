#!/usr/bin/env python3
"""Freeze reproducible W3R baseline source metrics without changing answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def build_report(manifest_path: Path, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "wuli.w3r-baseline-manifest.v1":
        raise ValueError("unsupported W3R baseline manifest")
    rows = []
    for item in manifest.get("cases", []):
        source = root / str(item["source"])
        data = source.read_bytes()
        text = data.decode("utf-8")
        headings = re.findall(r"^#{1,6}\s+(.+)$", text, flags=re.MULTILINE)
        rows.append({
            "case_id": item["case_id"],
            "kind": item["kind"],
            "source": item["source"],
            "source_digest": _sha256(data),
            "answer_signature": _sha256(re.sub(r"\s+", "", text).encode("utf-8")),
            "character_count": len(text),
            "sections": headings,
            "latex_inline_delimiter_count": text.count("$") - 2 * text.count("$$"),
            "latex_block_count": text.count("$$") // 2,
            "teacher_approval_state": item["teacher_approval_state"],
        })
    return {
        "schema": "wuli.w3r-baseline-report.v1",
        "manifest_digest": _sha256(manifest_path.read_bytes()),
        "production_behavior_changed": False,
        "case_count": len(rows),
        "cases": rows,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# W3R Baseline v1",
        "",
        f"- 冻结样例：{report['case_count']}",
        "- 生产行为变化：否",
        f"- Manifest：`{report['manifest_digest']}`",
        "",
        "| Case | 类型 | 字符数 | 章节数 | LaTeX 块 | 教师状态 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in report["cases"]:
        lines.append(
            f"| {item['case_id']} | {item['kind']} | "
            f"{item['character_count']} | {len(item['sections'])} | "
            f"{item['latex_block_count']} | {item['teacher_approval_state']} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    args = parser.parse_args()
    report = build_report(args.manifest)
    rendered_json = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json_out:
        args.json_out.write_text(rendered_json, encoding="utf-8")
    else:
        print(rendered_json, end="")
    if args.md_out:
        args.md_out.write_text(markdown_report(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
