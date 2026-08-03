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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import kb  # noqa: E402
import model_registry  # noqa: E402
import server as teacher_server  # noqa: E402
import slow_mock_server  # noqa: E402
import truncation_mock_server  # noqa: E402
import visual_mock_server  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402

SCENARIOS = (
    "lifecycle.e2e.mjs",
    "visualization.e2e.mjs",
    "publication.e2e.mjs",
    "claim-evidence.e2e.mjs",
    "visual-web-clear.e2e.mjs",
    "visual-cli-clear.e2e.mjs",
    "visual-blurred-fail-closed.e2e.mjs",
    "static-diagram-collaboration.e2e.mjs",
    "runtime-route-rollback.e2e.mjs",
    # Wave E2/E3: analysis-run observability
    # (docs/archive/2026-08-03/analysis-run-observability-w3-pipeline-work-tree.md)
    "analysis-core-report.e2e.mjs",
    "analysis-core-truncated.e2e.mjs",
    "analysis-w3-report.e2e.mjs",
    "analysis-w3-backjump.e2e.mjs",
    # Wave 5: provider qualification + deadline budget + route preview
    # (docs/archive/2026-08-03/analysis-provider-timeout-repair-work-tree.md, A2.2/A2.4/A4.1)
    "analysis-qualified-route.e2e.mjs",
    "analysis-soft-timeout.e2e.mjs",
    "analysis-route-preview.e2e.mjs",
    # Wave 4 A4.5: real-route + deadline-layer visibility through the page
    # (docs/archive/2026-08-03/w3-w3r-route-deadline-repair-work-tree.md)
    "analysis-core-hard-timeout-route-visible.e2e.mjs",
    "analysis-w3-soft-timeout-stage-visible.e2e.mjs",
    "analysis-w3-verified-w3r-shadow.e2e.mjs",
    "analysis-w3r-off-not-run.e2e.mjs",
    # Work-tree core-failure-attribution-repair T1: gate rejection attribution
    # (A2/A3/A4) and B1 zero-token checkpoint replay.
    "analysis-core-materializer-rejection.e2e.mjs",
    "analysis-core-checkpoint-replay.e2e.mjs",
    # Work-tree core-failure-attribution-repair D2: complex problems auto-queue
    # the static diagram after a successful rich solve; simple problems do not.
    "analysis-core-diagram-autoqueue.e2e.mjs",
)

# Scenarios that need the controlled mock vision endpoint (one mode each).
VISUAL_SCENARIOS = {
    "visual-web-clear.e2e.mjs": "clear",
    "visual-cli-clear.e2e.mjs": "clear",
    "visual-blurred-fail-closed.e2e.mjs": "blurred",
    "static-diagram-collaboration.e2e.mjs": "clear",
    "analysis-core-diagram-autoqueue.e2e.mjs": "clear",
}
VISUAL_FIXTURES = {
    "clear": CONSOLE / "tests" / "fixtures" / "visual-routing" / "clear-question.png",
    "blurred": CONSOLE / "tests" / "fixtures" / "visual-routing" / "blurred-question.png",
}


class E2EAgentGateway(AgentGateway):
    """Route registered Claude test identities through the deterministic adapter."""

    def _task_environ(self, task: dict | None = None) -> dict[str, str]:
        environment = super()._task_environ(task)
        config = task.get("model_config") if isinstance(task, dict) else None
        provider = str(config.get("provider", "")).strip() if isinstance(config, dict) else ""
        # analysis-core-truncated registers an openai-compatible model against
        # a local mock endpoint; let it route to the real openai-compatible
        # adapter so the reasoning-only failure envelope is produced end to
        # end. Every other scenario keeps the deterministic fake adapter.
        if provider != "openai-compatible":
            environment["TEACHER_CONSOLE_AGENT_PROVIDER"] = "adapter"
        return environment


def configure_w3_test_models(library: Path) -> None:
    """Install two probed Claude identities without storing a real credential."""
    model_registry.LIBRARY = library
    model_registry.save_model_registry_settings({
        "defaults": {
            "analysis.generate": "e2e-claude-solver",
            "expert": "e2e-claude-solver",
            "claim.verify": "e2e-claude-verifier",
        },
        "models": [
            {
                "id": "e2e-claude-solver",
                "provider": "claude",
                "model": "e2e-solver-model",
                "capabilities": ["analysis.generate"],
            },
            {
                "id": "e2e-claude-verifier",
                "provider": "claude",
                "model": "e2e-verifier-model",
                "capabilities": ["claim.verify"],
            },
        ],
    })
    for model_id in ("e2e-claude-solver", "e2e-claude-verifier"):
        model_registry.update_model_probe_result(
            model_id,
            {
                "live_probe": {
                    "status": "passed",
                    "provider": "claude",
                    "reason": "deterministic E2E test double",
                },
            },
        )
    # Task-level qualification (A2.2): the solver is the analysis.generate
    # default and must carry a current qualification record.
    model_registry.record_analysis_qualification(
        "e2e-claude-solver",
        {
            "provider": "claude",
            "sample_set_version": "e2e-fixture-v1",
            "sample_count": 3,
            "structural_success_count": 3,
            "gate_success_count": 3,
            "p50_latency_ms": 4000,
            "p95_latency_ms": 12000,
            "usage": {"completion_tokens": 5000},
            "conclusion": "qualified",
        },
    )


def configure_truncation_test_model(library: Path, base_url: str) -> None:
    """Register a probed openai-compatible model whose endpoint always truncates.

    The mock endpoint returns ``finish_reason=length`` with reasoning-only
    output, so the ``analysis-core-truncated`` scenario can exercise the
    ``output_truncated`` classification and failure-envelope usage (work-tree
    B1/B2) through the registry-routed openai-compatible adapter. The model is
    only selected when the analyze request passes ``model_id=e2e-mock-truncated``
    explicitly; ``defaults.analysis.generate`` keeps the deterministic Claude
    double for every other scenario.
    """
    model_registry.LIBRARY = library
    settings = model_registry.model_registry_settings()
    settings.setdefault("defaults", {})
    models = settings.setdefault("models", [])
    if not any(isinstance(item, dict) and str(item.get("id", "")) == "e2e-mock-truncated" for item in models):
        models.append({
            "id": "e2e-mock-truncated",
            "display_name": "E2E 截断 Mock",
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "e2e-truncation-model",
            "capabilities": ["analysis.generate"],
            "api_key": "e2e-mock-key",
            "timeout_seconds": "10",
            "model_tier": "standard",
        })
    model_registry.save_model_registry_settings(settings)
    model_registry.update_model_probe_result(
        "e2e-mock-truncated",
        {
            "live_probe": {
                "status": "passed",
                "provider": "openai-compatible",
                "reason": "deterministic E2E truncation mock endpoint",
            },
        },
    )


def configure_slow_test_model(library: Path, base_url: str) -> None:
    """Register a probed+qualified openai-compatible model that always hangs.

    The local mock endpoint sleeps past the adapter's HTTP soft deadline, so
    the ``analysis-soft-timeout`` scenario exercises the ordered three-layer
    deadline budget (work-tree A2.1/A2.4) end to end: the adapter times out
    first and the Gateway records a single ``provider_timeout`` attempt with
    no second paid retry. The model is made the ``analysis.generate`` default
    and must carry a current task-level qualification record — the
    ``config_digest`` is computed by ``record_analysis_qualification`` from
    the stored registry entry (no digest guessing in the scenario). Only this
    scenario re-points the default; every other scenario keeps the
    deterministic Claude double from ``configure_w3_test_models``.
    """
    model_registry.LIBRARY = library
    settings = model_registry.model_registry_settings()
    defaults = settings.setdefault("defaults", {})
    defaults["analysis.generate"] = "e2e-mock-slow"
    models = settings.setdefault("models", [])
    if not any(isinstance(item, dict) and str(item.get("id", "")) == "e2e-mock-slow" for item in models):
        models.append({
            "id": "e2e-mock-slow",
            "display_name": "E2E 挂起 Mock",
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "e2e-slow-model",
            "capabilities": ["analysis.generate"],
            "api_key": "e2e-mock-key",
            "timeout_seconds": "8",
            "model_tier": "standard",
        })
    model_registry.save_model_registry_settings(settings)
    model_registry.update_model_probe_result(
        "e2e-mock-slow",
        {
            "live_probe": {
                "status": "passed",
                "provider": "openai-compatible",
                "reason": "deterministic E2E slow mock endpoint",
            },
        },
    )
    model_registry.record_analysis_qualification(
        "e2e-mock-slow",
        {
            "provider": "openai-compatible",
            "sample_set_version": "e2e-fixture-v1",
            "sample_count": 3,
            "structural_success_count": 3,
            "gate_success_count": 3,
            "p50_latency_ms": 4000,
            "p95_latency_ms": 12000,
            "usage": {"completion_tokens": 5000},
            "conclusion": "qualified",
        },
    )


def configure_slow_explicit_test_model(library: Path, base_url: str) -> None:
    """Register the hanging openai-compatible mock WITHOUT re-pointing the default.

    Unlike ``configure_slow_test_model`` (analysis-soft-timeout), this helper
    only adds the ``e2e-mock-slow`` model (passed probe + qualification record)
    to the registry. The scenario selects it explicitly via ``model_id`` on the
    analyze call, so ``defaults.analysis.generate`` keeps the deterministic
    ``e2e-claude-solver`` and the route preview keeps resolving the solver that
    the rest of the suite depends on (A4.5 analysis-w3-soft-timeout-stage-visible).

    The model is appended through a direct registry edit (not
    ``save_model_registry_settings``), which rebuilds model entries and would
    silently drop the existing ``analysis_qualification`` records that the
    route preview depends on.
    """
    model_registry.LIBRARY = library
    registry_path = library / "config" / "model-registry.json"
    registry = kb.load_json(registry_path, {"schema_version": 1, "defaults": {}, "models": []})
    registry.setdefault("defaults", {})
    registry.setdefault("models", [])
    if not any(isinstance(item, dict) and str(item.get("id", "")) == "e2e-mock-slow" for item in registry["models"]):
        registry["models"].append({
            "id": "e2e-mock-slow",
            "display_name": "E2E 挂起 Mock（显式选择）",
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "e2e-slow-model",
            "capabilities": ["analysis.generate"],
            "api_key": "e2e-mock-key",
            "timeout_seconds": "8",
            "model_tier": "standard",
        })
    kb.write_json(registry_path, registry)
    model_registry.update_model_probe_result(
        "e2e-mock-slow",
        {
            "live_probe": {
                "status": "passed",
                "provider": "openai-compatible",
                "reason": "deterministic E2E slow mock endpoint",
            },
        },
    )
    # The model is only ever selected explicitly, but keeping a qualification
    # record makes the registry state identical to configure_slow_test_model.
    model_registry.record_analysis_qualification(
        "e2e-mock-slow",
        {
            "provider": "openai-compatible",
            "sample_set_version": "e2e-fixture-v1",
            "sample_count": 3,
            "structural_success_count": 3,
            "gate_success_count": 3,
            "p50_latency_ms": 4000,
            "p95_latency_ms": 12000,
            "usage": {"completion_tokens": 5000},
            "conclusion": "qualified",
        },
    )


def configure_hang_test_model(library: Path) -> None:
    """Register a probed deterministic model whose child process never returns.

    ``e2e-hang-solver`` is a claude-provider identity, so E2EAgentGateway keeps
    routing it through the deterministic fake adapter. The adapter sleeps past
    the attempt deadline whenever the problem carries the ``[e2e-hang]`` marker
    (core-solve tasks only — an additive fake-adapter mode). The Gateway then
    hard-kills the child and records ``timeout_layer=attempt_hard`` with the
    ``timeout_summary`` contract (A4.3/A4.5
    analysis-core-hard-timeout-route-visible). The slow openai-compatible mock
    cannot produce that layer: the adapter always soft-times-out first at its
    HTTP deadline (http_soft < attempt), and the current server only emits
    ``timeout_summary`` for the hard-kill branch.

    Like ``configure_slow_explicit_test_model``, the model is appended via a
    direct registry edit so the solver's qualification record survives.
    """
    model_registry.LIBRARY = library
    registry_path = library / "config" / "model-registry.json"
    registry = kb.load_json(registry_path, {"schema_version": 1, "defaults": {}, "models": []})
    registry.setdefault("defaults", {})
    registry.setdefault("models", [])
    if not any(isinstance(item, dict) and str(item.get("id", "")) == "e2e-hang-solver" for item in registry["models"]):
        registry["models"].append({
            "id": "e2e-hang-solver",
            "display_name": "E2E 挂起求解器",
            "provider": "claude",
            "model": "e2e-hang-model",
            "capabilities": ["analysis.generate"],
            "model_tier": "standard",
        })
    kb.write_json(registry_path, registry)
    model_registry.update_model_probe_result(
        "e2e-hang-solver",
        {
            "live_probe": {
                "status": "passed",
                "provider": "claude",
                "reason": "deterministic E2E hang double",
            },
        },
    )


def configure_visual_test_models(library: Path, base_url: str) -> None:
    """Register a probed local mock vision model (controlled visual adapter).

    Merges the ``e2e-mock-vision`` openai-compatible model into the current
    registry and points ``defaults.vision`` at it, so the registry-routed
    visual extraction hits the scenario's mock endpoint instead of any real
    model. The mock URL must already be known here (started before the node
    script), because a passed probe is a hard availability requirement and can
    only be written in-process against the temp registry.
    """
    model_registry.LIBRARY = library
    settings = model_registry.model_registry_settings()
    defaults = settings.setdefault("defaults", {})
    defaults["vision"] = "e2e-mock-vision"
    models = settings.setdefault("models", [])
    if not any(isinstance(item, dict) and str(item.get("id", "")) == "e2e-mock-vision" for item in models):
        models.append({
            "id": "e2e-mock-vision",
            "display_name": "E2E 受控视觉 Mock",
            "provider": "openai-compatible",
            "base_url": base_url,
            "model": "mock-vision",
            "traits": {"vision": True},
            "capabilities": ["visual-extract"],
            "api_key": "e2e-mock-key",
            "timeout_seconds": "10",
            "model_tier": "expert",
        })
    model_registry.save_model_registry_settings(settings)
    model_registry.update_model_probe_result(
        "e2e-mock-vision",
        {
            "live_probe": {
                "status": "passed",
                "provider": "openai-compatible",
                "reason": "deterministic E2E mock vision endpoint",
            },
        },
    )
    model_registry.record_vision_probe(
        "e2e-mock-vision",
        {
            "status": "passed",
            "schema": "wuli.vision-probe.v1",
            "reason": "synthetic image probe passed against mock endpoint",
        },
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
    os.environ["TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW"] = "1"
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

            mock_server = None
            mock_url = ""
            visual_mode = VISUAL_SCENARIOS.get(script_name, "")
            if visual_mode:
                mock_server, mock_url = visual_mock_server.serve_visual_mock(visual_mode)

            teacher_server.LIBRARY = library
            teacher_server.UPLOADS = uploads
            teacher_server.PUBLIC_SITE = public_site
            teacher_server.MODEL_REGISTRY_PATH = library / "config" / "model-registry.json"
            configure_w3_test_models(library)
            if mock_server is not None:
                configure_visual_test_models(library, mock_url)
            truncation_url = ""
            if script_name == "analysis-core-truncated.e2e.mjs":
                # Local reasoning-only truncation endpoint + probed
                # openai-compatible model (registered before the node script
                # runs, mirroring configure_visual_test_models).
                truncation_server, truncation_url = truncation_mock_server.serve_truncation_mock()
                mock_server = truncation_server
                configure_truncation_test_model(library, truncation_url)
            slow_url = ""
            if script_name == "analysis-soft-timeout.e2e.mjs":
                # Local hanging endpoint + probed+qualified openai-compatible
                # model registered as the analysis.generate default (A2.1/A2.4).
                slow_server, slow_url = slow_mock_server.serve_slow_mock()
                mock_server = slow_server
                configure_slow_test_model(library, slow_url)
            elif script_name == "analysis-w3-soft-timeout-stage-visible.e2e.mjs":
                # A4.5 scenario 2: the same hanging endpoint, but the scenario
                # selects e2e-mock-slow explicitly on the W3 analyze call so
                # the decompose stage soft-times-out with its real stage name.
                slow_server, slow_url = slow_mock_server.serve_slow_mock()
                mock_server = slow_server
                configure_slow_explicit_test_model(library, slow_url)
            elif script_name == "analysis-core-hard-timeout-route-visible.e2e.mjs":
                # A4.5 scenario 1: a deterministic child that never returns, so
                # the Gateway hard-kill path (timeout_layer=attempt_hard +
                # timeout_summary) is exercised end to end. See
                # configure_hang_test_model for why the slow mock cannot.
                configure_hang_test_model(library)
            # Recompute the start-time identity snapshot against the temp
            # library AFTER the test models are registered; the module-import
            # snapshot referenced the real project library and would otherwise
            # report stale immediately.
            from runtime_environment import runtime_identity

            teacher_server.SERVER_RUNTIME_IDENTITY_SNAPSHOT = runtime_identity(project_root=ROOT, library=library)

            agent_environment = dict(os.environ)
            agent_environment.update({
                "TEACHER_CONSOLE_AGENT_PROVIDER": "adapter",
                "TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": shlex.join([sys.executable, str(adapter)]),
            })
            teacher_server.AGENT_GATEWAY = E2EAgentGateway(environ=agent_environment)
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
                # Synthetic clear image used by the analysis-run scenarios
                # (web upload path with ocr=none).
                "E2E_ANALYSIS_FIXTURE": str(CONSOLE / "tests" / "fixtures" / "visual-routing" / "clear-question.png"),
            })
            if visual_mode:
                child_environment.update({
                    "E2E_VISUAL_MODE": visual_mode,
                    "E2E_VISUAL_FIXTURE": str(VISUAL_FIXTURES[visual_mode]),
                    "E2E_VISION_MOCK_URL": mock_url,
                })
            if truncation_url:
                child_environment["E2E_TRUNCATION_MOCK_URL"] = truncation_url
            if slow_url:
                child_environment["E2E_SLOW_MOCK_URL"] = slow_url
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
                if mock_server is not None:
                    mock_server.shutdown()
                    mock_server.server_close()
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
