#!/usr/bin/env python3
"""Real-provider legacy W3 isolated shadow canary (work-tree A5.2, approved).

Runs the full W3 claim-evidence shadow (decompose -> solver-a -> solver-b ->
claim-verifier -> proof aggregation) on ONE synthetic complex physics problem
(no student data) through the real Gateway path with distinct solver/verifier
identities (deepseek-v4-flash-api + deepseek-v4-pro-api). The shadow is
isolated in a temporary library; canonical answers are never written. Respects
the existing W3 call limits and does not fall back to a production Core rerun.

Usage:
    python3 teacher-console/scripts/w3_shadow_real_canary.py [--problem 1]
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

import kb  # noqa: E402
import model_registry  # noqa: E402
import server as teacher_server  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from analysis_qualification_canary import PROBLEMS  # noqa: E402

REAL_LIBRARY = ROOT / "student-error-library"


def _model_entry(model_id: str) -> dict:
    # Copy the RAW registry entry (including the stored api_key) so the
    # qualification config_digest matches in the temp registry. The public
    # settings view strips api_key and would invalidate the digest.
    registry = kb.load_json(REAL_LIBRARY / "config" / "model-registry.json", {"models": []})
    for item in registry.get("models", []):
        if isinstance(item, dict) and str(item.get("id", "")) == model_id:
            return dict(item)
    raise SystemExit(f"model {model_id} not in registry")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--problem", type=int, default=1)
    args = parser.parse_args()
    os.environ["TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW"] = "1"

    model_registry.LIBRARY = REAL_LIBRARY
    if not model_registry.remote_agent_allowed():
        raise SystemExit("project privacy allow_remote_agent is not enabled; refusing remote W3 shadow")
    solver = _model_entry("deepseek-v4-flash-api")
    verifier = _model_entry("deepseek-v4-pro-api")

    problem = PROBLEMS[args.problem]
    with tempfile.TemporaryDirectory(prefix="wuli-w3-real-canary-") as temp_name:
        library = Path(temp_name) / "library"
        kb.init_library(library)
        (library / "config.json").write_text(
            json.dumps(
                {"schema_version": 1, "privacy": {"allow_remote_agent": True, "storage": "local"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        kb.write_json(
            library / "config" / "model-registry.json",
            {
                "schema_version": 1,
                "defaults": {
                    # The W3 solver resolves via the analysis.generate default;
                    # deepseek-v4-flash-api carries a current qualification
                    # record, deepseek-v4-pro-api has never been qualified so it
                    # is only used as the claim-verifier identity (no
                    # qualification gate on claim.verify).
                    "analysis.generate": "deepseek-v4-flash-api",
                    "claim.verify": "deepseek-v4-pro-api",
                    "expert": "deepseek-v4-flash-api",
                },
                "models": [solver, verifier],
            },
        )
        kb.write_json(
            library / "config" / "analysis-production-routing.json",
            {
                "schema_version": 1,
                "policy_version": "wuli-core-first-routing-v1",
                "mode": "legacy-adaptive",
                "max_latency_seconds": 90,
            },
        )
        kb.write_json(
            library / "config" / "w3r-production-routing.json",
            {
                "schema_version": 1,
                "policy_version": "wuli-w3r-routing-v1",
                "mode": "shadow",
                "gray_entry_ids": [],
                "evidence": {
                    "report_digest": "",
                    "paired_case_count": 0,
                    "teacher_reviewed_case_count": 0,
                    "fresh_holdout_case_count": 0,
                    "fresh_holdout_target_count": 0,
                    "final_answer_fidelity": 0.0,
                    "claim_support_coverage": 0.0,
                    "condition_retention": 0.0,
                    "target_coverage": 0.0,
                    "latex_validity": 0.0,
                    "unsupported_claim_rate": 1.0,
                    "teacher_readability_preference": 0.0,
                    "teacher_edit_rate_non_regression": False,
                },
            },
        )
        entry = library / "entries" / "w3-real-canary"
        entry.mkdir(parents=True)
        (entry / "problem.md").write_text(problem["text"], encoding="utf-8")
        kb.write_json(
            entry / "record.json",
            {
                "schema_version": 1,
                "id": entry.name,
                "kind": "error",
                "status": "needs-review",
                "answer_status": "pending",
                "title": problem["title"],
                "subject": "高中物理",
                "knowledge_points": ["测试"],
                "error_types": ["待确认"],
                "source_review": {"status": "passed"},
            },
        )

        teacher_server.LIBRARY = library
        teacher_server.MODEL_REGISTRY_PATH = library / "config" / "model-registry.json"
        model_registry.LIBRARY = library
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise SystemExit("DEEPSEEK_API_KEY not set")
        teacher_server.AGENT_GATEWAY = AgentGateway(
            environ={
                **os.environ,
                "TEACHER_CONSOLE_AGENT_API_BASE_URL": "https://api.deepseek.com",
                "TEACHER_CONSOLE_AGENT_API_MODEL": str(solver.get("model", "")),
                "TEACHER_CONSOLE_AGENT_API_KEY": api_key,
                "TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "60",
            }
        )
        handler = object.__new__(teacher_server.Handler)
        result = handler.run_w3_shadow_analysis(
            entry,
            {"routing_tier": "expert", "method_profile": "high_school_standard"},
        )

    stages = [
        {
            "stage": item.get("stage"),
            "status": item.get("status"),
            "duration_seconds": item.get("duration_seconds"),
            "failure_type": item.get("failure_type"),
        }
        for item in result.get("stages", [])
        if isinstance(item, dict)
    ]
    report = result.get("report") or {}
    aggregation = report.get("aggregation") or {}
    summary = {
        "schema": "wuli.w3-shadow-real-canary.v1",
        "problem_id": problem["id"],
        "status": result.get("status"),
        "message": str(result.get("message", ""))[:500],
        "stage_count": len(stages),
        "stages": stages,
        "proof_aggregation_status": aggregation.get("status"),
        "verified_claim_count": aggregation.get("verified_claim_count"),
        "unresolved_claim_count": aggregation.get("unresolved_claim_count"),
        "w3r_shadow_status": (report.get("w3r_shadow") or {}).get("status"),
        "redactions": ["api-keys", "full-prompts", "reasoning-bodies", "student-data", "absolute-paths"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        f"[canary] W3 shadow status={result.get('status')} stages={len(stages)} "
        f"proof={aggregation.get('status', 'n/a')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
