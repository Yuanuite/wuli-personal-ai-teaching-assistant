#!/usr/bin/env python3
"""Real-provider deadline binding canary (work-tree A5.1, maintainer-approved).

Runs one synthetic complex physics problem (no student data) through the REAL
Gateway path (AgentGateway.run) against deepseek-v4-flash-api and verifies the
frozen deadline budget actually governs the child: the recorded budget is
ordered, the run completes (or fails) with a structured attempt, and the
binding metadata is reported. The child env cap is proven by unit tests
(A4.1); this canary is the real-provider integration smoke.

Usage:
    python3 teacher-console/scripts/deadline_binding_canary.py [--problem 0]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SCRIPTS))

import core_analysis  # noqa: E402
import model_registry  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from analysis_qualification_canary import PROBLEMS  # noqa: E402

LIBRARY = ROOT / "student-error-library"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="deepseek-v4-flash-api")
    parser.add_argument("--problem", type=int, default=0)
    args = parser.parse_args()

    model_registry.LIBRARY = LIBRARY
    if not model_registry.remote_agent_allowed():
        raise SystemExit("project privacy allow_remote_agent is not enabled; refusing remote canary")
    config = model_registry.model_config_for_task("analysis.generate", args.model, "auto")
    if not isinstance(config, dict):
        raise SystemExit(f"model config unavailable: {args.model}")
    api_key = str(config.get("api_key", "")).strip()
    if not api_key:
        api_key = os.environ.get(str(config.get("api_key_env", "")), "")
    if not api_key:
        raise SystemExit("no API key available for the model")

    problem = PROBLEMS[args.problem]
    brief = core_analysis.build_target_brief(problem["text"], method_profile="high_school_standard")

    with tempfile.TemporaryDirectory(prefix="wuli-deadline-canary-") as temp_name:
        entry = Path(temp_name) / "entry"
        entry.mkdir(parents=True)
        (entry / "problem.md").write_text(problem["text"], encoding="utf-8")
        (entry / "record.json").write_text(
            json.dumps(
                {"schema_version": 1, "id": "canary-entry", "title": problem["title"], "subject": "高中物理"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        task = {
            "schema_version": 1,
            "id": f"canary-{problem['id']}",
            "kind": "analysis.generate",
            "entry_id": entry.name,
            "entry_dir": str(entry),
            "working_dir": str(entry),
            "prompt": (
                "处理已复核的物理题。一次完成核心求解：覆盖 Target Brief 中的每个目标，"
                "写最终结论、决定性推导、适用条件和复算检查。不要生成教学 Markdown，"
                "不要分解成阶段接口，不要模拟第二求解器或仲裁器。\n\n题目：\n" + problem["text"]
            ),
            "allow_remote": True,
            "allowed_paths": [
                "record.json",
                "core-solution.json",
                "student-solution.md",
                "teacher-solution.md",
                "solution.md",
            ],
            "input_paths": ["problem.md", "record.json"],
            "denied_paths": [],
            "output_contract": core_analysis.output_contract(brief),
            "timeout_seconds": 90,
            "context_payloads": {".agent-context/target-brief.json": brief},
            "structured_context_paths": [".agent-context/target-brief.json"],
            "model_config": {
                "id": args.model,
                "provider": "openai-compatible",
                "base_url": str(config.get("base_url", "")).rstrip("/"),
                "model": str(config.get("model", "")),
                "api_key": api_key,
                "timeout_seconds": str(config.get("timeout_seconds", "70")),
            },
        }
        env = {
            **os.environ,
            "TEACHER_CONSOLE_AGENT_API_BASE_URL": str(config.get("base_url", "")).rstrip("/"),
            "TEACHER_CONSOLE_AGENT_API_MODEL": str(config.get("model", "")),
            "TEACHER_CONSOLE_AGENT_API_KEY": api_key,
            "TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "70",
        }
        gateway = AgentGateway(environ=env)
        result = gateway.run(
            task,
            materializer=lambda staging, payload: core_analysis.materialize(staging, payload, brief),
        )

    budget = result.get("deadline_budget") or {}
    attempt = (result.get("attempts") or [{}])[0]
    binding_ok = bool(
        budget
        and float(budget.get("http_soft_deadline") or 0) > 0
        and float(budget.get("http_soft_deadline") or 0) + float(budget.get("cleanup_grace") or 0)
        <= float(budget.get("attempt_deadline") or 0)
        <= float(budget.get("task_deadline") or 0)
    )
    summary = {
        "schema": "wuli.deadline-binding-canary.v1",
        "model_id": args.model,
        "problem_id": problem["id"],
        "status": result.get("status"),
        "failure_type": result.get("failure_type", ""),
        "deadline_budget": budget,
        "ordered_invariant": binding_ok,
        "attempt": {
            "provider": attempt.get("provider"),
            "status": attempt.get("status"),
            "duration_seconds": attempt.get("duration_seconds"),
            "timeout_layer": attempt.get("timeout_layer", ""),
            "child_stdout_empty": attempt.get("child_stdout_empty"),
            "timeout_seconds": attempt.get("timeout_seconds"),
            "stage_progress": attempt.get("stage_progress"),
            "token_usage": attempt.get("token_usage"),
        },
        "timeout_summary": result.get("timeout_summary"),
        "redactions": ["api-keys", "full-prompts", "reasoning-bodies", "student-data", "absolute-paths"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if result.get("status") != "completed":
        print(f"[canary] status={result.get('status')} failure={result.get('failure_type', '?')}", file=sys.stderr)
        return 1
    if not binding_ok:
        print("[canary] FAILED: deadline budget ordering invariant violated", file=sys.stderr)
        return 1
    print(f"[canary] OK: completed within ordered deadline budget (soft {budget.get('http_soft_deadline')}s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
