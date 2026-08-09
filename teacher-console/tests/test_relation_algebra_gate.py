"""Tests for the isolated, feature-flagged algebra-consistency gate."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core_analysis
import physics_quality
import relation_algebra_gate
from relation_algebra_gate import algebra_violations, gate_enabled

# Real failing sample from the coaxial-cylinder postmortem: the governing
# equations live in relations r1/r2 while the solved value lives in r3, and the
# declared final value matches none of them.
Q4I_BAD = {
    "id": "Q4i",
    "final_answer": "内圆筒角速度 ω = (q^2 μ0 Ω r2^2) / (2 m r1^2 + q^2 μ0 r2^2)（与 Ω 同向）",
    "key_relations": [
        "系统角动量守恒：初始总角动量为零，最终内筒角动量 L1 = m r1^2 ω，外筒角动量 L2 = m r2^2 Ω",
        "电磁场角动量变化等于机械角动量变化，由角动量守恒得 m r1^2 ω + m r2^2 Ω = 0？但需考虑电磁场角动量",
        "实际由电磁感应和角动量守恒，得到 m r1^2 ω + m r2^2 Ω = (q^2 μ0 / (2π)) (Ω - ω) r2^2？需仔细推导",
        "最终解得 ω = (q^2 μ0 Ω r2^2) / (2 m r1^2 + q^2 μ0 r2^2)",
    ],
}

CONSISTENT = {
    "id": "Q1",
    "final_answer": "x = 6",
    "key_relations": ["由牛顿第二定律得 2 x = 12，解得 x = 6"],
}

INEQUALITY_FINAL = {
    "id": "Q3",
    "final_answer": "当 ω ≥ sqrt( q Q (1/r1 - 1/r2) / (2π ε0 μ) ) 时，点电荷能到达外圆筒。",
    "key_relations": ["能量守恒：电场力做功 W_e = Q q/(2π ε0) ln(r2/r1)"],
}

UNPARSEABLE = {
    "id": "Q9",
    "final_answer": "E = q/(2π ε0 r)",
    "key_relations": ["由高斯定理 E·2πr = q/ε0，故 E = q/(2π ε0 r)"],
}

_SYMPY = relation_algebra_gate._SYMPY_AVAILABLE


def _gate_on() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {relation_algebra_gate.GATE_ENV: "on"})


def _gate_off() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {relation_algebra_gate.GATE_ENV: ""})


def _retry_on() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {relation_algebra_gate.RETRY_ENV: "on"})


def _retry_off() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {relation_algebra_gate.RETRY_ENV: ""})


def _render_retry_on() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {core_analysis.RENDER_RETRY_ENV: "on"})


def _render_retry_off() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {core_analysis.RENDER_RETRY_ENV: ""})


def _render_agent_on() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {core_analysis.RENDER_AGENT_ENV: "on"})


def _render_agent_off() -> mock._patch_dict:
    return mock.patch.dict(os.environ, {core_analysis.RENDER_AGENT_ENV: ""})


_GATE_REJECT_ERROR = (
    "physics quality gate rejected: relation-algebra-inconsistent@Q4i"
)

_RENDER_REJECT_ERROR = (
    "render fidelity gate rejected: missing content: "
    "Q1: E=q/(2π ε0 r)；Q2: 当 r<r1 时 B=0。"
)


class GateSwitchTest(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        with _gate_off():
            self.assertFalse(gate_enabled())

    def test_disabled_returns_no_violations_even_for_bad_target(self) -> None:
        with _gate_off():
            self.assertEqual(algebra_violations(Q4I_BAD), [])

    def test_enabled_flag_values(self) -> None:
        for value in ("1", "on", "ON", "true", "yes"):
            with mock.patch.dict(os.environ, {relation_algebra_gate.GATE_ENV: value}):
                self.assertTrue(gate_enabled(), value)
        for value in ("", "off", "0", "no"):
            with mock.patch.dict(os.environ, {relation_algebra_gate.GATE_ENV: value}):
                self.assertFalse(gate_enabled(), value)


@unittest.skipUnless(_SYMPY, "sympy is not installed in this environment")
class AlgebraViolationTest(unittest.TestCase):
    def test_rejects_q4i_algebra_inconsistency(self) -> None:
        with _gate_on():
            violations = algebra_violations(Q4I_BAD)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["code"], "relation-algebra-inconsistent")
        self.assertEqual(violations[0]["target_id"], "Q4i")

    def test_passes_self_consistent_target(self) -> None:
        with _gate_on():
            self.assertEqual(algebra_violations(CONSISTENT), [])

    def test_skips_inequality_final(self) -> None:
        with _gate_on():
            self.assertEqual(algebra_violations(INEQUALITY_FINAL), [])

    def test_parse_failure_is_not_a_rejection(self) -> None:
        with _gate_on():
            self.assertEqual(algebra_violations(UNPARSEABLE), [])


class ReportIsolationTest(unittest.TestCase):
    def _report(self, target: dict) -> dict:
        return physics_quality.physics_quality_report(
            {"targets": [target]}, {"digest": "d"}, "内圆筒半径 r1 外圆筒半径 r2 质量 m 电荷 q"
        )

    def test_disabled_report_matches_legacy_shape(self) -> None:
        with _gate_off():
            report = self._report(CONSISTENT)
        names = [item["name"] for item in report["checked_obligations"]]
        self.assertNotIn("relation-algebra-consistency", names)
        self.assertEqual(len(names), 5)

    @unittest.skipUnless(_SYMPY, "sympy is not installed in this environment")
    def test_enabled_report_surfaces_q4i_violation(self) -> None:
        with _gate_on():
            report = self._report(Q4I_BAD)
        codes = [item["code"] for item in report["reason_codes"]]
        self.assertIn("relation-algebra-inconsistent", codes)
        names = [item["name"] for item in report["checked_obligations"]]
        self.assertIn("relation-algebra-consistency", names)


class RetrySwitchTest(unittest.TestCase):
    def test_retry_disabled_by_default(self) -> None:
        with _retry_off():
            self.assertFalse(relation_algebra_gate.retry_enabled())
            failed = {"status": "failed"}
            self.assertFalse(
                relation_algebra_gate.should_retry(failed, _GATE_REJECT_ERROR)
            )

    def test_should_retry_only_for_algebra_rejection(self) -> None:
        with _retry_on():
            self.assertTrue(
                relation_algebra_gate.should_retry(
                    {"status": "failed"}, _GATE_REJECT_ERROR
                )
            )
            self.assertFalse(
                relation_algebra_gate.should_retry(
                    {"status": "completed"}, _GATE_REJECT_ERROR
                )
            )
            self.assertFalse(
                relation_algebra_gate.should_retry(
                    {"status": "failed"},
                    "physics quality gate rejected: sign-flip-unjustified@Q4i",
                )
            )
            self.assertFalse(relation_algebra_gate.should_retry({"status": "failed"}))


@unittest.skipUnless(_SYMPY, "sympy is not installed in this environment")
class CorrectiveFeedbackTest(unittest.TestCase):
    def test_feedback_empty_when_switches_off(self) -> None:
        with _gate_off(), _retry_off():
            self.assertEqual(relation_algebra_gate.corrective_feedback([Q4I_BAD]), "")
        with _gate_on(), _retry_off():
            self.assertEqual(relation_algebra_gate.corrective_feedback([Q4I_BAD]), "")

    def test_feedback_carries_equations_and_solutions(self) -> None:
        with _gate_on(), _retry_on():
            feedback = relation_algebra_gate.corrective_feedback([Q4I_BAD, INEQUALITY_FINAL])
        self.assertIn("代数一致性修正", feedback)
        self.assertIn("Q4i", feedback)
        # The exact sympy solution of the stated angular-momentum equation.
        self.assertIn("-Omega*r2**2/r1**2", feedback)
        # Inequality finals contribute no feedback block.
        self.assertNotIn("Q3", feedback)


class ServerRetryChannelTest(unittest.TestCase):
    """The isolated layer-three channel in ``server.run_agent_gateway``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        library = Path(self._tmp.name)
        self.entry = library / "entries" / "test-entry"
        self.entry.mkdir(parents=True)
        (library / "indexes").mkdir()
        self.checkpoint_path = library / ".cache" / "core-checkpoints" / "test-entry.json"
        self.checkpoint_path.parent.mkdir(parents=True)
        self.task = {"kind": "analysis.generate", "prompt": "solve it"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_stale_checkpoint(self) -> None:
        self.checkpoint_path.write_text(json.dumps({"payload": {}}), encoding="utf-8")

    def _failed_run(self, task, validator, materializer=None):
        if materializer is not None:
            try:
                materializer(Path("staging"), {"claims": [Q4I_BAD]})
            except ValueError:
                pass  # capture_materializer recorded the reject reason
        return {
            "status": "failed",
            "failure_type": "materializer_rejected",
            "materializer_error": True,
            "attempts": [],
            "usage": {},
        }

    def _run(self, outcomes, *, reject_text=_GATE_REJECT_ERROR, **kwargs):
        import server

        self.materializer_calls: list[dict] = []
        # Support chained repair flows: each materialization rejects with the
        # next pending text; once exhausted, payloads materialize cleanly.
        pending_rejects = (
            list(reject_text) if isinstance(reject_text, (list, tuple)) else [reject_text]
        )

        def flaky_materializer(staging, payload):
            self.materializer_calls.append(payload)
            if pending_rejects:
                raise ValueError(pending_rejects.pop(0))
            return {"materialized": True}

        pending = iter(outcomes)

        def dispatcher(task, validator, materializer=None):
            return next(pending)(task, validator, materializer=materializer)

        with mock.patch.object(server.AGENT_GATEWAY, "run", side_effect=dispatcher) as run:
            result = server.run_agent_gateway(
                self.entry,
                self.task,
                validator=lambda staging, changed: [],
                materializer=flaky_materializer,
                bounded_failure_repair=False,
                **kwargs,
            )
        return run, result

    def test_switch_off_never_retries(self) -> None:
        with _gate_on(), _retry_off():
            self._write_stale_checkpoint()
            run, result = self._run([self._failed_run], retry_on_algebra_reject=True)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(self.checkpoint_path.is_file())
        self.assertNotIn("algebra_gate_retry", result)

    def test_retry_recovers_with_corrective_prompt(self) -> None:
        def recovered_run(task, validator, materializer=None):
            return {"status": "completed", "attempts": [], "usage": {}}

        with _gate_on(), _retry_on():
            self._write_stale_checkpoint()
            run, result = self._run(
                [self._failed_run, recovered_run],
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(run.call_count, 2)
        retry_task = run.call_args_list[1].args[0]
        self.assertIn("代数一致性修正", retry_task["prompt"])
        self.assertIn("-Omega*r2**2/r1**2", retry_task["prompt"])
        # The stale checkpoint is dropped before the fresh corrective call.
        self.assertFalse(self.checkpoint_path.is_file())
        self.assertEqual(result["algebra_gate_retry"]["status"], "recovered")
        # The original task object is never mutated (deep-copied for retry).
        self.assertEqual(self.task["prompt"], "solve it")

    def test_retry_exhausted_keeps_failure(self) -> None:
        with _gate_on(), _retry_on():
            run, result = self._run(
                [self._failed_run, self._failed_run],
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result["algebra_gate_retry"]["status"], "exhausted")
        self.assertEqual(result["status"], "failed")

    def test_failed_retry_clears_repair_checkpoint(self) -> None:
        def second_poisoned(task, validator, materializer=None):
            if materializer is not None:
                materializer(Path("staging"), {"claims": [Q4I_BAD]})
            # The repair's materializer checkpointed before a later gate
            # rejected; the poisoned checkpoint must not survive the failure.
            self.checkpoint_path.write_text(json.dumps({"payload": {}}), encoding="utf-8")
            return {
                "status": "failed",
                "failure_type": "materializer_rejected",
                "materializer_error": True,
                "attempts": [],
                "usage": {},
            }

        with _gate_on(), _retry_on():
            run, result = self._run(
                [self._failed_run, second_poisoned],
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(result["algebra_gate_retry"]["status"], "exhausted")
        self.assertFalse(self.checkpoint_path.is_file())

    def test_no_retry_for_other_gate_rejections(self) -> None:
        with _gate_on(), _retry_on():
            run, result = self._run(
                [self._failed_run],
                reject_text="physics quality gate rejected: sign-flip-unjustified@Q4i",
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("algebra_gate_retry", result)


class RenderFidelityFeedbackTest(unittest.TestCase):
    """The corrective text for the verbatim-claims contract."""

    def test_feedback_empty_when_switch_off(self) -> None:
        with _render_retry_off():
            self.assertEqual(core_analysis.render_fidelity_feedback(_RENDER_REJECT_ERROR), "")

    def test_feedback_ignores_non_render_reasons(self) -> None:
        with _render_retry_on():
            self.assertEqual(core_analysis.render_fidelity_feedback(_GATE_REJECT_ERROR), "")
            self.assertEqual(core_analysis.render_fidelity_feedback(""), "")

    def test_feedback_lists_missing_content_and_verbatim_rule(self) -> None:
        with _render_retry_on():
            feedback = core_analysis.render_fidelity_feedback(_RENDER_REJECT_ERROR)
        self.assertIn("渲染保真修正", feedback)
        self.assertIn("E=q/(2π ε0 r)", feedback)
        self.assertIn("逐字", feedback)
        self.assertIn("不得改写", feedback)


class RenderRetryChannelTest(ServerRetryChannelTest):
    """Render-fidelity rejections reuse the same single-retry plumbing."""

    def test_render_retry_disabled_by_default(self) -> None:
        with _gate_on(), _retry_on(), _render_retry_off():
            run, result = self._run(
                [self._failed_run],
                reject_text=_RENDER_REJECT_ERROR,
                retry_on_algebra_reject=True,
            )
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("algebra_gate_retry", result)

    def test_render_retry_recovers_with_verbatim_prompt(self) -> None:
        def recovered_run(task, validator, materializer=None):
            return {"status": "completed", "attempts": [], "usage": {}}

        with _gate_on(), _retry_on(), _render_retry_on():
            self._write_stale_checkpoint()
            run, result = self._run(
                [self._failed_run, recovered_run],
                reject_text=_RENDER_REJECT_ERROR,
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(run.call_count, 2)
        retry_task = run.call_args_list[1].args[0]
        self.assertIn("渲染保真修正", retry_task["prompt"])
        self.assertIn("E=q/(2π ε0 r)", retry_task["prompt"])
        # The stale checkpoint is dropped before the fresh corrective call.
        self.assertFalse(self.checkpoint_path.is_file())
        self.assertEqual(result["algebra_gate_retry"]["status"], "recovered")
        self.assertEqual(result["algebra_gate_retry"]["kind"], "render-fidelity")

    def test_render_retry_exhausted_keeps_failure(self) -> None:
        with _gate_on(), _retry_on(), _render_retry_on():
            run, result = self._run(
                [self._failed_run, self._failed_run],
                reject_text=_RENDER_REJECT_ERROR,
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result["algebra_gate_retry"]["status"], "exhausted")
        self.assertEqual(result["status"], "failed")


class RenderAgentChannelTest(ServerRetryChannelTest):
    """A scoped render-only agent repairs render-fidelity rejections."""

    def _recovered_render_run(self, task, validator, materializer=None):
        if materializer is not None:
            # Mirrors the real gateway: the materializer only runs when the
            # structured payload carries status == "completed".
            materializer(Path("staging"), {"status": "completed", "student_solution": "rendered-markdown"})
        return {"status": "completed", "attempts": [], "usage": {}}

    def test_render_agent_off_keeps_rewrite_retry(self) -> None:
        def recovered_run(task, validator, materializer=None):
            return {"status": "completed", "attempts": [], "usage": {}}

        with _gate_on(), _retry_on(), _render_retry_on(), _render_agent_off():
            run, result = self._run(
                [self._failed_run, recovered_run],
                reject_text=_RENDER_REJECT_ERROR,
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(result["algebra_gate_retry"]["kind"], "render-fidelity")
        self.assertIn("渲染保真修正", run.call_args_list[1].args[0]["prompt"])

    def test_render_agent_rebuilds_markdown_from_claims(self) -> None:
        with _gate_on(), _retry_on(), _render_retry_on(), _render_agent_on(), \
                mock.patch("server.resolve_model_id_for_task", return_value="economy-model"), \
                mock.patch("server.model_config_for_task", return_value={"provider": "openai-compatible"}):
            run, result = self._run(
                [self._failed_run, self._recovered_render_run],
                reject_text=_RENDER_REJECT_ERROR,
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(run.call_count, 2)
        retry_task = run.call_args_list[1].args[0]
        # The repair call is render-only: scoped prompt, economy tier, and a
        # contract whose instructions carry the approved claims verbatim.
        self.assertIn("只做渲染", retry_task["prompt"])
        self.assertEqual(retry_task["routing_tier"], "economy")
        instructions = retry_task["output_contract"]["instructions"]
        self.assertIn("排版渲染器", instructions)
        self.assertIn("Q4i", instructions)
        self.assertIn(Q4I_BAD["final_answer"], instructions)
        # The merged payload keeps the approved claims and swaps the markdown.
        merged = self.materializer_calls[-1]
        self.assertEqual(merged["student_solution"], "rendered-markdown")
        self.assertEqual(merged["claims"], [Q4I_BAD])
        self.assertEqual(result["algebra_gate_retry"]["status"], "recovered")
        self.assertEqual(result["algebra_gate_retry"]["kind"], "render-agent")

    def test_render_agent_degrades_when_registry_miss(self) -> None:
        def recovered_run(task, validator, materializer=None):
            return {"status": "completed", "attempts": [], "usage": {}}

        with _gate_on(), _retry_on(), _render_retry_on(), _render_agent_on(), \
                mock.patch(
                    "server.resolve_model_id_for_task",
                    side_effect=ValueError("no economy model registered"),
                ):
            run, result = self._run(
                [self._failed_run, recovered_run],
                reject_text=_RENDER_REJECT_ERROR,
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(result["algebra_gate_retry"]["kind"], "render-fidelity")
        self.assertIn("渲染保真修正", run.call_args_list[1].args[0]["prompt"])

    def test_render_agent_falls_back_to_solver_model(self) -> None:
        # Registry has no qualified economy renderer, but the solve task
        # carries a model config: render in a fresh context with that model.
        self.task["model_config"] = {"provider": "openai-compatible", "id": "solver-model"}
        self.task["routing_tier"] = "auto"
        with _gate_on(), _retry_on(), _render_retry_on(), _render_agent_on(), \
                mock.patch(
                    "server.resolve_model_id_for_task",
                    side_effect=ValueError("no economy model registered"),
                ):
            run, result = self._run(
                [self._failed_run, self._recovered_render_run],
                reject_text=_RENDER_REJECT_ERROR,
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        retry_task = run.call_args_list[1].args[0]
        self.assertEqual(result["algebra_gate_retry"]["kind"], "render-agent")
        self.assertEqual(retry_task["model_config"]["id"], "solver-model")
        self.assertEqual(retry_task["routing_tier"], "auto")

    def test_algebra_retry_then_render_agent_chain(self) -> None:
        # Real failure mode (2026-08-07): the algebra repair passes the physics
        # gates but its markdown breaks the verbatim-claims contract; one
        # chained render-only repair must still get a chance to recover.
        with _gate_on(), _retry_on(), _render_retry_on(), _render_agent_on(), \
                mock.patch("server.resolve_model_id_for_task", return_value="economy-model"), \
                mock.patch("server.model_config_for_task", return_value={"provider": "openai-compatible"}):
            run, result = self._run(
                [self._failed_run, self._failed_run, self._recovered_render_run],
                reject_text=[_GATE_REJECT_ERROR, _RENDER_REJECT_ERROR],
                retry_on_algebra_reject=True,
                checkpoint_entry=self.entry,
            )
        self.assertEqual(run.call_count, 3)
        # Second call is the algebra rewrite; third is the scoped render agent.
        self.assertIn("代数一致性修正", run.call_args_list[1].args[0]["prompt"])
        self.assertIn("只做渲染", run.call_args_list[2].args[0]["prompt"])
        merged = self.materializer_calls[-1]
        self.assertEqual(merged["student_solution"], "rendered-markdown")
        self.assertEqual(merged["claims"], [Q4I_BAD])
        self.assertEqual(result["algebra_gate_retry"]["status"], "recovered")
        self.assertEqual(result["algebra_gate_retry"]["kind"], "render-agent")

    def test_render_contract_status_satisfies_gateway_materialization(self) -> None:
        # Regression: the gateway only runs the materializer when the payload
        # carries status == "completed"; the render schema previously allowed
        # no such field (additionalProperties: false), so the retry wrote no
        # files and died as candidate_no_change.
        contract = core_analysis.render_agent_contract([Q4I_BAD], "olympiad_official")
        schema = contract["schema"]
        self.assertIn("status", schema["required"])
        self.assertEqual(schema["properties"]["status"]["enum"], ["completed"])
        self.assertIn('status="completed"', contract["instructions"])


if __name__ == "__main__":
    unittest.main()
