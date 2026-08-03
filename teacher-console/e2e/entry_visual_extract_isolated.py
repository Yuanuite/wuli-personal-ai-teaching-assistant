#!/usr/bin/env python3
"""Run the thin visual-extract CLI against an isolated E2E library (C5.3).

``scripts/entry_visual_extract.py`` resolves the vision model through the
``model_registry`` module global, which defaults to the real project library.
This wrapper points that global at the E2E temp library first, then delegates
to the exact same ``main()`` -- no other behavior is changed, and the wrapper
never grants source approval.

Usage: entry_visual_extract_isolated.py <library> <entry-id> [thin-cli-args...]
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
THIN = CONSOLE / "scripts" / "entry_visual_extract.py"

sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))


import model_registry  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: entry_visual_extract_isolated.py <library> <entry-id> [thin-cli-args...]",
            file=sys.stderr,
        )
        return 2
    library = Path(sys.argv[1]).resolve()
    if not (library / "config" / "model-registry.json").is_file():
        print(f"model registry not found under {library}", file=sys.stderr)
        return 2
    model_registry.LIBRARY = library
    # Rebuild the CLI argv as: script-name entry-id --library <library> [args...]
    sys.argv = [sys.argv[0], *sys.argv[2:], "--library", str(library)]
    spec = importlib.util.spec_from_file_location("entry_visual_extract", THIN)
    if spec is None or spec.loader is None:
        print(f"cannot load thin CLI: {THIN}", file=sys.stderr)
        return 2
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
