#!/usr/bin/env python3
"""Thin CLI for explicit entry actions (closure C4.3).

Shares the exact application services used by the web: ``build-diagram`` calls
the same ``build_diagram()`` service, Gateway, validators and renderer. The CLI
never re-implements providers or the SVG renderer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))


def _fail(status: int, error: str) -> int:
    print(json.dumps({"status": "failed", "error": str(error)[:500]}, ensure_ascii=False))
    return status


def cmd_build_diagram(args) -> int:
    library = Path(args.library)
    entry = (library / "entries" / args.entry_id).resolve()
    try:
        entry.relative_to((library / "entries").resolve())
    except ValueError:
        return _fail(2, "entry path escapes the library")
    if not entry.is_dir():
        return _fail(2, f"entry not found: {args.entry_id}")

    # The application service and Gateway resolve models through module
    # globals; point them at the requested library and honour an explicit
    # adapter runtime from the environment (same contract as the E2E harness).
    import model_registry

    model_registry.LIBRARY = library
    import server

    server.LIBRARY = library
    server.MODEL_REGISTRY_PATH = library / "config" / "model-registry.json"
    from agent_gateway import AgentGateway

    environ = dict(os.environ)
    if str(environ.get("TEACHER_CONSOLE_AGENT_PROVIDER", "")).strip() == "adapter":

        class _AdapterForcingGateway(AgentGateway):
            def _task_environ(self, task=None):
                env = super()._task_environ(task)
                env["TEACHER_CONSOLE_AGENT_PROVIDER"] = "adapter"
                return env

        server.AGENT_GATEWAY = _AdapterForcingGateway(environ=environ)
    else:
        server.AGENT_GATEWAY = AgentGateway(environ=environ)

    from diagram_application import build_diagram
    from model_registry import model_config_for_task, resolve_model_id_for_task

    model_config = None
    try:
        model_id = resolve_model_id_for_task("analysis.generate", args.tier, None)
        model_config = model_config_for_task("analysis.generate", model_id, args.tier)
    except Exception as exc:  # noqa: BLE001 - diagram may still run without a config
        print(json.dumps({"status": "blocked", "error": f"模型路由解析失败：{exc}"}, ensure_ascii=False))
        return 3
    result = build_diagram(
        entry,
        library=library,
        routing_tier=args.tier,
        model_config=model_config,
        enable_soft_review=not args.skip_soft_review,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("status") == "completed" else 4


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    build = sub.add_parser("build-diagram", help="explicit static diagram generation")
    build.add_argument("entry_id")
    build.add_argument("--library", default=str(ROOT / "student-error-library"))
    build.add_argument("--tier", default="auto", choices=["auto", "economy", "expert"])
    build.add_argument("--skip-soft-review", action="store_true", help="skip the non-blocking MiMo soft review")
    build.set_defaults(handler=cmd_build_diagram)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
