#!/usr/bin/env python3
"""Fixed-condition cognitive-loop off/on shadow ablation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import claim_ledger
import cognitive_loop
import w3_pipeline

ABLATION_SCHEMA = "wuli.correctness-cognitive-loop-ablation.v1"
FIXED_RANDOM_SEED = 20260729

BLUEPRINT = {
    "status": "completed",
    "question_targets": [{
        "id": "q1",
        "prompt": "求第一次进入后的结果",
        "answer_type": "value",
    }],
    "physical_stages": [{"id": "p1"}],
    "reasoning_steps": [],
    "stage_step_links": [],
    "verification_obligations": [{
        "id": "v1",
        "target_id": "q1",
        "check": "核对首次事件与结果",
        "risk": "medium",
    }],
}
SOLVER = {
    "status": "completed",
    "message": "ok",
    "targets": [{
        "id": "q1",
        "final_answer": "6",
        "supporting_relations": ["2×3=6"],
        "conditions": [],
        "covered_obligation_ids": ["v1"],
    }],
    "stage_results": [],
    "option_verdicts": [],
    "blueprint_audit": {
        "status": "followed",
        "covered_target_ids": ["q1"],
        "covered_obligation_ids": ["v1"],
        "revisions": [],
    },
    "_runtime_identity": {
        "model_id": "fixed-solver-model",
        "provider": "fixed-fixture",
        "context_isolated": False,
    },
}
SCENARIOS = (
    ("normal", "pass"),
    ("conflict", "conflict"),
    ("insufficient", "insufficient"),
    ("fuse", "insufficient"),
)


def _controlled_hypotheses(scenario: str) -> list[dict[str, Any]]:
    common = {
        "snapshot_version": 1,
        "challenge_id": "CH1",
        "explains_gap": (
            f"It proposes a finite falsification path for the {scenario} gap."
        ),
        "falsification": {
            "test_type": "deterministic",
            "procedure": "Enumerate the declared alternatives and compare outcomes.",
            "expected_observation": "One alternative changes the disputed relation.",
            "failure_observation": "Every alternative preserves the disputed relation.",
        },
        "affected_claim_ids": ["C1"],
        "status": "candidate",
    }
    return [
        {
            **common,
            "id": "H1",
            "operator": "event-reordering",
            "proposal": "An admissible earlier event changes the selected outcome.",
            "novelty_basis": "The current candidate tests only one event order.",
        },
        {
            **common,
            "id": "H2",
            "operator": "counterexample",
            "proposal": "One admissible boundary state falsifies the universal result.",
            "novelty_basis": "The current candidate contains no boundary counterexample.",
        },
    ]


def _select_controlled_hypothesis(scenario: str) -> dict[str, Any]:
    arguments = {
        "conflict_class": "order",
        "claim_risks": {"C1": 0.9},
        "operator_stats": {},
        "random_seed": FIXED_RANDOM_SEED,
        "exploration_rate": 0.2,
    }
    first = cognitive_loop.select_hypothesis(
        _controlled_hypotheses(scenario), **arguments
    )
    replay = cognitive_loop.select_hypothesis(
        _controlled_hypotheses(scenario), **arguments
    )
    return {
        **first,
        "replayable": first == replay,
    }


def _stage_runner(verdict: str, call_log: list[dict[str, Any]]):
    def run(name: str, context: dict[str, Any]) -> dict[str, Any]:
        if name != "claim-verifier":
            raise AssertionError(f"unexpected ablation stage: {name}")
        request_fingerprint = claim_ledger.stable_fingerprint(
            "ablation-verification-view-v1", context["verification_view"]
        )
        call_log.append({
            "stage": name,
            "request_fingerprint": request_fingerprint,
            "model_id": "fixed-verifier-model",
            "provider": "fixed-fixture",
        })
        audits = []
        for request in context["verification_view"]["requests"]:
            claim = request["claim"]
            audits.append({
                "claim_id": claim["id"],
                "claim_version": claim["version"],
                "verdict": verdict,
                "normalized_result": f"fixed-{verdict}",
                "decisive_checks": (
                    ["fixed isolated recomputation"] if verdict == "pass" else []
                ),
                "issues": (
                    [] if verdict == "pass" else [f"fixed {verdict} evidence"]
                ),
            })
        return {
            "status": "completed",
            "message": "fixed ablation audit",
            "claim_audits": audits,
            "interface_audit": None,
            "_runtime_identity": {
                "model_id": "fixed-verifier-model",
                "provider": "fixed-fixture",
                "context_isolated": True,
            },
        }

    return run


def _run_mode(
    scenario: str,
    verdict: str,
    *,
    loop_enabled: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    report = w3_pipeline.run_claim_evidence_shadow(
        f"固定条件认知环消融题 [{scenario}]",
        BLUEPRINT,
        SOLVER,
        stage_runner=_stage_runner(verdict, calls),
        risk_decisions=[{"target_id": "q1", "risk": 0.5}],
        cognitive_loop_enabled=loop_enabled,
    )
    return report, calls


def run_ablation(*, generated_at: str | None = None) -> dict[str, Any]:
    cases = []
    for scenario, verdict in SCENARIOS:
        off, off_calls = _run_mode(
            scenario, verdict, loop_enabled=False
        )
        on, on_calls = _run_mode(
            scenario, verdict, loop_enabled=True
        )
        off_final = off["aggregation"]["final_claims"]
        on_final = on["aggregation"]["final_claims"]
        same_request = off_calls == on_calls
        same_input = (
            off["ledger"]["input_fingerprint"]
            == on["ledger"]["input_fingerprint"]
        )
        fault_case = verdict != "pass"
        association = (
            _select_controlled_hypothesis(scenario)
            if fault_case
            else None
        )
        off_false_promotion = (
            fault_case and off["aggregation"]["status"] == "VERIFIED"
        )
        on_false_promotion = (
            fault_case and on["aggregation"]["status"] == "VERIFIED"
        )
        cases.append({
            "scenario": scenario,
            "injected_verdict": verdict,
            "random_seed": FIXED_RANDOM_SEED,
            "same_problem": True,
            "same_solver_model": True,
            "same_verifier_model": True,
            "same_evidence": same_request,
            "same_input_fingerprint": same_input,
            "same_final_answer": off_final == on_final,
            "off": {
                "aggregation_status": off["aggregation"]["status"],
                "verified_claim_count": off["metrics"][
                    "verified_claim_count"
                ],
                "challenge_count": off["metrics"]["challenge_count"],
                "fuse_triggered": off["metrics"]["fuse_triggered"],
                "false_promotion": off_false_promotion,
            },
            "on": {
                "aggregation_status": on["aggregation"]["status"],
                "verified_claim_count": on["metrics"][
                    "verified_claim_count"
                ],
                "challenge_count": on["metrics"]["challenge_count"],
                "fuse_triggered": on["metrics"]["fuse_triggered"],
                "false_promotion": on_false_promotion,
                "controlled_association": association,
            },
        })

    fault_cases = [item for item in cases if item["injected_verdict"] != "pass"]
    off_false = sum(item["off"]["false_promotion"] for item in cases)
    on_false = sum(item["on"]["false_promotion"] for item in cases)
    off_challenges = sum(item["off"]["challenge_count"] for item in cases)
    on_challenges = sum(item["on"]["challenge_count"] for item in cases)
    associations = [
        item["on"]["controlled_association"]
        for item in fault_cases
        if isinstance(item["on"]["controlled_association"], dict)
    ]
    conditions_equal = all(
        item["same_problem"]
        and item["same_solver_model"]
        and item["same_verifier_model"]
        and item["same_evidence"]
        and item["same_input_fingerprint"]
        for item in cases
    )
    return {
        "schema_version": ABLATION_SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "fixed_conditions": {
            "case_count": len(cases),
            "fault_case_count": len(fault_cases),
            "random_seed": FIXED_RANDOM_SEED,
            "conditions_equal": conditions_equal,
        },
        "metrics": {
            "off_false_promotion_count": off_false,
            "on_false_promotion_count": on_false,
            "answer_consistency_rate": round(
                sum(item["same_final_answer"] for item in cases) / len(cases),
                6,
            ),
            "off_challenge_count": off_challenges,
            "on_challenge_count": on_challenges,
            "diagnostic_challenge_gain": on_challenges - off_challenges,
            "on_fault_fuse_count": sum(
                item["on"]["fuse_triggered"] for item in fault_cases
            ),
            "controlled_association_count": len(associations),
            "association_replay_rate": round(
                sum(item["replayable"] for item in associations)
                / len(associations),
                6,
            ),
            "association_truth_promotion_count": sum(
                item["truth_status"] != "candidate" for item in associations
            ),
        },
        "gates": {
            "fixed_conditions_intact": conditions_equal,
            "no_error_promotion_regression": on_false <= off_false == 0,
            "answer_not_mutated": all(
                item["same_final_answer"] for item in cases
            ),
            "diagnostic_gain_positive": on_challenges > off_challenges,
            "controlled_association_replayable": all(
                item["replayable"] for item in associations
            ),
            "association_never_promotes_truth": all(
                item["truth_status"] == "candidate"
                for item in associations
            ),
            "production_authorized": False,
        },
        "cases": cases,
        "conclusion": (
            "The bounded loop adds specific challenges and terminal fuses "
            "without changing the candidate answer or promoting injected "
            "errors. It does not yet demonstrate higher natural-question "
            "accuracy; fresh teacher-scored holdout remains required."
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    conditions = report["fixed_conditions"]
    lines = [
        "# 受控认知环同条件消融报告 v1",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 场景：{conditions['case_count']}；故障场景："
        f"{conditions['fault_case_count']}",
        f"- 固定随机种子：`{conditions['random_seed']}`",
        f"- 同题、同模型、同证据：{str(conditions['conditions_equal']).lower()}",
        f"- 错误晋升 off / on：{metrics['off_false_promotion_count']} / "
        f"{metrics['on_false_promotion_count']}",
        f"- 候选答案一致率：{metrics['answer_consistency_rate']:.1%}",
        f"- Challenge off / on：{metrics['off_challenge_count']} / "
        f"{metrics['on_challenge_count']}",
        f"- 故障场景熔断数（on）：{metrics['on_fault_fuse_count']}",
        f"- 固定种子联想选择：{metrics['controlled_association_count']}；"
        f"重放一致率：{metrics['association_replay_rate']:.1%}；"
        f"真值晋升：{metrics['association_truth_promotion_count']}",
        "",
        "| 场景 | 注入结果 | off 状态 | on 状态 | on Challenge | on 熔断 |",
        "|---|---|---|---|---:|---|",
    ]
    for item in report["cases"]:
        lines.append(
            f"| {item['scenario']} | {item['injected_verdict']} | "
            f"{item['off']['aggregation_status']} | "
            f"{item['on']['aggregation_status']} | "
            f"{item['on']['challenge_count']} | "
            f"{str(item['on']['fuse_triggered']).lower()} |"
        )
    lines.extend([
        "",
        "## 结论",
        "",
        "认知环开启后增加了与具体 Claim 绑定的 Challenge，并在没有新证据时有限熔断；"
        "它没有改写候选答案，也没有把注入错误晋升为正确。",
        "",
        "随机性只用于从受约束、可证伪的候选中选择下一项搜索任务；固定种子可完全重放，"
        "被选中的假设仍保持 `candidate`，不能进入证明 DAG 或改变真值。",
        "",
        "本消融只能证明诊断增强且无错误晋升回归，尚不能证明自然题正确率提高；"
        "后者仍需新鲜、教师评分的独立 holdout。",
    ])
    return "\n".join(lines) + "\n"
