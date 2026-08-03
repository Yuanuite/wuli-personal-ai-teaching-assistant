#!/usr/bin/env python3
"""Read-only metrics for correctness faults and Claim Evidence shadow runs."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import correctness_faults

METRICS_SCHEMA = "wuli.correctness-evidence-metrics.v1"


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _shadow_scenarios(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if summary is None:
        return []
    if summary.get("status") != "passed":
        raise ValueError("claim-evidence shadow summary must have passed")
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("claim-evidence shadow summary has no scenarios")
    required = {
        "name",
        "aggregation_status",
        "claim_count",
        "certificate_count",
        "verified_claim_count",
        "critical_certificate_coverage",
        "unresolved_claim_count",
        "repeated_task_count",
        "loop_transition_count",
        "canonical_unchanged",
    }
    normalized = []
    for raw in scenarios:
        if not isinstance(raw, dict) or not required.issubset(raw):
            missing = sorted(required - set(raw if isinstance(raw, dict) else {}))
            raise ValueError(f"claim-evidence shadow scenario is incomplete: {missing}")
        normalized.append(dict(raw))
    return normalized


def build_report(
    fault_cases: list[dict[str, Any]],
    *,
    shadow_summary: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    outcomes = correctness_faults.run_fault_suite(fault_cases)
    scenarios = _shadow_scenarios(shadow_summary)
    detected = sum(item["detected"] for item in outcomes)
    false_promotions = sum(item["false_promotion"] for item in outcomes)
    category_counts = Counter(item["category"] for item in outcomes)
    category_detected = Counter(item["category"] for item in outcomes if item["detected"])
    backjump = next(
        (item["detail"] for item in outcomes if item["category"] == "backjump"),
        {},
    )
    hypothesis_attempts = sum(int(item["detail"].get("hypothesis_attempt_count", 0)) for item in outcomes)
    duplicate_hypotheses = sum(int(item["detail"].get("duplicate_hypothesis_count", 0)) for item in outcomes)

    shadow_claims = sum(int(item["claim_count"]) for item in scenarios)
    shadow_verified = sum(int(item["verified_claim_count"]) for item in scenarios)
    repeated_tasks = sum(int(item["repeated_task_count"]) for item in scenarios)
    transitions = sum(int(item["loop_transition_count"]) for item in scenarios)
    hard_unresolved = sum(item["aggregation_status"] == "UNRESOLVED" for item in scenarios)
    non_verified = sum(item["aggregation_status"] != "VERIFIED" for item in scenarios)
    canonical_mutations = sum(not bool(item["canonical_unchanged"]) for item in scenarios)
    critical_coverages = [float(item["critical_certificate_coverage"]) for item in scenarios]

    fault_metrics = {
        "case_count": len(outcomes),
        "detected_count": detected,
        "detection_rate": _ratio(detected, len(outcomes)),
        "false_promotion_count": false_promotions,
        "by_category": {
            category: {
                "case_count": count,
                "detected_count": category_detected[category],
                "detection_rate": _ratio(category_detected[category], count),
            }
            for category, count in sorted(category_counts.items())
        },
    }
    shadow_metrics = {
        "scenario_count": len(scenarios),
        "claim_count": shadow_claims,
        "certificate_count": sum(int(item["certificate_count"]) for item in scenarios),
        "verified_claim_rate": _ratio(shadow_verified, shadow_claims),
        "mean_critical_certificate_coverage": (
            round(sum(critical_coverages) / len(critical_coverages), 6) if critical_coverages else None
        ),
        "repeated_task_count": repeated_tasks,
        "repeated_task_rate": _ratio(repeated_tasks, transitions + repeated_tasks),
        "non_verified_rate": _ratio(non_verified, len(scenarios)),
        "hard_unresolved_rate": _ratio(hard_unresolved, len(scenarios)),
        "canonical_mutation_count": canonical_mutations,
    }
    loop_metrics = {
        "backjump_precision": backjump.get("backjump_precision"),
        "backjump_recall": backjump.get("backjump_recall"),
        "duplicate_hypothesis_count": duplicate_hypotheses,
        "duplicate_hypothesis_rate": _ratio(duplicate_hypotheses, hypothesis_attempts),
    }
    gates = {
        "zero_false_promotion": false_promotions == 0,
        "all_declared_faults_detected": detected == len(outcomes),
        "backjump_exact": (backjump.get("backjump_precision") == 1.0 and backjump.get("backjump_recall") == 1.0),
        "no_repeated_shadow_task": (bool(scenarios) and repeated_tasks == 0),
        "shadow_canonical_unchanged": (bool(scenarios) and canonical_mutations == 0),
        "production_authorized": False,
    }
    return {
        "schema_version": METRICS_SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": "deterministic-faults-and-fake-adapter-shadow",
        "fault_metrics": fault_metrics,
        "shadow_metrics": shadow_metrics,
        "loop_metrics": loop_metrics,
        "gates": gates,
        "fault_outcomes": outcomes,
        "shadow_scenarios": scenarios,
        "limitations": [
            "故障集只证明已声明检测器的行为，不证明自然题准确率。",
            "影子场景使用隔离 fake adapter，不是新鲜 holdout 证据。",
            "旧 W3 没有结构化阶段接口，因此正常场景仍保守标记为 PROVISIONAL。",
            "本报告不能授权生产，也不能绕过 WAIT-5。",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    fault = report["fault_metrics"]
    shadow = report["shadow_metrics"]
    loop = report["loop_metrics"]
    lines = [
        "# 教学正确性证据链指标报告 v1",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 评测范围：`{report['evaluation_scope']}`",
        f"- 故障发现：{fault['detected_count']}/{fault['case_count']} （{fault['detection_rate']:.1%}）",
        f"- 错误晋升：{fault['false_promotion_count']}",
        f"- 影子 Claim：{shadow['claim_count']}；证书：{shadow['certificate_count']}",
        f"- 已验证 Claim 比例：{shadow['verified_claim_rate'] if shadow['verified_claim_rate'] is not None else 'N/A'}",
        f"- 关键 Claim 证书覆盖均值："
        f"{shadow['mean_critical_certificate_coverage'] if shadow['mean_critical_certificate_coverage'] is not None else 'N/A'}",  # noqa: E501
        f"- 重复任务率：{shadow['repeated_task_rate'] if shadow['repeated_task_rate'] is not None else 'N/A'}",
        f"- 回跳 precision / recall：{loop['backjump_precision']} / {loop['backjump_recall']}",
        f"- 未验证场景率 / 硬冲突率：{shadow['non_verified_rate']} / {shadow['hard_unresolved_rate']}",
        "",
        "## 故障分类",
        "",
        "| 分类 | 检出 | 总数 | 检出率 |",
        "|---|---:|---:|---:|",
    ]
    for category, item in fault["by_category"].items():
        lines.append(f"| {category} | {item['detected_count']} | {item['case_count']} | {item['detection_rate']:.1%} |")
    lines.extend([
        "",
        "## 门禁",
        "",
    ])
    for gate, passed in report["gates"].items():
        lines.append(f"- `{gate}`：{str(passed).lower()}")
    lines.extend([
        "",
        "## 解释边界",
        "",
    ])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"
