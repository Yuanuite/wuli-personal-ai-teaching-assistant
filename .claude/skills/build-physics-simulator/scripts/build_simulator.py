#!/usr/bin/env python3
"""Build and validate one offline simulator from physics-model.json."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
RENDERERS = {
    "concentric-radial-multi-field": SKILL_DIR / "assets" / "guided-charged-particle-template.html",
    "opposite-circular-magnetic": SKILL_DIR / "assets" / "field-trajectory-template.html",
    "electric-to-bounded-magnetic": SKILL_DIR / "assets" / "field-trajectory-template.html",
}


def run(command: list[str], env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(command, text=True, capture_output=True, check=False, env=env)
    return {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}


def parsed_output(result: dict) -> dict:
    try:
        return json.loads(result["stdout"])
    except (json.JSONDecodeError, TypeError):
        return {"stdout": result.get("stdout", ""), "stderr": result.get("stderr", "")}


def runtime_check(html: str, output_dir: Path, mode: str) -> dict:
    if mode == "skip":
        return {"status": "skipped", "reason": "explicitly skipped by caller"}
    node = os.environ.get("PHYSICS_SIMULATOR_NODE") or shutil.which("node")
    if not node:
        result = {"status": "skipped", "reason": "Node.js is unavailable"}
        if mode == "required":
            result["status"] = "failed"
        return result
    screenshot = output_dir / "runtime-check.png"
    environment = os.environ.copy()
    node_modules = os.environ.get("PHYSICS_SIMULATOR_NODE_MODULES")
    if node_modules:
        environment["NODE_PATH"] = node_modules
    checked = run([node, str(SKILL_DIR / "scripts" / "browser_check.mjs"), html, "--screenshot", str(screenshot)], env=environment)
    report = parsed_output(checked)
    if checked["returncode"] == 2:
        report.setdefault("status", "skipped")
        report.setdefault("reason", checked.get("stderr") or "browser dependency unavailable")
        if mode == "required":
            report["status"] = "failed"
            report["reason"] = f"required runtime check unavailable: {report.get('reason', 'unknown reason')}"
    elif checked["returncode"]:
        report["status"] = "failed"
    else:
        report["status"] = "passed"
    if not screenshot.exists():
        report["screenshot"] = None
    return report


def render_fragment(model: dict, template: Path, output: Path) -> None:
    source = template.read_text(encoding="utf-8")
    marker = "__PHYSICS_MODEL_JSON__"
    if marker not in source:
        raise ValueError(f"template missing {marker}")
    embedded = json.dumps(model, ensure_ascii=False).replace("</", "<\\/")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source.replace(marker, embedded), encoding="utf-8")


def build(model_path: Path, entry_dir: Path, output_dir: Path, name: str, title: str, make_zip: bool, runtime_mode: str) -> dict:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    template = RENDERERS.get(model.get("model_type"))
    if not template:
        return {"status": "unsupported", "model_type": model.get("model_type"), "supported": sorted(RENDERERS)}
    validation = run([sys.executable, str(SKILL_DIR / "scripts" / "validate_physics_model.py"), str(model_path)])
    if validation["returncode"]:
        return {"status": "invalid-model", "validation": validation}
    fragment = entry_dir / "assets" / f"{name}.fragment.html"
    render_fragment(model, template, fragment)
    command = [sys.executable, str(SKILL_DIR / "scripts" / "export_offline.py"), str(fragment), "--output-dir", str(output_dir), "--name", name, "--title", title]
    if make_zip:
        command.append("--zip")
    exported = run(command)
    if exported["returncode"]:
        return {"status": "export-failed", "export": exported}
    artifacts = json.loads(exported["stdout"])
    check = [sys.executable, str(SKILL_DIR / "scripts" / "validate_simulator.py"), artifacts["html"]]
    if artifacts.get("zip"):
        check.extend(["--zip", artifacts["zip"]])
    verified = run(check)
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime = runtime_check(artifacts["html"], output_dir, runtime_mode) if not verified["returncode"] else {"status": "not-run", "reason": "static validation failed"}
    successful = not verified["returncode"] and runtime.get("status") in {"passed", "skipped"}
    report = {
        "status": "ok" if successful else "validation-failed",
        "model": str(model_path),
        "artifacts": artifacts,
        "model_validation": parsed_output(validation),
        "simulator_validation": parsed_output(verified),
        "runtime_check": runtime,
    }
    (output_dir / "simulation-build.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--entry-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--name", default="physics-simulator")
    parser.add_argument("--title")
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--runtime-check", choices=("auto", "required", "skip"), default="auto")
    args = parser.parse_args()
    model = json.loads(args.model.read_text(encoding="utf-8"))
    report = build(args.model.resolve(), args.entry_dir.resolve(), args.output_dir.resolve(), args.name, args.title or model.get("title", "物理过程仿真"), args.zip, args.runtime_check)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
