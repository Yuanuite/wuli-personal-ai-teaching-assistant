#!/usr/bin/env python3
"""Run teacher-console/scripts/analysis_run_report.py against an isolated E2E library.

The report script (Wave D) resolves its library from the project root and has
no CLI flag for an alternate library; E2E scenarios must run it against their
temporary isolated library, so this wrapper passes the temp path through the
script's ``main(argv, *, library=...)`` entry point. The script itself is
never modified.

Usage:
    python3 run_analysis_report_isolated.py <library> [script args...]
"""

from __future__ import annotations

import sys
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONSOLE / "scripts"))

import analysis_run_report  # noqa: E402

if len(sys.argv) < 2:
    raise SystemExit("usage: run_analysis_report_isolated.py <library> [script args...]")
library = Path(sys.argv[1]).resolve()
if not library.is_dir():
    raise SystemExit(f"library 不存在：{library}")
raise SystemExit(analysis_run_report.main(sys.argv[2:], library=library))
