"""Contract tests for RouteSnapshot.v1 (closure C3.1/C3.2/C3.3)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
CONSOLE = ROOT / "teacher-console"
for path in (SCRIPTS, CONSOLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import kb
from route_snapshot import (
    ROUTE_SNAPSHOT_SCHEMA,
    build_route_snapshot,
    route_config_digest,
    route_snapshot_is_stale,
    route_snapshot_summary,
)


class RouteSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        config_dir = self.library / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "model-registry.json").write_text(
            json.dumps({"schema_version": 1, "defaults": {"vision": "mimo"}, "models": []}),
            encoding="utf-8",
        )
        (config_dir / "analysis-production-routing.json").write_text(
            json.dumps({"schema_version": 1, "mode": "core-first"}),
            encoding="utf-8",
        )

    def _snapshot(self):
        return build_route_snapshot(
            kind="analysis.generate",
            routing_tier="economy",
            requested_model_id="auto",
            config={
                "id": "deepseek-v4-flash-api",
                "provider": "openai-compatible",
                "model": "deepseek-v4-flash",
            },
            library=self.library,
        )

    def test_snapshot_contract_and_no_secrets(self):
        snapshot = self._snapshot()
        self.assertEqual(snapshot["schema"], ROUTE_SNAPSHOT_SCHEMA)
        self.assertEqual(snapshot["kind"], "analysis.generate")
        self.assertEqual(snapshot["requested_model_id"], "auto")
        self.assertEqual(snapshot["resolved_model_id"], "deepseek-v4-flash-api")
        self.assertEqual(snapshot["provider"], "openai-compatible")
        self.assertEqual(snapshot["upstream_model"], "deepseek-v4-flash")
        self.assertEqual(len(snapshot["config_digest"]), 64)
        self.assertTrue(snapshot["created_at"])
        blob = json.dumps(snapshot, ensure_ascii=False)
        for forbidden in ("api_key", "sk-", "Bearer"):
            self.assertNotIn(forbidden, blob)

    def test_digest_changes_when_registry_or_route_changes(self):
        before = route_config_digest(self.library)
        (self.library / "config" / "model-registry.json").write_text(
            json.dumps({"schema_version": 1, "defaults": {"vision": "other"}, "models": []}),
            encoding="utf-8",
        )
        self.assertNotEqual(before, route_config_digest(self.library))
        (self.library / "config" / "model-registry.json").write_text(
            json.dumps({"schema_version": 1, "defaults": {"vision": "mimo"}, "models": []}),
            encoding="utf-8",
        )
        (self.library / "config" / "analysis-production-routing.json").write_text(
            json.dumps({"schema_version": 1, "mode": "w3-gated"}),
            encoding="utf-8",
        )
        self.assertNotEqual(before, route_config_digest(self.library))

    def test_stale_detection_fails_closed(self):
        snapshot = self._snapshot()
        self.assertFalse(route_snapshot_is_stale(snapshot, self.library))
        (self.library / "config" / "model-registry.json").write_text(
            json.dumps({"schema_version": 1, "defaults": {"vision": "changed"}, "models": []}),
            encoding="utf-8",
        )
        self.assertTrue(route_snapshot_is_stale(snapshot, self.library))
        self.assertTrue(route_snapshot_is_stale({"schema": "wrong"}, self.library))
        self.assertTrue(route_snapshot_is_stale(cast(dict, None), self.library))

    def test_summary_is_public_safe(self):
        summary = route_snapshot_summary(self._snapshot())
        self.assertEqual(summary["schema"], ROUTE_SNAPSHOT_SCHEMA)
        self.assertEqual(summary["resolved_model_id"], "deepseek-v4-flash-api")
        blob = json.dumps(summary, ensure_ascii=False)
        for forbidden in ("api_key", "sk-"):
            self.assertNotIn(forbidden, blob)
        self.assertEqual(route_snapshot_summary(cast(dict, None)), {})


if __name__ == "__main__":
    unittest.main()
