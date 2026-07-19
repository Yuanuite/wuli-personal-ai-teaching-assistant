#!/usr/bin/env python3
"""Run static, JavaScript syntax, and ZIP integrity checks."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("--zip", type=Path, dest="zip_path")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = args.html.read_text(encoding="utf-8")
    except Exception as exc:
        raise SystemExit(f"cannot read UTF-8 HTML: {exc}")

    required = {
        "doctype": r"<!doctype\s+html",
        "charset": r"<meta[^>]+charset=[\"']?utf-8",
        "viewport": r"<meta[^>]+name=[\"']viewport[\"']",
        "script": r"<script\b",
    }
    for label, pattern in required.items():
        if not re.search(pattern, text, re.I):
            errors.append(f"missing {label}")

    forbidden = {
        "remote URL": r"https?://",
        "fetch": r"\bfetch\s*\(",
        "XMLHttpRequest": r"\bXMLHttpRequest\b",
        "WebSocket": r"\bWebSocket\b",
        "iframe": r"<iframe\b",
        "document.currentScript": r"document\.currentScript",
    }
    for label, pattern in forbidden.items():
        if re.search(pattern, text, re.I):
            errors.append(f"offline incompatibility: {label}")

    ids = re.findall(r"\bid=[\"']([^\"']+)[\"']", text, re.I)
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        errors.append(f"duplicate ids: {', '.join(duplicates)}")

    for expected, message in [
        (r"type=[\"']range[\"']", "no range control found"),
        (r"<canvas\b|<svg\b", "no canvas or SVG found"),
        (r"play|播放", "no visible playback control found"),
    ]:
        if not re.search(expected, text, re.I):
            warnings.append(message)

    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", text, re.I | re.S)
    node = shutil.which("node")
    if node:
        for index, (attributes, script) in enumerate(scripts, 1):
            # Structured data embedded in HTML is intentionally not JavaScript.
            script_type = re.search(r"\btype=[\"']([^\"']+)[\"']", attributes, re.I)
            if script_type and script_type.group(1).lower() not in ("text/javascript", "application/javascript", "module"):
                continue
            result = subprocess.run(
                [node, "--check", "-"],
                input=script,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                errors.append(f"JavaScript block {index}: {result.stderr.strip()}")
    else:
        warnings.append("node not found; JavaScript syntax was not checked")

    if args.zip_path:
        try:
            with zipfile.ZipFile(args.zip_path) as archive:
                bad = archive.testzip()
                if bad:
                    errors.append(f"corrupt ZIP member: {bad}")
                names = archive.namelist()
                if not any(name.endswith(".html") for name in names):
                    errors.append("ZIP contains no HTML file")
                non_ascii = [name for name in names if not name.isascii()]
                if non_ascii:
                    errors.append(f"non-ASCII ZIP member names: {', '.join(non_ascii)}")
        except Exception as exc:
            errors.append(f"ZIP check failed: {exc}")

    report = {"html": str(args.html.resolve()), "errors": errors, "warnings": warnings}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
