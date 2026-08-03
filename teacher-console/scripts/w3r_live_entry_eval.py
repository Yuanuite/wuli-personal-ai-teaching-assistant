#!/usr/bin/env python3
"""Run current W3+Claim Evidence+W3R on isolated copies of real entries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
SCRIPTS = CONSOLE / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SCRIPTS))

import analysis_artifacts  # noqa: E402
import analysis_routing  # noqa: E402
import paired_answer_web_run  # noqa: E402
import server  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def text_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluate_entry(
    source_library: Path,
    entry_id: str,
    *,
    output_dir: Path,
    routing_tier: str,
    model_id: str,
    method_profile: str,
    keep_workspace: bool = False,
) -> dict[str, Any]:
    source_entry = source_library / "entries" / entry_id
    prior_request = load_json(source_entry / "w3-shadow-report.json")
    prior_report = prior_request.get("report", {}) if isinstance(prior_request.get("report"), dict) else {}
    prior_solution = str(prior_report.get("recommended_student_solution", ""))

    started = time.monotonic()

    def run_in_workspace(workspace: Path) -> dict[str, Any]:
        library = workspace / "student-error-library"
        if not library.is_dir():
            library = paired_answer_web_run.prepare_workspace(source_library, workspace, entry_id)
        paired_answer_web_run.configure_server(library, workspace)
        entry = library / "entries" / entry_id
        handler = object.__new__(server.Handler)
        return cast(
            dict[str, Any],
            handler.run_w3_shadow_analysis(
                entry,
                {
                    "routing_tier": routing_tier,
                    "model_id": model_id,
                    "method_profile": method_profile,
                },
            ),
        )

    if keep_workspace:
        workspace = output_dir / "workspaces" / entry_id
        workspace.mkdir(parents=True, exist_ok=True)
        result = run_in_workspace(workspace)
    else:
        with tempfile.TemporaryDirectory(prefix=f"w3r-live-{entry_id[:18]}-") as temporary:
            result = run_in_workspace(Path(temporary))
    elapsed = round(time.monotonic() - started, 4)

    report = result.get("report", {}) if isinstance(result.get("report"), dict) else {}
    evidence = report.get("claim_evidence_shadow", {}) if isinstance(report.get("claim_evidence_shadow"), dict) else {}
    aggregation = evidence.get("aggregation", {}) if isinstance(evidence.get("aggregation"), dict) else {}
    shadow = report.get("w3r_shadow", {}) if isinstance(report.get("w3r_shadow"), dict) else {}
    render = shadow.get("render_result", {}) if isinstance(shadow.get("render_result"), dict) else {}
    gate = render.get("render_gate_report", {}) if isinstance(render.get("render_gate_report"), dict) else {}
    ready, readiness_errors = analysis_routing.w3r_render_readiness(report)
    current_solution = str(report.get("recommended_student_solution", ""))
    method_errors = (
        analysis_artifacts.student_method_errors(current_solution, method_profile) if current_solution else []
    )
    artifact_dir = output_dir / "artifacts" / entry_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if current_solution:
        (artifact_dir / "w3-recommended.md").write_text(current_solution, encoding="utf-8")
    if report.get("blueprint"):
        (artifact_dir / "w3-blueprint.json").write_text(
            json.dumps(report["blueprint"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if render.get("student_solution_md"):
        (artifact_dir / "student-solution.md").write_text(str(render["student_solution_md"]), encoding="utf-8")
    if render.get("teacher_solution_md"):
        (artifact_dir / "teacher-solution.md").write_text(str(render["teacher_solution_md"]), encoding="utf-8")

    telemetry = result.get("stages", [])
    stage_timings: list[dict[str, Any]] = (
        [
            {
                "stage": str(item.get("stage", "")),
                "batch_index": item.get("batch_index"),
                "provider": str(item.get("provider", "")),
                "duration_seconds": float(item.get("duration_seconds", 0.0) or 0.0),
                "provider_seconds": float(item.get("provider_seconds", 0.0) or 0.0),
                "overhead_seconds": float(item.get("overhead_seconds", 0.0) or 0.0),
                "attempt_count": int(item.get("attempt_count", 0) or 0),
            }
            for item in telemetry
            if isinstance(item, dict)
        ]
        if isinstance(telemetry, list)
        else []
    )
    measured_stage_work_seconds = round(
        float(sum(item["duration_seconds"] for item in stage_timings)),
        4,
    )
    measured_provider_seconds = round(
        float(sum(item["provider_seconds"] for item in stage_timings)),
        4,
    )
    semantic_audit = evidence.get("semantic_audit", {}) if isinstance(evidence.get("semantic_audit"), dict) else {}
    claim_audit_wall_seconds = round(
        float(semantic_audit.get("duration_seconds", 0.0) or 0.0),
        4,
    )
    critical_path_stage_seconds = round(
        float(sum(item["duration_seconds"] for item in stage_timings if item["stage"] != "claim-verifier"))
        + claim_audit_wall_seconds,
        4,
    )
    summary = {
        "entry_id": entry_id,
        "status": result.get("status", "failed"),
        "elapsed_seconds": elapsed,
        "screen_decision": report.get("screen", {}).get("decision", ""),
        "stage_count": len(telemetry) if isinstance(telemetry, list) else 0,
        "provider_call_count": sum(
            1 for item in telemetry if isinstance(item, dict) and item.get("provider") not in {"checkpoint", None, ""}
        ),
        "checkpoint_replay_count": sum(
            1 for item in telemetry if isinstance(item, dict) and item.get("provider") == "checkpoint"
        ),
        "measured_stage_work_seconds": measured_stage_work_seconds,
        "measured_provider_work_seconds": measured_provider_seconds,
        "claim_audit_wall_seconds": claim_audit_wall_seconds,
        "critical_path_stage_seconds": critical_path_stage_seconds,
        "measured_framework_overhead_seconds": round(
            float(sum(item["overhead_seconds"] for item in stage_timings)),
            4,
        ),
        "outside_stage_seconds": round(
            max(elapsed - critical_path_stage_seconds, 0.0),
            4,
        ),
        "stage_timings": stage_timings,
        "stage_failures": [
            {
                "stage": item.get("stage", ""),
                "failure_type": item.get("failure_type", ""),
                "message": item.get("message", ""),
                "stderr": str(item.get("stderr", ""))[:2_000],
                "adapter_error": str(item.get("adapter_error", ""))[:1_000],
                "adapter_output": str(item.get("adapter_output", ""))[-4_000:],
            }
            for item in telemetry
            if isinstance(item, dict) and item.get("status") != "completed"
        ][:4],
        "w3_solution_digest": text_digest(current_solution) if current_solution else "",
        "matches_prior_teacher_reviewed_w3": bool(prior_solution and current_solution == prior_solution),
        "method_profile": method_profile,
        "method_gate_status": ("not-run" if not current_solution else "pass" if not method_errors else "failed"),
        "method_gate_errors": method_errors,
        "stage_interface_status": evidence.get("stage_interface_report", {}).get("status", "missing"),
        "claim_aggregation_status": aggregation.get("status", "missing"),
        "claim_count": evidence.get("metrics", {}).get("claim_count", 0),
        "certificate_count": evidence.get("metrics", {}).get("certificate_count", 0),
        "unresolved_claim_count": evidence.get("metrics", {}).get("unresolved_claim_count", 0),
        "w3r_status": shadow.get("status", "missing"),
        "w3r_gate_status": gate.get("status", "missing"),
        "w3r_metrics": gate.get("metrics", {}),
        "w3r_violation_codes": [
            str(item.get("code", "")) for item in gate.get("violations", []) if isinstance(item, dict)
        ],
        "w3r_production_ready": ready,
        "w3r_readiness_errors": readiness_errors,
        "student_character_count": len(str(render.get("student_solution_md", ""))),
        "student_section_count": str(render.get("student_solution_md", "")).count("## "),
        "render_attempt": render.get("attempt", 0),
    }
    (artifact_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry_id", nargs="+")
    parser.add_argument(
        "--library",
        type=Path,
        default=PROJECT_ROOT / "student-error-library",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--routing-tier", default="expert")
    parser.add_argument("--model-id", default="Deepseek-v4-pro")
    parser.add_argument(
        "--method-profile",
        choices=["high_school_standard", "olympiad_official"],
        default="high_school_standard",
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Keep the isolated /private/tmp workspace so successful stage checkpoints can be reused.",
    )
    args = parser.parse_args()

    os.environ["TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW"] = "1"
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for entry_id in args.entry_id:
        row = evaluate_entry(
            args.library.resolve(),
            entry_id,
            output_dir=args.output,
            routing_tier=args.routing_tier,
            model_id=args.model_id,
            method_profile=args.method_profile,
            keep_workspace=args.keep_workspace,
        )
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {
        "schema": "wuli.w3r-live-entry-eval.v1",
        "mode": "isolated-shadow",
        "claim_evidence_enabled": True,
        "solver_provider_policy": "claude-only",
        "method_profile": args.method_profile,
        "canonical_answers_changed": False,
        "case_count": len(rows),
        "cases": rows,
    }
    (args.output / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if rows and all(row["status"] == "completed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
