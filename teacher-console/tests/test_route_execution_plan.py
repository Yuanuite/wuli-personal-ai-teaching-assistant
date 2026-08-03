"""RouteExecutionPlan.v1 matrix tests (work-tree A2.1/A4.4).

The plan must express ``core + w3r off``, ``w3 + w3r shadow`` and
``w3 + legacy renderer`` as distinct states — never a single "deep analysis"
boolean — and its config digest must match the route snapshot digest so the
preview can be cross-checked against the executed job.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

from route_snapshot import (  # noqa: E402
    CORE_FIRST_STAGES,
    ROUTE_EXECUTION_PLAN_SCHEMA,
    W3_STAGES,
    build_route_execution_plan,
    route_config_digest,
)


def _write_config(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class RouteExecutionPlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        (self.library / "config").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, core_mode: str = "core-first", w3r_mode: str = "off"):
        core_config = (
            {
                "schema_version": 1,
                "policy_version": "wuli-core-first-routing-v1",
                "mode": core_mode,
                "max_latency_seconds": 90,
            }
            if core_mode
            else {}
        )
        w3r_config = {"schema_version": 1, "policy_version": "wuli-w3r-routing-v1", "mode": w3r_mode, "gray_entry_ids": [], "evidence": {}}
        return build_route_execution_plan(library=self.library, core_config=core_config, w3r_config=w3r_config)

    def test_core_plus_w3r_off(self):
        plan = self.plan(core_mode="core-first", w3r_mode="off")
        self.assertEqual(plan["schema"], ROUTE_EXECUTION_PLAN_SCHEMA)
        self.assertEqual(plan["planned_solver_route"], "core")
        self.assertEqual(plan["w3r_mode"], "off")
        self.assertEqual(plan["planned_renderer_mode"], "legacy")
        self.assertEqual(plan["expected_stages"], list(CORE_FIRST_STAGES))

    def test_w3_plus_w3r_shadow(self):
        plan = self.plan(core_mode="legacy-adaptive", w3r_mode="shadow")
        self.assertEqual(plan["planned_solver_route"], "w3")
        self.assertEqual(plan["w3r_mode"], "shadow")
        self.assertEqual(plan["planned_renderer_mode"], "w3r-shadow")
        self.assertEqual(plan["expected_stages"], list(W3_STAGES))

    def test_w3_plus_legacy_renderer(self):
        plan = self.plan(core_mode="legacy-adaptive", w3r_mode="off")
        self.assertEqual(plan["planned_solver_route"], "w3")
        self.assertEqual(plan["planned_renderer_mode"], "legacy")
        self.assertIn("solver-a", plan["expected_stages"])

    def test_invalid_w3r_mode_fails_closed_to_off(self):
        plan = self.plan(core_mode="legacy-adaptive", w3r_mode="bogus")
        self.assertEqual(plan["w3r_mode"], "off")
        self.assertEqual(plan["planned_renderer_mode"], "legacy")

    def test_core_first_never_claims_w3r_even_with_shadow_config(self):
        plan = self.plan(core_mode="core-first", w3r_mode="shadow")
        self.assertEqual(plan["planned_solver_route"], "core")
        self.assertEqual(plan["planned_renderer_mode"], "legacy")
        self.assertNotIn("solver-a", plan["expected_stages"])

    def test_plan_digest_matches_route_snapshot_digest(self):
        plan = self.plan()
        self.assertEqual(plan["config_digest"], route_config_digest(self.library))

    def test_rollback_drill_switches_route_without_code_change(self):
        # A5.4: flipping only the config files (legacy-adaptive -> core-first,
        # W3R shadow -> off) must stop the new route without any code change.
        legacy = self.plan(core_mode="legacy-adaptive", w3r_mode="shadow")
        self.assertEqual(legacy["planned_solver_route"], "w3")
        self.assertEqual(legacy["planned_renderer_mode"], "w3r-shadow")
        rolled_back = self.plan(core_mode="core-first", w3r_mode="off")
        self.assertEqual(rolled_back["planned_solver_route"], "core")
        self.assertEqual(rolled_back["planned_renderer_mode"], "legacy")
        self.assertNotIn("solver-a", rolled_back["expected_stages"])

    def test_canonical_write_policy_is_explicit(self):
        plan = self.plan()
        self.assertEqual(plan["canonical_write_policy"], "promote-after-teacher-review")


if __name__ == "__main__":
    unittest.main()
