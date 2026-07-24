#!/usr/bin/env python3
"""Run the isolated teacher-console browser/API E2E flow."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SCRIPTS))

import kb  # noqa: E402
import model_registry  # noqa: E402
import server as teacher_server  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402

SCENARIOS = (
    "lifecycle.e2e.mjs",
    "visualization.e2e.mjs",
    "publication.e2e.mjs",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", help="Node.js executable; defaults to E2E_NODE or PATH")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(name.removesuffix(".e2e.mjs") for name in SCENARIOS),
        help="Run only the named scenario; repeat to select multiple",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=ROOT / "test-results" / "e2e",
        help="Screenshots and JSON reports directory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    node = args.node or os.environ.get("E2E_NODE") or shutil.which("node")
    if not node:
        raise SystemExit("Node.js is required for the Playwright E2E test")

    artifacts = args.artifacts.expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    for legacy_name in (
        "delivered.png",
        "evaluation.json",
        "failure.json",
        "failure.png",
        "failed-workspace",
        "lifecycle-summary.json",
        "pipeline-quality.json",
    ):
        legacy = artifacts / legacy_name
        if legacy.is_dir():
            shutil.rmtree(legacy)
        elif legacy.exists():
            legacy.unlink()
    adapter = Path(__file__).with_name("fake_agent_adapter.py")
    failures: list[str] = []
    selected = set(args.scenario or ())
    scenarios = tuple(
        script_name for script_name in SCENARIOS if not selected or script_name.removesuffix(".e2e.mjs") in selected
    )

    for script_name in scenarios:
        scenario = script_name.removesuffix(".e2e.mjs")
        scenario_artifacts = artifacts / scenario
        if scenario_artifacts.exists():
            shutil.rmtree(scenario_artifacts)
        scenario_artifacts.mkdir(parents=True)
        browser_test = Path(__file__).with_name(script_name)
        print(f"\n=== E2E scenario: {scenario} ===", flush=True)

        with tempfile.TemporaryDirectory(prefix=f"wuli-e2e-{scenario}-") as temp_name:
            workspace = Path(temp_name)
            library = workspace / "student-error-library"
            uploads = workspace / "error-collection"
            public_site = workspace / "student-site"
            fixture = workspace / "question-source.png"
            image = Image.new("RGB", (640, 360), "white")
            drawing = ImageDraw.Draw(image)
            drawing.rectangle((0, 0, 640, 72), fill=(44, 62, 80))
            drawing.rectangle((35, 110, 605, 325), outline=(40, 80, 140), width=5)
            drawing.line((90, 260, 510, 160), fill=(190, 45, 45), width=8)
            image.save(fixture, "PNG")
            kb.init_library(library)

            teacher_server.LIBRARY = library
            teacher_server.UPLOADS = uploads
            teacher_server.PUBLIC_SITE = public_site
            teacher_server.MODEL_REGISTRY_PATH = library / "config" / "model-registry.json"
            model_registry.LIBRARY = library

            agent_environment = dict(os.environ)
            agent_environment.update({
                "TEACHER_CONSOLE_AGENT_PROVIDER": "adapter",
                "TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": shlex.join([sys.executable, str(adapter)]),
            })
            teacher_server.AGENT_GATEWAY = AgentGateway(environ=agent_environment)
            teacher_server._JOB_MANAGER = AgentJobManager(library / ".cache" / "agent-jobs", max_workers=1)

            httpd = ThreadingHTTPServer(("127.0.0.1", 0), teacher_server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{httpd.server_port}"

            child_environment = dict(os.environ)
            child_environment.update({
                "E2E_BASE_URL": base_url,
                "E2E_LIBRARY": str(library),
                "E2E_PUBLIC_SITE": str(public_site),
                "E2E_PROJECT_ROOT": str(ROOT),
                "E2E_PYTHON": sys.executable,
                "E2E_ARTIFACT_DIR": str(scenario_artifacts),
                "E2E_FIXTURE_IMAGE": str(fixture),
            })
            try:
                completed = subprocess.run(
                    [node, str(browser_test)],
                    cwd=ROOT,
                    env=child_environment,
                    check=False,
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)
                teacher_server._JOB_MANAGER.shutdown(wait=True)
                teacher_server._JOB_MANAGER = None

            if completed.returncode != 0:
                failures.append(scenario)
                failed_workspace = scenario_artifacts / "failed-workspace"
                shutil.copytree(workspace, failed_workspace)
                print(
                    f"E2E {scenario} failed; isolated workspace copied to {failed_workspace}",
                    file=sys.stderr,
                )

    if failures:
        print(f"E2E failures: {', '.join(failures)}", file=sys.stderr)
        return 1
    print(f"\nAll {len(scenarios)} E2E scenarios passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
