#!/usr/bin/env python3
"""Run an isolated teacher-console server for a blind CPhO web evaluation.

The script copies only runtime configuration.  It deliberately initializes an
empty knowledge store and never accepts a path to an official solution file.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

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


def configure_models(source: Path, target: Path) -> None:
    registry = json.loads(source.read_text(encoding="utf-8"))
    registry = copy.deepcopy(registry)
    registry.setdefault("defaults", {}).update({
        "analysis.generate": "Deepseek-v4-flash",
        "claim.verify": "Deepseek-v4-pro",
        "economy": "Deepseek-v4-flash",
        "expert": "Deepseek-v4-pro",
    })
    for model in registry.get("models", []):
        model_id = str(model.get("id", ""))
        if model_id == "Deepseek-v4-flash":
            capabilities = set(model.get("capabilities", []))
            capabilities.update({"analysis.generate", "answer.revise"})
            model["capabilities"] = sorted(capabilities)
            model["provider"] = "openai-compatible"
            model["model"] = "deepseek-v4-flash"
            model["timeout_seconds"] = "300"
            model.pop("effort", None)
        elif model_id == "Deepseek-v4-pro":
            capabilities = set(model.get("capabilities", []))
            capabilities.add("claim.verify")
            model["capabilities"] = sorted(capabilities)
            model["provider"] = "openai-compatible"
            model["model"] = "deepseek-v4-pro"
            model["timeout_seconds"] = "300"
            model.pop("effort", None)
        else:
            continue
        model["probe"] = {
            "status": "passed",
            "provider": "openai-compatible",
            "message": "直连 API 隔离探针通过",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "config_digest": model_registry._model_probe_digest(model),
        }
    target.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare_workspace(project_root: Path, workspace: Path) -> Path:
    library = workspace / "student-error-library"
    kb.init_library(library)
    (library / "config").mkdir(parents=True, exist_ok=True)
    (workspace / "error-collection").mkdir(parents=True, exist_ok=True)
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    (workspace / "student-site").mkdir(parents=True, exist_ok=True)

    source_library = project_root / "student-error-library"
    shutil.copy2(source_library / "config.json", library / "config.json")
    for name in (
        "agent-runtime.json",
        "agent-scheduler.json",
        "w3-production-routing.json",
    ):
        source = source_library / "config" / name
        if source.is_file():
            shutil.copy2(source, library / "config" / name)
    configure_models(
        source_library / "config" / "model-registry.json",
        library / "config" / "model-registry.json",
    )
    return library


def configure_server(library: Path, workspace: Path) -> None:
    teacher_server.LIBRARY = library
    teacher_server.UPLOADS = workspace / "error-collection"
    teacher_server.PUBLIC_SITE = workspace / "student-site"
    teacher_server.MODEL_REGISTRY_PATH = library / "config" / "model-registry.json"
    model_registry.LIBRARY = library
    teacher_server.AGENT_GATEWAY = AgentGateway(environment_resolver=lambda: resolved_environment(library))
    teacher_server._JOB_MANAGER = AgentJobManager(
        library / ".cache" / "agent-jobs",
        max_workers=1,
        scheduler_config={
            "adaptive_concurrency": {
                "enabled": False,
            }
        },
    )
    original_run_adaptive_analysis = teacher_server.Handler.run_adaptive_analysis

    def run_competition_analysis(self, entry, data):
        return original_run_adaptive_analysis(
            self,
            entry,
            {
                **dict(data),
                "method_profile": "olympiad_official",
            },
        )

    setattr(teacher_server.Handler, "run_adaptive_analysis", run_competition_analysis)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    library = prepare_workspace(PROJECT_ROOT, args.workspace)
    configure_server(library, args.workspace)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), teacher_server.Handler)
    print(
        json.dumps(
            {
                "status": "ready",
                "url": f"http://127.0.0.1:{args.port}",
                "library": str(library),
                "knowledge_entries": len(list((library / "entries").iterdir())),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
