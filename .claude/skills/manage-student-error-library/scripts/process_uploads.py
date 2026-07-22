#!/usr/bin/env python3
"""Coordinate upload → analysis handoff → answer → PDF/simulator → delivery."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import kb
import source_review


SKILL_DIR = Path(__file__).resolve().parent.parent
BUILD_SKILL = SKILL_DIR.parent / "build-physics-simulator"
VISUALIZATION_DIR = "visualization"


def write_json(path: Path, value: dict) -> None:
    kb.write_json(path, value)


def pipeline_state(entry: Path) -> dict:
    record = kb.load_json(entry / "record.json", {})
    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").exists() else ""
    solution = (entry / "solution.md").read_text(encoding="utf-8") if (entry / "solution.md").exists() else ""
    pending = any(marker in problem or marker in solution for marker in kb.PENDING_MARKERS)
    review = kb.load_json(entry / "source-review.json", {})
    answer_review = kb.load_json(entry / "answer-review.json", record.get("answer_review", {}))
    model_path = entry / "physics-model.json"
    has_model = model_path.exists()
    visualization = visualization_snapshot(entry)
    visualization_review = visualization["review"]
    # The standalone visualization stage is reserved for a model-backed,
    # interactive simulation. Static SVG/PNG explanations belong to answer
    # review and are already covered by answer_artifact_digest().
    visualization_required = has_model
    answer_review_stale = (
        answer_review.get("status") == "passed"
        and answer_review.get("answer_digest") != answer_digest(entry)
    )
    visualization_review_stale = visualization["review_stale"]
    delivered_valid = (
        record.get("status") == "ready"
        and (entry / "delivery.json").exists()
        and not answer_review_stale
        and (
            not visualization_required
            or (
                not visualization_review_stale
                and visualization_review.get("status") in {"passed", "legacy-not-recorded"}
            )
        )
    )
    if delivered_valid:
        state = "delivered"
    elif record.get("ocr", {}).get("review_required") or "[待核对]" in problem:
        state = "needs-source-review"
    elif pending or len(solution.strip()) < 100 or answer_review.get("status") == "revision-requested":
        state = "needs-analysis-and-answer"
    elif answer_review.get("status") in {"not-ready", "needs-review"} or answer_review_stale:
        state = "needs-answer-review"
    elif has_model and not visualization["build_current"]:
        state = "needs-visualization-build"
    elif visualization_required and (visualization_review.get("status") != "passed" or visualization_review_stale):
        state = "needs-visualization-review"
    else:
        state = "ready-to-finish"
    return {
        "entry_id": entry.name,
        "state": state,
        "entry": str(entry.resolve()),
        "has_physics_model": (entry / "physics-model.json").exists(),
        "next_action": {
            "needs-source-review": (
                "Use the visual adapter or inspect source-review.md and original assets, correct problem.md, then run "
                f"process_uploads.py approve-source {entry.name} --reviewer <name-or-role>."
            ),
            "needs-analysis-and-answer": "Solve, classify, add an explanatory asset, and write layered answers. Do not create an interactive model unless the teacher explicitly requests one.",
            "needs-answer-review": f"Inspect the student/teacher answers, then approve or request revision for {entry.name}.",
            "needs-visualization-build": f"Build or refresh the reviewed visualization for {entry.name}.",
            "needs-visualization-review": f"Build and inspect the simulator, then approve the current physics model for {entry.name}.",
            "ready-to-finish": f"Run process_uploads.py finish {entry.name} --simulator auto.",
            "delivered": "Use delivery.json and the output directory.",
        }[state],
        "source_review": {
            "status": review.get("status", "not-run"),
            "method": review.get("method"),
            "review_packet": review.get("review_packet"),
            "uncertainties": review.get("uncertainties", []),
        },
        "answer_review": {
            "status": "stale" if answer_review_stale else answer_review.get("status", "legacy-not-recorded"),
            "reviewer": answer_review.get("reviewer"),
            "note": answer_review.get("note", ""),
        },
        "visualization_review": {
            "status": "stale" if visualization_review_stale else visualization_review.get("status", "not-required" if not has_model else "not-ready"),
            "reviewer": visualization_review.get("reviewer"),
            "note": visualization_review.get("note", ""),
        },
        "visualization": visualization,
    }


def delivery_files(output_dir: Path) -> list[str]:
    return [
        str(path.relative_to(output_dir))
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(output_dir).parts)
    ]


def resolve_review_options(
    root: Path,
    mode: str,
    vision_capability: str,
    command: str | None,
    locality: str | None,
) -> dict:
    config = kb.load_json(root / "config.json", {})
    configured = config.get("source_review", {})
    privacy = config.get("privacy", {})
    resolved_command = command if command is not None else str(configured.get("adapter_command", ""))
    resolved_locality = locality or str(configured.get("adapter_locality", "local"))
    resolved_mode = mode if mode != "auto" else str(configured.get("mode", "auto"))
    if resolved_mode == "auto":
        if resolved_command:
            resolved_mode = "adapter"
        elif vision_capability == "available":
            resolved_mode = "agent"
        else:
            resolved_mode = "human"
    if resolved_mode == "agent" and vision_capability != "available":
        resolved_mode = "human"
    return {
        "mode": resolved_mode,
        "vision_capability": vision_capability,
        "command": resolved_command,
        "locality": resolved_locality,
        "allow_remote": bool(privacy.get("allow_remote_visual_review", False)),
    }


def review_entry_source(entry: Path, options: dict) -> dict:
    mode = options["mode"]
    if mode == "adapter":
        if not options["command"]:
            return source_review.prepare_review(entry, "visual adapter mode selected but no adapter command is configured")
        try:
            return source_review.run_adapter(entry, options["command"], options["locality"], options["allow_remote"])
        except Exception as exc:  # noqa: BLE001 - preserve safe human fallback
            packet = source_review.prepare_review(entry, f"visual adapter failed: {exc}")
            packet["adapter_error"] = str(exc)
            kb.write_json(entry / "source-review.json", packet)
            return packet
    if mode == "agent":
        return source_review.prepare_review(entry, "vision-capable agent must inspect the original before approval")
    return source_review.prepare_review(entry, "reasoning model has no image capability; human visual review required")


def start(
    root: Path,
    input_path: Path,
    ocr: str,
    ocr_command: str | None,
    subject: str,
    review_mode: str,
    vision_capability: str,
    visual_review_command: str | None,
    adapter_locality: str | None,
) -> dict:
    kb.init_library(root)
    review_options = resolve_review_options(root, review_mode, vision_capability, visual_review_command, adapter_locality)
    inputs = kb.discover_inputs(input_path)
    results: list[dict] = []
    for item in inputs:
        if item.suffix.lower() == ".pdf":
            results.extend(kb.ingest_pdf_pages(root, item, ocr, ocr_command, subject))
        else:
            results.append(kb.ingest_one(root, item, ocr, ocr_command, subject, None))
    orders = []
    for result in results:
        entry_id = result.get("entry_id")
        if not entry_id:
            continue
        entry = root / "entries" / entry_id
        if result.get("status") == "ingested":
            result["source_review"] = review_entry_source(entry, review_options)
        state = pipeline_state(entry)
        pipeline_path = entry / "pipeline.json"
        if result.get("status") == "ingested" or not pipeline_path.exists():
            pipeline = {
                "schema_version": 1,
                "entry_id": entry_id,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "state": state["state"],
                "steps": ["ingest", "source-review", "analysis", "answer-review", "visualization-build", "visualization-review", "validate", "deliver"],
            }
            write_json(pipeline_path, pipeline)
        orders.append(state)
    folders = kb.sync_library_folders(root)
    kb.rebuild_index(root)
    return {
        "status": "started",
        "inputs": len(inputs),
        "review_mode": review_options["mode"],
        "vision_capability": review_options["vision_capability"],
        "results": results,
        "work_orders": orders,
        "folders": folders,
    }


def approve_source(root: Path, entry_id: str, reviewer: str, note: str) -> dict:
    entry = root / "entries" / entry_id
    try:
        report = source_review.approve_source(entry, reviewer, note)
    except Exception as exc:  # noqa: BLE001 - return an auditable blocked state
        return {"status": "blocked", "entry_id": entry_id, "errors": [str(exc)], "state": pipeline_state(entry) if entry.exists() else None}
    pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry_id})
    state = pipeline_state(entry)
    pipeline.update({"state": state["state"], "source_review": report})
    write_json(entry / "pipeline.json", pipeline)
    kb.rebuild_index(root)
    return {"status": "approved", "source_review": report, "state": state}


def answer_digest(entry: Path) -> str:
    return kb.answer_artifact_digest(entry)


def visualization_artifact_digest(entry: Path) -> str:
    digest = hashlib.sha256()
    paths: list[Path] = []
    model = entry / "physics-model.json"
    if model.exists():
        paths.append(model)
    visual_dir = entry / VISUALIZATION_DIR
    for name in ("physics-simulator.html", "physics-simulator.zip", "runtime-check.png", "simulation-build.json"):
        path = visual_dir / name
        if path.exists():
            paths.append(path)
    for path in paths:
        digest.update(str(path.resolve().relative_to(entry.resolve())).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def visualization_snapshot(entry: Path) -> dict:
    record = kb.load_json(entry / "record.json", {})
    model_path = entry / "physics-model.json"
    visual_dir = entry / VISUALIZATION_DIR
    build = kb.load_json(visual_dir / "simulation-build.json", record.get("visualization_build", {}))
    attempt = kb.load_json(entry / "visualization-build-attempt.json", {})
    current_model_digest = kb.sha256_file(model_path) if model_path.exists() else None
    failed_current_attempt = bool(
        attempt
        and attempt.get("status") != "ok"
        and attempt.get("model_digest") == current_model_digest
        and str(attempt.get("built_at", "")) >= str(build.get("built_at", ""))
    )
    build_current = bool(
        model_path.exists()
        and build.get("status") == "ok"
        and build.get("model_digest") == current_model_digest
        and (visual_dir / "physics-simulator.html").is_file()
        and not failed_current_attempt
    )
    kind = "simulator" if model_path.exists() else "not-generated"
    digest = visualization_artifact_digest(entry)
    review = kb.load_json(entry / "visualization-review.json", record.get("visualization_review", {}))
    if not model_path.exists():
        review = {"status": "not-required"}
    review_stale = bool(model_path.exists() and review.get("status") == "passed" and review.get("artifact_digest") != digest)
    return {
        "kind": kind,
        "has_model": model_path.exists(),
        "model_digest": current_model_digest,
        "build": attempt if failed_current_attempt or not build else build,
        "build_current": build_current,
        "artifact_digest": digest,
        "review": dict(review, status="stale") if review_stale else review,
        "review_stale": review_stale,
        "html": str(visual_dir / "physics-simulator.html") if build_current else None,
    }


def should_render_answers(model_path: Path) -> bool:
    model = kb.load_json(model_path, {})
    return model.get("source", {}).get("answer_render_mode", "model") != "manual"


def build_simulator(entry: Path, output_dir: Path, runtime_mode: str = "auto") -> dict:
    model_path = entry / "physics-model.json"
    if not model_path.exists():
        return {"status": "blocked", "errors": ["physics-model.json is missing"]}
    command = [
        sys.executable,
        str(BUILD_SKILL / "scripts" / "build_simulator.py"),
        str(model_path),
        "--entry-dir", str(entry),
        "--output-dir", str(output_dir),
        "--name", "physics-simulator",
        "--title", str(kb.load_json(entry / "record.json", {}).get("title", "物理仿真")),
        "--zip",
        "--runtime-check", runtime_mode,
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError:
        report = {"status": "failed", "stderr": result.stderr.strip()[:1000]}
    report["model_digest"] = kb.sha256_file(model_path)
    report["built_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "simulation-build.json", report)
    return report


def _rebase_report_paths(value, old_root: Path, new_root: Path):
    if isinstance(value, dict):
        return {key: _rebase_report_paths(item, old_root, new_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase_report_paths(item, old_root, new_root) for item in value]
    if isinstance(value, str) and value.startswith(str(old_root)):
        return str(new_root) + value[len(str(old_root)):]
    return value


def prepare_visualization(root: Path, entry_id: str, runtime_mode: str = "auto") -> dict:
    """Build once for teacher review; the approved bytes are later copied to delivery."""
    entry = root / "entries" / entry_id
    if not entry.exists():
        return {"status": "blocked", "errors": [f"entry not found: {entry_id}"]}
    if not (entry / "physics-model.json").exists():
        return {
            "status": "needs-model",
            "errors": ["physics-model.json is missing; explicitly ask an Agent to invoke build-physics-simulator first"],
            "visualization": visualization_snapshot(entry),
            "state": pipeline_state(entry),
        }
    with tempfile.TemporaryDirectory(prefix=".visualization-build-", dir=entry) as temp_name:
        staged = Path(temp_name) / "simulation"
        report = build_simulator(entry, staged, runtime_mode)
        write_json(entry / "visualization-build-attempt.json", report)
        if report.get("status") != "ok":
            recorded = record_visualization_build(root, entry_id, report)
            recorded["errors"] = report.get("errors", [f"visualization build status: {report.get('status', 'failed')}"])
            return recorded
        target = entry / VISUALIZATION_DIR
        previous = entry / ".visualization-previous"
        if previous.exists():
            shutil.rmtree(previous, ignore_errors=True)
        if target.exists():
            target.rename(previous)
        try:
            staged.rename(target)
        except Exception:
            if previous.exists() and not target.exists():
                previous.rename(target)
            raise
        finally:
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)
        report = _rebase_report_paths(report, staged, target)
        write_json(target / "simulation-build.json", report)
    return record_visualization_build(root, entry_id, report)


def record_visualization_build(root: Path, entry_id: str, report: dict) -> dict:
    entry = root / "entries" / entry_id
    record = kb.load_json(entry / "record.json", {})
    current = kb.load_json(entry / "visualization-review.json", record.get("visualization_review", {}))
    snapshot = visualization_snapshot(entry)
    same_approved_artifact = (
        report.get("status") == "ok"
        and current.get("status") == "passed"
        and current.get("artifact_digest") == snapshot["artifact_digest"]
    )
    review = current if same_approved_artifact else {
        "schema_version": 1,
        "entry_id": entry_id,
        "status": "needs-review",
        "model_digest": report.get("model_digest"),
        "artifact_digest": snapshot["artifact_digest"],
        "note": "可视化已构建，等待教师复核" if report.get("status") == "ok" else "可视化构建未通过，请查看错误或请求 Agent 修复",
    }
    record["visualization_build"] = {
        "status": report.get("status"),
        "model_digest": report.get("model_digest"),
        "built_at": report.get("built_at"),
        "runtime_check": report.get("runtime_check", {}),
        "artifact_digest": snapshot["artifact_digest"],
    }
    record["visualization_review"] = review
    record["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_json(entry / "record.json", record)
    write_json(entry / "visualization-review.json", review)
    state = pipeline_state(entry)
    pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry_id})
    pipeline.update({"state": state["state"], "visualization_build": record["visualization_build"], "visualization_review": review})
    write_json(entry / "pipeline.json", pipeline)
    return {"status": report.get("status"), "build": report, "review": review, "state": state}


def approve_visualization(root: Path, entry_id: str, reviewer: str, note: str) -> dict:
    entry = root / "entries" / entry_id
    if not entry.exists():
        return {"status": "blocked", "errors": [f"entry not found: {entry_id}"]}
    record = kb.load_json(entry / "record.json", {})
    snapshot = visualization_snapshot(entry)
    if not snapshot["has_model"]:
        return {
            "status": "blocked",
            "errors": ["interactive visualization is not applicable without physics-model.json"],
            "state": pipeline_state(entry),
        }
    if snapshot["has_model"] and not snapshot["build_current"]:
        return {"status": "blocked", "errors": ["build or refresh the current visualization before approval"], "state": pipeline_state(entry)}
    if not reviewer.strip():
        return {"status": "blocked", "errors": ["reviewer is required"]}
    reviewed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report = {
        "schema_version": 1,
        "entry_id": entry_id,
        "status": "passed",
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "kind": snapshot["kind"],
        "model_digest": snapshot["model_digest"],
        "artifact_digest": snapshot["artifact_digest"],
        "note": note.strip(),
    }
    record["visualization_review"] = report
    record["updated_at"] = reviewed_at
    write_json(entry / "record.json", record)
    write_json(entry / "visualization-review.json", report)
    state = pipeline_state(entry)
    pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry_id})
    pipeline.update({"state": state["state"], "visualization_review": report})
    write_json(entry / "pipeline.json", pipeline)
    return {"status": "approved", "visualization_review": report, "state": state}


def approve_answer(root: Path, entry_id: str, reviewer: str, note: str) -> dict:
    entry = root / "entries" / entry_id
    if not entry.exists():
        return {"status": "blocked", "errors": [f"entry not found: {entry_id}"]}
    errors = kb.validate_entry(root, entry, ready_rules=True, require_answer_review=False)
    if errors:
        return {"status": "blocked", "errors": errors, "state": pipeline_state(entry)}
    reviewed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report = {
        "schema_version": 1,
        "entry_id": entry_id,
        "status": "passed",
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "answer_digest": answer_digest(entry),
        "note": note.strip(),
    }
    if not report["reviewer"]:
        return {"status": "blocked", "errors": ["reviewer is required"]}
    record = kb.load_json(entry / "record.json", {})
    record["answer_review"] = report
    record["updated_at"] = reviewed_at
    write_json(entry / "record.json", record)
    write_json(entry / "answer-review.json", report)
    pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry_id})
    state = pipeline_state(entry)
    pipeline.update({"state": state["state"], "answer_review": report})
    write_json(entry / "pipeline.json", pipeline)
    return {"status": "approved", "answer_review": report, "state": state}


def request_answer_revision(root: Path, entry_id: str, reviewer: str, note: str) -> dict:
    entry = root / "entries" / entry_id
    if not entry.exists():
        return {"status": "blocked", "errors": [f"entry not found: {entry_id}"]}
    if not reviewer.strip() or not note.strip():
        return {"status": "blocked", "errors": ["reviewer and revision note are required"]}
    reviewed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report = {
        "schema_version": 1,
        "entry_id": entry_id,
        "status": "revision-requested",
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "answer_digest": answer_digest(entry),
        "note": note.strip(),
    }
    record = kb.load_json(entry / "record.json", {})
    record["answer_review"] = report
    record["answer_status"] = "pending"
    record["updated_at"] = reviewed_at
    write_json(entry / "record.json", record)
    write_json(entry / "answer-review.json", report)
    pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry_id})
    state = pipeline_state(entry)
    pipeline.update({"state": state["state"], "answer_review": report})
    write_json(entry / "pipeline.json", pipeline)
    return {"status": "revision-requested", "answer_review": report, "state": state}


def build_student_package(entry: Path, output_dir: Path, export_result: dict, simulation: dict | None) -> Path:
    problem = (entry / "problem.md").read_text(encoding="utf-8")
    answer_path = entry / "student-solution.md"
    answer = answer_path.read_text(encoding="utf-8") if answer_path.exists() else (entry / "solution.md").read_text(encoding="utf-8")
    combined = f"{problem}\n\n---\n\n{answer}"
    package = output_dir / "student-package.zip"
    package_manifest = {"schema_version": 1, "entry_id": entry.name, "files": ["student-answer.md"]}
    packaged_assets: list[tuple[Path, str]] = []
    assets = entry / "assets"
    if assets.exists():
        for index, asset in enumerate(sorted(assets.iterdir()), 1):
            if not asset.is_file() or asset.name.startswith(".") or asset.name.endswith(".fragment.html"):
                continue
            safe_name = asset.name if asset.name.isascii() else f"asset-{index}{asset.suffix.lower()}"
            combined = combined.replace(f"assets/{asset.name}", f"assets/{safe_name}")
            packaged_assets.append((asset, safe_name))
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("student-answer.md", combined)
        for asset, safe_name in packaged_assets:
            archive.write(asset, f"assets/{safe_name}")
            package_manifest["files"].append(f"assets/{safe_name}")
        pdf = output_dir / "带答案错题.pdf"
        if pdf.exists():
            archive.write(pdf, "student-answer.pdf")
            package_manifest["files"].append("student-answer.pdf")
        if simulation and simulation.get("status") == "ok":
            html = Path(simulation["artifacts"]["html"])
            archive.write(html, "physics-simulator.html")
            package_manifest["files"].append("physics-simulator.html")
        archive.writestr("package-manifest.json", json.dumps(package_manifest, ensure_ascii=False, indent=2))
    return package


def finish(root: Path, entry_id: str, output_base: Path | None, simulator: str) -> dict:
    entry = root / "entries" / entry_id
    if not entry.exists():
        return {"status": "blocked", "errors": [f"entry not found: {entry_id}"]}
    model_path = entry / "physics-model.json"
    if not model_path.exists() and simulator == "required":
        return {"status": "blocked", "errors": ["simulator required but physics-model.json is missing"]}
    record = kb.load_json(entry / "record.json", {})
    answer_review = record.get("answer_review")
    if answer_review is not None:
        if answer_review.get("status") != "passed":
            return {"status": "blocked", "errors": ["teacher answer review has not passed"], "state": pipeline_state(entry)}
        if answer_review.get("answer_digest") != answer_digest(entry):
            return {"status": "blocked", "errors": ["answer changed after teacher approval; review it again"], "state": pipeline_state(entry)}
    visualization = visualization_snapshot(entry)
    visualization_required = model_path.exists()
    if visualization_required and not (model_path.exists() and simulator == "skip"):
        visualization_review = kb.load_json(entry / "visualization-review.json", record.get("visualization_review", {}))
        if visualization_review.get("status") != "passed":
            return {"status": "blocked", "errors": ["teacher visualization review has not passed"], "state": pipeline_state(entry)}
        if visualization_review.get("artifact_digest") != visualization["artifact_digest"]:
            return {"status": "blocked", "errors": ["visualization changed after teacher approval; review it again"], "state": pipeline_state(entry)}
        if model_path.exists() and not visualization["build_current"]:
            return {"status": "blocked", "errors": ["approved visualization build is missing or stale"], "state": pipeline_state(entry)}
    errors = kb.finalize_entry(root, entry_id)
    if errors:
        return {"status": "blocked", "state": pipeline_state(entry), "errors": errors}
    exported = kb.export_entry(root, entry_id, output_base)
    if exported.get("error"):
        return {"status": "blocked", "errors": [exported["error"]], "export": exported}
    output_dir = Path(exported["output"])
    simulation = None
    if model_path.exists() and simulator != "skip":
        reviewed_dir = entry / VISUALIZATION_DIR
        delivery_dir = output_dir / "simulation"
        shutil.copytree(reviewed_dir, delivery_dir)
        simulation = kb.load_json(delivery_dir / "simulation-build.json", {})
        simulation = _rebase_report_paths(simulation, reviewed_dir, delivery_dir)
        write_json(delivery_dir / "simulation-build.json", simulation)
    package = build_student_package(entry, output_dir, exported, simulation)
    manifest = {
        "schema_version": 1,
        "entry_id": entry_id,
        "status": "delivered",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "entry": str(entry.resolve()),
        "output": str(output_dir.resolve()),
        "answer_validation": "passed",
        "source_review": kb.load_json(entry / "record.json", {}).get("source_review", {"status": "legacy-not-recorded"}),
        "answer_review": kb.load_json(entry / "record.json", {}).get("answer_review", {"status": "legacy-not-recorded"}),
        "visualization_review": (
            kb.load_json(entry / "record.json", {}).get("visualization_review", {"status": "not-ready"})
            if model_path.exists()
            else {"status": "not-required"}
        ),
        "pdf": exported.get("pdf"),
        "simulation": simulation or {"status": "not-generated"},
        "runtime_check": simulation.get("runtime_check", {"status": "not-generated"}) if simulation else {"status": "not-generated"},
        "student_package": str(package.resolve()),
        "files": delivery_files(output_dir),
    }
    manifest_path = output_dir / "delivery-manifest.json"
    write_json(manifest_path, manifest)
    manifest["files"] = delivery_files(output_dir)
    write_json(manifest_path, manifest)
    write_json(entry / "delivery.json", manifest)
    pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry_id})
    pipeline.update({"state": "delivered", "completed_at": manifest["generated_at"], "delivery_manifest": str((output_dir / "delivery-manifest.json").resolve())})
    write_json(entry / "pipeline.json", pipeline)
    kb.rebuild_index(root)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    commands = parser.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start")
    start_parser.add_argument("--input", type=Path, default=Path.cwd() / "error-collection")
    start_parser.add_argument("--ocr", choices=("auto", "vision", "command", "none"), default="auto")
    start_parser.add_argument("--ocr-command")
    start_parser.add_argument("--subject", default="高中物理")
    start_parser.add_argument("--source-review-mode", choices=("auto", "agent", "human", "adapter"), default="auto")
    start_parser.add_argument("--vision-capability", choices=("auto", "available", "unavailable"), default="auto")
    start_parser.add_argument("--visual-review-command")
    start_parser.add_argument("--adapter-locality", choices=("local", "remote"))
    status_parser = commands.add_parser("status")
    status_parser.add_argument("entry_id", nargs="?")
    approve_parser = commands.add_parser("approve-source")
    approve_parser.add_argument("entry_id")
    approve_parser.add_argument("--reviewer", required=True)
    approve_parser.add_argument("--note", default="")
    approve_answer_parser = commands.add_parser("approve-answer")
    approve_answer_parser.add_argument("entry_id")
    approve_answer_parser.add_argument("--reviewer", required=True)
    approve_answer_parser.add_argument("--note", default="")
    revise_answer_parser = commands.add_parser("request-answer-revision")
    revise_answer_parser.add_argument("entry_id")
    revise_answer_parser.add_argument("--reviewer", required=True)
    revise_answer_parser.add_argument("--note", required=True)
    build_visualization_parser = commands.add_parser("prepare-visualization")
    build_visualization_parser.add_argument("entry_id")
    build_visualization_parser.add_argument("--runtime-check", choices=("auto", "required", "skip"), default="auto")
    approve_visualization_parser = commands.add_parser("approve-visualization")
    approve_visualization_parser.add_argument("entry_id")
    approve_visualization_parser.add_argument("--reviewer", required=True)
    approve_visualization_parser.add_argument("--note", default="")
    finish_parser = commands.add_parser("finish")
    finish_parser.add_argument("entry_id")
    finish_parser.add_argument("--output", type=Path)
    finish_parser.add_argument("--simulator", choices=("auto", "required", "skip"), default="auto")
    args = parser.parse_args()
    root = args.library.expanduser().resolve()
    if args.command == "start":
        report = start(
            root,
            args.input.expanduser().resolve(),
            args.ocr,
            args.ocr_command,
            args.subject,
            args.source_review_mode,
            args.vision_capability,
            args.visual_review_command,
            args.adapter_locality,
        )
    elif args.command == "status":
        entries = [root / "entries" / args.entry_id] if args.entry_id else list(kb.entry_dirs(root))
        report = {"entries": [pipeline_state(entry) for entry in entries if entry.exists()]}
    elif args.command == "approve-source":
        report = approve_source(root, args.entry_id, args.reviewer, args.note)
    elif args.command == "approve-answer":
        report = approve_answer(root, args.entry_id, args.reviewer, args.note)
    elif args.command == "request-answer-revision":
        report = request_answer_revision(root, args.entry_id, args.reviewer, args.note)
    elif args.command == "prepare-visualization":
        report = prepare_visualization(root, args.entry_id, args.runtime_check)
    elif args.command == "approve-visualization":
        report = approve_visualization(root, args.entry_id, args.reviewer, args.note)
    else:
        report = finish(root, args.entry_id, args.output.resolve() if args.output else None, args.simulator)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
