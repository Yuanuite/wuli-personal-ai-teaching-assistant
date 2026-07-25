#!/usr/bin/env python3
"""Generate paired benchmark answers through the real teacher-console web UI.

Every case runs in its own temporary library.  The browser clicks the same
``#run-analysis`` control used by a teacher, while the canonical library stays
read-only.  The promoted Agent answer is copied from the temporary
``.agent-baseline`` snapshot into the private benchmark experiment.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))

import kb  # noqa: E402
import model_registry  # noqa: E402
import server as teacher_server  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402
from runtime_environment import resolved_environment  # noqa: E402

EVIDENCE_COHORTS = {
    "current": "web-current",
    "disabled": "web-no-rag",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sqlite_backup(source: Path, target: Path) -> None:
    """Copy a live SQLite database without depending on WAL sidecars."""
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)


def prepare_workspace(source_library: Path, workspace: Path, entry_id: str) -> Path:
    """Build the smallest isolated library that still uses current RAG evidence."""
    library = workspace / "student-error-library"
    kb.init_library(library)

    source_config = source_library / "config.json"
    if source_config.is_file():
        shutil.copy2(source_config, library / "config.json")
    if (source_library / "config").is_dir():
        shutil.copytree(source_library / "config", library / "config", dirs_exist_ok=True)

    sqlite_backup(
        source_library / "indexes" / "wuli-memory.db",
        library / "indexes" / "wuli-memory.db",
    )
    # Do not copy wuli-memory.dirty.json: the database is an immutable snapshot
    # for this run, and the benchmark must not rebuild the canonical store.

    source_entry = source_library / "entries" / entry_id
    if not source_entry.is_dir():
        raise FileNotFoundError(source_entry)
    target_entry = library / "entries" / entry_id
    shutil.copytree(source_entry, target_entry)
    source_assets = teacher_server.source_asset_names(target_entry)
    answer_assets = teacher_server.answer_asset_names(target_entry)
    for relative in answer_assets - source_assets:
        candidate = target_entry / relative
        if candidate.is_file():
            candidate.unlink()
    for generated in (
        "student-solution.md",
        "teacher-solution.md",
        "solution.md",
        "answer-review.json",
        "physics-model.json",
        "physics-model-review.json",
        "visualization-review.json",
        "delivery.json",
        "evaluation.json",
        "pipeline.json",
    ):
        (target_entry / generated).unlink(missing_ok=True)
    shutil.rmtree(target_entry / "visualization", ignore_errors=True)
    shutil.rmtree(target_entry / ".agent-baseline", ignore_errors=True)
    for transient in (
        "analysis-request.json",
        "answer-revision-request.json",
        "source-clean-request.json",
    ):
        (target_entry / transient).unlink(missing_ok=True)
    record_path = target_entry / "record.json"
    record = load_json(record_path)
    record["status"] = "draft"
    record["answer_status"] = "pending"
    record.pop("answer_review", None)
    record.pop("visualization_review", None)
    record.pop("delivery", None)
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return library


def configure_server(library: Path, workspace: Path) -> None:
    teacher_server.LIBRARY = library
    teacher_server.UPLOADS = workspace / "error-collection"
    teacher_server.PUBLIC_SITE = workspace / "student-site"
    teacher_server.MODEL_REGISTRY_PATH = library / "config" / "model-registry.json"
    model_registry.LIBRARY = library
    teacher_server.AGENT_GATEWAY = AgentGateway(
        environment_resolver=lambda: resolved_environment(library)
    )
    teacher_server._JOB_MANAGER = AgentJobManager(
        library / ".cache" / "agent-jobs",
        max_workers=1,
    )


def fixed_evidence_snapshot(entry: Path, evidence_mode: str) -> dict[str, Any]:
    """Build the exact evidence payload that the isolated web click will receive."""
    snapshot = teacher_server.agent_evidence_payload(entry, "analysis.generate", "auto")
    if evidence_mode == "current":
        return snapshot
    if evidence_mode != "disabled":
        raise ValueError(f"unsupported evidence mode: {evidence_mode}")
    budget = dict(snapshot.get("context_budget", {}))
    candidate_count = int(budget.get("candidate_reference_count", 0) or 0)
    budget.update({
        "included_reference_count": 0,
        "omitted_reference_count": candidate_count,
        "truncated": bool(candidate_count),
    })
    disabled = {
        "schema_version": 1,
        "kind": "agent-evidence",
        "task_type": "analysis.generate",
        "status": "disabled-for-benchmark",
        "references": [],
        "instructions": list(snapshot.get("instructions", [])),
        "evidence_set": {
            "kind": "candidate-evidence-set",
            "reference_count": 0,
            "required_slots": [
                "concepts-and-labels",
                "problem-context",
                "solution-method",
            ],
            "covered_slots": [],
            "missing_slots": [
                "concepts-and-labels",
                "problem-context",
                "solution-method",
            ],
            "coverage_ratio": 0.0,
        },
        "context_budget": budget,
    }
    for _ in range(2):
        disabled["context_budget"]["serialized_chars"] = len(
            json.dumps(disabled, ensure_ascii=False)
        )
    return disabled


def run_case(
    *,
    source_library: Path,
    experiment: Path,
    entry_id: str,
    node: str,
    timeout_seconds: int,
    evidence_mode: str = "current",
) -> dict[str, Any]:
    artifact_prefix = EVIDENCE_COHORTS[evidence_mode]
    artifact_dir = experiment / "artifacts" / entry_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    failed_workspace = artifact_dir / f"{artifact_prefix}-failed-workspace"
    if failed_workspace.exists():
        shutil.rmtree(failed_workspace)
    for stale in (
        f"{artifact_prefix}.md",
        f"{artifact_prefix}.meta.json",
        f"{artifact_prefix}.evidence.json",
        f"{artifact_prefix}-browser.json",
        f"{artifact_prefix}-completed.png",
        f"{artifact_prefix}-failure.png",
    ):
        (artifact_dir / stale).unlink(missing_ok=True)
    driver = PROJECT_ROOT / "teacher-console" / "e2e" / "paired-answer-web.e2e.mjs"

    with tempfile.TemporaryDirectory(prefix=f"wuli-answer-pair-{entry_id[:18]}-") as temp_name:
        workspace = Path(temp_name)
        library = prepare_workspace(source_library, workspace, entry_id)
        configure_server(library, workspace)
        entry = library / "entries" / entry_id
        evidence_snapshot = fixed_evidence_snapshot(entry, evidence_mode)
        evidence_text = json.dumps(
            evidence_snapshot,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        evidence_digest = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
        (artifact_dir / f"{artifact_prefix}.evidence.json").write_text(
            evidence_text,
            encoding="utf-8",
        )
        original_evidence_builder = teacher_server.agent_evidence_payload

        def use_fixed_evidence(
            requested_entry: Path,
            kind: str,
            routing_tier: str = "auto",
        ) -> dict[str, Any]:
            if requested_entry.name == entry_id and kind == "analysis.generate":
                return copy.deepcopy(evidence_snapshot)
            return original_evidence_builder(requested_entry, kind, routing_tier)

        teacher_server.agent_evidence_payload = use_fixed_evidence
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), teacher_server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        environment = dict(os.environ)
        environment.update({
            "E2E_BASE_URL": f"http://127.0.0.1:{httpd.server_port}",
            "E2E_ENTRY_ID": entry_id,
            "E2E_ARTIFACT_DIR": str(artifact_dir),
            "E2E_ARTIFACT_PREFIX": artifact_prefix,
            "E2E_TIMEOUT_MS": str(timeout_seconds * 1000),
        })
        try:
            completed = subprocess.run(
                [node, str(driver)],
                cwd=PROJECT_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds + 60,
            )
        except subprocess.TimeoutExpired as exc:
            completed = subprocess.CompletedProcess(
                args=exc.cmd,
                returncode=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + "\nweb benchmark driver timed out",
            )
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            teacher_server._JOB_MANAGER.shutdown(wait=True)
            teacher_server._JOB_MANAGER = None
            teacher_server.agent_evidence_payload = original_evidence_builder

        request = load_json(entry / "analysis-request.json")
        baseline = entry / ".agent-baseline" / "student-solution.md"
        status = {
            "schema_version": 1,
            "entry_id": entry_id,
            "cohort": artifact_prefix,
            "evidence_mode": evidence_mode,
            "evidence_snapshot_sha256": evidence_digest,
            "source": "teacher-console-browser-click",
            "status": "completed"
            if completed.returncode == 0 and baseline.is_file()
            else "failed",
            "driver_returncode": completed.returncode,
            "model_id": request.get("model_id"),
            "model_display_name": request.get("model_display_name"),
            "provider": request.get("provider"),
            "routing_tier": request.get("routing_tier"),
            "evidence_context": request.get("evidence_context", {}),
            "changed_files": request.get("changed_files", []),
            "validation_errors": request.get("validation_errors", []),
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-4000:],
        }
        if baseline.is_file():
            shutil.copy2(baseline, artifact_dir / f"{artifact_prefix}.md")
        (artifact_dir / f"{artifact_prefix}.meta.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if status["status"] != "completed":
            shutil.copytree(workspace, failed_workspace)
        return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library",
        type=Path,
        default=PROJECT_ROOT / "student-error-library",
    )
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--entry", action="append", dest="entries")
    parser.add_argument("--only-with-direct", action="store_true")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--node", default=os.environ.get("E2E_NODE") or shutil.which("node"))
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--evidence-mode",
        choices=tuple(EVIDENCE_COHORTS),
        default="current",
        help="Use the current RAG snapshot or an otherwise identical empty evidence pack.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.node:
        raise SystemExit("Node.js is required for the browser benchmark")
    library = args.library.expanduser().resolve()
    experiment = args.experiment.expanduser().resolve()
    manifest = load_json(experiment / "manifest.json")
    manifest_ids = [str(case.get("entry_id", "")) for case in manifest.get("cases", [])]
    selected = args.entries or manifest_ids
    selected = [entry_id for entry_id in selected if entry_id in manifest_ids]
    if args.only_with_direct:
        selected = [
            entry_id
            for entry_id in selected
            if (experiment / "artifacts" / entry_id / "direct.md").is_file()
        ]
    if args.max_cases > 0:
        selected = selected[: args.max_cases]
    results = []
    for entry_id in selected:
        result = run_case(
            source_library=library,
            experiment=experiment,
            entry_id=entry_id,
            node=str(args.node),
            timeout_seconds=max(60, args.timeout_seconds),
            evidence_mode=args.evidence_mode,
        )
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if results and all(item["status"] == "completed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
