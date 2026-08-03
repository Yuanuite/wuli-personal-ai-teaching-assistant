#!/usr/bin/env python3
"""Run one read-only Evidence Agent shadow task from frozen JSON inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

from agent_gateway import AgentGateway  # noqa: E402
import evidence_agent  # noqa: E402


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute one single-route-bypass evidence.build shadow run."
    )
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--entry-id", required=True)
    parser.add_argument("--blueprint", type=Path, required=True)
    parser.add_argument("--needs", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--routing-tier", choices=("economy", "expert"), default="expert")
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument(
        "--source-kind",
        action="append",
        default=[],
        help="restrict model-visible evidence to one source kind; repeatable",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    library = args.library.expanduser().resolve()
    entry = library / "entries" / args.entry_id
    problem_path = entry / "problem.md"
    if not problem_path.is_file():
        parser.error(f"missing approved problem: {problem_path}")
    needs_payload = _json(args.needs)
    needs = (
        needs_payload.get("items", [])
        if isinstance(needs_payload, dict)
        else needs_payload
    )
    if not isinstance(needs, list):
        parser.error("--needs must contain an array or an object with items")

    result = evidence_agent.run_shadow(
        AgentGateway(),
        library_root=library,
        entry=entry,
        problem=problem_path.read_text(encoding="utf-8"),
        blueprint=_json(args.blueprint),
        retrieval_needs=needs,
        top_k_per_need=args.top_k,
        routing_tier=args.routing_tier,
        model_config=_json(args.model_config) if args.model_config else None,
        allow_remote=args.allow_remote,
        source_kinds=tuple(args.source_kind),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if result["status"] in {"sufficient", "not_needed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
