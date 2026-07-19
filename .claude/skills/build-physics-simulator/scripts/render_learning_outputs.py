#!/usr/bin/env python3
"""Backward-compatible bridge; answer rendering is owned by the library Skill."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from build_simulator import render_fragment


def load_answer_renderer():
    script = Path(__file__).resolve().parents[2] / "manage-student-error-library" / "scripts" / "render_answers.py"
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("manage_render_answers", script)
    if not spec or not spec.loader:
        raise RuntimeError("manage-student-error-library render_answers.py is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--entry-dir", type=Path, required=True)
    parser.add_argument("--fragment-template", type=Path)
    parser.add_argument("--fragment-output", type=Path)
    parser.add_argument("--update-canonical", action="store_true")
    args = parser.parse_args()
    renderer = load_answer_renderer()
    result = renderer.render(args.model.resolve(), args.entry_dir.resolve(), args.update_canonical)
    if args.fragment_template or args.fragment_output:
        if not (args.fragment_template and args.fragment_output):
            raise SystemExit("--fragment-template and --fragment-output must be used together")
        model = json.loads(args.model.read_text(encoding="utf-8"))
        render_fragment(model, args.fragment_template.resolve(), args.fragment_output.resolve())
        result["fragment"] = str(args.fragment_output.resolve())
    result["compatibility_bridge"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
