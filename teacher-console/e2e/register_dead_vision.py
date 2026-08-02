#!/usr/bin/env python3
"""Register a vision model whose endpoint is dead (C5.6 rollback drill).

Points ``defaults.vision`` at a model whose base_url port has no listener, with
a correctly computed probe digest, so visual extraction fails closed with a
durable ledger record instead of silently succeeding.
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
    if len(sys.argv) < 2:
        print("usage: register_dead_vision.py <library>", file=sys.stderr)
        return 2
    library = Path(sys.argv[1]).resolve()
    model_registry.LIBRARY = library
    settings = model_registry.model_registry_settings()
    defaults = settings.setdefault("defaults", {})
    defaults["vision"] = "dead-vision"
    models = settings.setdefault("models", [])
    if not any(isinstance(m, dict) and m.get("id") == "dead-vision" for m in models):
        models.append({
            "id": "dead-vision",
            "display_name": "Dead Vision",
            "provider": "openai-compatible",
            "base_url": "http://127.0.0.1:1/v1",
            "model": "dead-vision-model",
            "traits": {"vision": True},
            "capabilities": ["visual-extract"],
            "api_key": "dead-key",
            "timeout_seconds": "3",
            "model_tier": "expert",
        })
    model_registry.save_model_registry_settings(settings)
    model_registry.update_model_probe_result(
        "dead-vision",
        {
            "live_probe": {
                "status": "passed",
                "provider": "openai-compatible",
                "reason": "probe passed; endpoint is dead",
            }
        },
    )
    model_registry.record_vision_probe(
        "dead-vision",
        {"schema": "wuli.vision-probe.v1", "status": "passed", "reason": "probe passed; endpoint is dead"},
    )
    print("registered dead vision model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
