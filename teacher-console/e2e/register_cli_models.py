#!/usr/bin/env python3
"""Register the deterministic E2E mock models into an isolated CLI library.

Mirrors ``configure_visual_test_models`` in ``run_e2e.py``: writes the registry
and records probes through ``model_registry`` so digests are computed exactly
like the production path. Usage:

    register_cli_models.py <library> <vision_mock_base_url>
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))

import model_registry  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: register_cli_models.py <library> <vision_mock_base_url>", file=sys.stderr)
        return 2
    library = Path(sys.argv[1]).resolve()
    vision_base = sys.argv[2].strip()
    model_registry.LIBRARY = library
    model_registry.save_model_registry_settings({
        "schema_version": 1,
        "defaults": {
            "vision": "e2e-mock-vision",
            "expert": "e2e-claude-solver",
            "analysis.generate": "e2e-claude-solver",
        },
        "models": [
            {
                "id": "e2e-mock-vision",
                "display_name": "E2E Mock Vision",
                "provider": "openai-compatible",
                "base_url": vision_base,
                "model": "mock-vision",
                "traits": {"vision": True},
                "capabilities": ["visual-extract"],
                "api_key": "e2e-mock-key",
                "timeout_seconds": "10",
                "model_tier": "expert",
            },
            {
                "id": "e2e-claude-solver",
                "provider": "claude",
                "model": "e2e-solver-model",
                "capabilities": ["analysis.generate"],
            },
        ],
    })
    model_registry.update_model_probe_result(
        "e2e-mock-vision",
        {"live_probe": {"status": "passed", "provider": "openai-compatible", "reason": "deterministic E2E mock"}},
    )
    model_registry.record_vision_probe(
        "e2e-mock-vision",
        {"schema": "wuli.vision-probe.v1", "status": "passed", "reason": "synthetic image probe passed"},
    )
    model_registry.update_model_probe_result(
        "e2e-claude-solver",
        {"live_probe": {"status": "passed", "provider": "claude", "reason": "deterministic E2E mock"}},
    )
    print("registered cli models")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
