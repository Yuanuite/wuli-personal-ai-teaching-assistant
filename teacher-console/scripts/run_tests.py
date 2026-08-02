#!/usr/bin/env python3
"""Fixed test runtime for teacher-console unit tests (T10/A6.3, closure C5.1).

Runs the work-tree acceptance test list (or every unit test) with the same
sys.path layout from any working directory, so results do not depend on
accidentally landing in ``teacher-console/``.

Usage:
    python3 teacher-console/scripts/run_tests.py                 # acceptance list
    python3 teacher-console/scripts/run_tests.py --all           # every unit test
    python3 teacher-console/scripts/run_tests.py --strict        # fail on missing/skipped
    python3 teacher-console/scripts/run_tests.py test_foo.py     # selected files

``--strict`` treats a missing required test or any skipped test as a failure
(C-T10: no false green). Known environment exclusions can be declared with
``--exclude``; excluded tests are reported separately and never count toward
the pass/fail verdict.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"

ACCEPTANCE_TESTS = [
    "test_model_registry.py",
    "test_visual_extraction.py",
    "test_visual_source_review.py",
    "test_mimo_deepseek_collaboration.py",
    "test_agent_http.py",
    "test_agent_routing.py",
    "test_core_analysis.py",
    "test_physics_diagram.py",
    "test_static_contract.py",
    "test_runtime_environment.py",
    "test_svg_collaboration.py",
    "test_analysis_routing.py",
    "test_entry_visual_extract.py",
    "test_agent_gateway.py",
    "test_visual_application.py",
    "test_route_snapshot.py",
    "test_diagram_visual_review.py",
    "test_diagram_application.py",
    "test_analysis_run_observability.py",
    "test_analysis_run_report.py",
]

_SKIPPED_RE = re.compile(r"\b(?:skipped|SKIPPED)\b")


def _env_for(python: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(CONSOLE), str(SKILL_SCRIPTS), env.get("PYTHONPATH", "")]
    )
    return env


def _check_runtime(python: str) -> list[str]:
    """Quick environment detection so a wrong interpreter fails fast."""
    problems: list[str] = []
    probe = subprocess.run(
        [python, "-c", "import PIL, http.server"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        problems.append(
            f"interpreter {python!r} lacks required deps (Pillow): {probe.stderr.strip()[:200]}"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="python interpreter to use (default: the interpreter running this script)",
    )
    parser.add_argument("--all", action="store_true", help="run every teacher-console unit test")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when a required test is missing or any test is skipped",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="NAME",
        help="known exclusion (file or file::test); reported separately, never a failure",
    )
    parser.add_argument("targets", nargs="*", help="test file names; defaults to the acceptance list")
    args = parser.parse_args()

    problems = _check_runtime(args.python)
    for problem in problems:
        print(f"[env] {problem}", file=sys.stderr)

    if args.all:
        targets = sorted(path.name for path in (CONSOLE / "tests").glob("test_*.py"))
    else:
        targets = args.targets or ACCEPTANCE_TESTS

    env = _env_for(args.python)
    failures: list[tuple[str, str]] = []
    excluded_reports: list[str] = []

    def is_excluded(name: str) -> bool:
        return any(name == item or name.startswith(item + "::") for item in args.exclude)

    for name in targets:
        target = CONSOLE / "tests" / name
        if not target.is_file():
            verdict = f"missing:{name}"
            if is_excluded(name):
                excluded_reports.append(verdict)
            else:
                failures.append((name, "missing"))
            continue
        print(f"== {name} ==")
        result = subprocess.run(
            [
                args.python,
                "-B",
                "-m",
                "unittest",
                "discover",
                "-s",
                str(CONSOLE / "tests"),
                "-p",
                name,
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            verdict = f"exit={result.returncode}"
            if is_excluded(name):
                excluded_reports.append(f"{name}({verdict})")
            else:
                failures.append((name, verdict))
            continue
        if args.strict and _SKIPPED_RE.search(combined):
            verdict = "skipped"
            if is_excluded(name):
                excluded_reports.append(f"{name}(skipped)")
            else:
                failures.append((name, verdict))

    print()
    if problems:
        print(f"环境问题 {len(problems)} 项；测试结果可能不可复现。")
    for report in excluded_reports:
        print(f"[排除项] {report}")
    if failures:
        print(f"失败 {len(failures)}/{len(targets)}: " + ", ".join(f"{n}({r})" for n, r in failures))
        return 1
    print(f"全部通过 {len(targets)}/{len(targets)}" + (f"，排除项 {len(excluded_reports)} 单独报告" if excluded_reports else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
