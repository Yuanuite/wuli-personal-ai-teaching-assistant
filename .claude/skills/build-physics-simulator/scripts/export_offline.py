#!/usr/bin/env python3
"""Wrap a simulator fragment as offline HTML and optionally create a ZIP."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from html import escape
from pathlib import Path


BASE_STYLE = """
:root{color-scheme:light;--background:#fff;--foreground:#1a1c1f;--card:#f5f6f7;--card-foreground:#1a1c1f;--primary:#277fbd;--primary-foreground:#fff;--muted-foreground:#5f6670;--border:#cfd5dc;--ring:#277fbd;--destructive:#d6531d;--viz-series-1:#277fbd;--viz-series-2:#c87821;--viz-series-3:#348c50;--viz-series-4:#a53b78}
@media(prefers-color-scheme:dark){:root{color-scheme:dark;--background:#181818;--foreground:#f7f7f7;--card:#292929;--card-foreground:#f7f7f7;--primary:#82c2f0;--primary-foreground:#101010;--muted-foreground:#b3b6ba;--border:#4a4d50;--ring:#82c2f0;--destructive:#ff925c;--viz-series-1:#82c2f0;--viz-series-2:#efaa65;--viz-series-3:#7bd092;--viz-series-4:#e58cba}}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--background);color:var(--foreground)}body{padding:16px;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{width:min(100%,1100px);margin:0 auto}.viz-controls{display:flex;flex-wrap:wrap;align-items:center;gap:8px 10px}.form-label{display:inline-flex;align-items:center;gap:5px}.form-range{flex:1 1 180px;min-width:120px;accent-color:var(--primary)}.btn{appearance:none;border:1px solid var(--border);border-radius:8px;padding:7px 11px;background:var(--card);color:var(--card-foreground);font:inherit;cursor:pointer}.btn:focus-visible,.form-range:focus-visible{outline:3px solid var(--ring);outline-offset:2px}.btn-primary{background:var(--primary);color:var(--primary-foreground);border-color:var(--primary)}.card{border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--card-foreground)}@media(max-width:520px){body{padding:10px}.viz-controls{align-items:stretch}.form-label,.form-range{flex-basis:100%}}
"""


def valid_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise argparse.ArgumentTypeError("--name must use lowercase ASCII letters, digits, and hyphens")
    return value


def wrap(source: str, title: str) -> str:
    if re.search(r"<!doctype\s+html|<html\b", source, re.I):
        return source
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{BASE_STYLE}</style>
</head>
<body><main>
{source}
</main></body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--name", type=valid_name, required=True)
    parser.add_argument("--title", default="Physics Simulator")
    parser.add_argument("--zip", action="store_true", dest="make_zip")
    args = parser.parse_args()

    source = args.source.read_text(encoding="utf-8")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    html_path = args.output_dir / f"{args.name}.html"
    html_path.write_text(wrap(source, args.title), encoding="utf-8")
    result: dict[str, str] = {"html": str(html_path.resolve())}

    if args.make_zip:
        zip_path = args.output_dir / f"{args.name}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(html_path, arcname=html_path.name)
        result["zip"] = str(zip_path.resolve())

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
