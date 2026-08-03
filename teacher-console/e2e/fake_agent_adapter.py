#!/usr/bin/env python3
"""Deterministic Agent adapter for the isolated teacher-console E2E flow."""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path


def w3_stage_payload(task: dict, problem: str) -> dict | None:
    """Mirror every private W3 contract used by the production Gateway."""
    stage = str(task.get("w3_stage", ""))
    if not stage:
        return None
    context_root = Path(task["entry_dir"]) / ".agent-context"
    scenario = (
        "conflict"
        if "[claim-conflict]" in problem
        else "insufficient"
        if "[claim-insufficient]" in problem or "[claim-fuse]" in problem
        else "normal"
    )
    blueprint = {
        "status": "completed",
        "message": "fake W3 blueprint",
        "question_targets": [
            {
                "id": "q1",
                "prompt": (
                    # A4.5 (w3-w3r work-tree): the W3R brief projection requires
                    # final answers of enumerate_all targets to declare
                    # complete=true, which the legacy-projection proof package does
                    # not carry. The [w3r-ready] marker switches the deterministic
                    # fixture to a single-value target so the W3R shadow render can
                    # complete; every pre-existing scenario (no marker) keeps the
                    # original enumerate_all prompt byte-for-byte.
                    "求粒子进入磁场时的速度大小" if "[w3r-ready]" in problem else "求全部可能结果并核对首次事件"
                ),
                "answer_type": "value",
            }
        ],
        "physical_stages": [
            {
                "id": "p1",
                "label": "单阶段运动",
                "entry_conditions": ["t=0"],
                "exit_conditions": ["到达目标事件"],
                "state_carried": ["速度"],
            }
        ],
        "stage_transitions": [],
        "reasoning_steps": [
            {
                "id": "r1",
                "operation": "建立运动关系并枚举首次事件",
                "depends_on": [],
                "target_ids": ["q1"],
                "decisive_relations": ["2×3=6"],
            }
        ],
        "stage_step_links": [{"stage_id": "p1", "step_id": "r1"}],
        "retrieval_needs": [
            {
                "id": "n1",
                "purpose": "核对首次事件的高中方法",
                "query": "首次事件 枚举 边界条件",
                "priority": 3,
                "target_ids": ["q1"],
                "stage_ids": ["p1"],
            }
        ],
        "verification_obligations": [
            {
                "id": "v1",
                "target_id": "q1",
                "check": "核对所有允许分支中的首次事件",
                "risk": "medium",
            }
        ],
    }
    solver_blueprint = blueprint
    try:
        candidate = json.loads((context_root / "w3-blueprint.json").read_text(encoding="utf-8"))
        if isinstance(candidate, dict):
            solver_blueprint = candidate
    except (OSError, json.JSONDecodeError):
        pass
    solver_obligation_ids = [
        str(item.get("id", ""))
        for item in solver_blueprint.get("verification_obligations", [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ]
    solution = {
        "status": "completed",
        "message": "fake W3 solution",
        "targets": [
            {
                "id": "q1",
                "final_answer": "6",
                "supporting_relations": ["2×3=6"],
                "conditions": ["允许经过边界后再次返回"],
                "covered_obligation_ids": solver_obligation_ids,
            }
        ],
        "stage_results": [
            {
                "stage_id": "p1",
                "result": "枚举允许事件后由 2×3=6 得到结论。",
            }
        ],
        "stage_interfaces": [
            {
                "stage_id": "p1",
                "coordinate_frame": "ground",
                "time_origin": "t=0",
                "directions": {"x": "positive along motion"},
                "entry_state": {"speed": "initial speed"},
                "exit_state": {"speed": "speed at target event"},
                "required_entry_keys": ["speed"],
                "carried_state_keys": [],
            }
        ],
        "stage_transitions": [],
        "option_verdicts": [],
        "blueprint_audit": {
            "status": "followed",
            "covered_target_ids": ["q1"],
            "covered_obligation_ids": solver_obligation_ids,
            "revisions": [],
        },
    }
    if stage == "decompose":
        return blueprint
    if stage in {"solver-a", "solver-b"}:
        return solution
    if stage == "verifier":
        target_verdict = (
            "conflict" if scenario == "conflict" else "insufficient" if scenario == "insufficient" else "pass"
        )
        return {
            "status": "completed",
            "message": "fake target audit",
            "target_audits": [
                {
                    "target_id": "q1",
                    "verdict": target_verdict,
                    "recomputed_result": "6" if target_verdict == "pass" else "待定",
                    "decisive_checks": (["独立复算 2×3=6"] if target_verdict == "pass" else []),
                    "issues": ([] if target_verdict == "pass" else [f"injected target {target_verdict}"]),
                }
            ],
        }
    if stage == "adjudicator":
        return {
            "status": "completed",
            "message": "fake adjudication",
            "target_decisions": [
                {
                    "target_id": "q1",
                    "selected_result": "6",
                    "decision": "recomputed",
                    "decisive_relation": "2×3=6",
                    "reason": "独立关系与题设一致",
                }
            ],
        }
    if stage == "claim-verifier":
        view_path = context_root / "w3-verification_view.json"
        view = json.loads(view_path.read_text(encoding="utf-8"))
        verdict = {
            "normal": "pass",
            "conflict": "conflict",
            "insufficient": "insufficient",
        }[scenario]
        return {
            "status": "completed",
            "message": f"fake claim audit {scenario}",
            "interface_audit": {
                "verdict": "pass",
                "decisive_checks": ["single-stage interface is complete"],
                "issues": [],
            },
            "claim_audits": [
                {
                    "claim_id": request["claim"]["id"],
                    "claim_version": request["claim"]["version"],
                    "verdict": verdict,
                    "normalized_result": (
                        "independently recomputed as 6"
                        if verdict == "pass"
                        else "recomputed value disagrees"
                        if verdict == "conflict"
                        else "source facts do not determine the boundary branch"
                    ),
                    "decisive_checks": (["independently checked 2×3=6"] if verdict == "pass" else []),
                    "issues": (
                        []
                        if verdict == "pass"
                        else ["injected semantic conflict"]
                        if verdict == "conflict"
                        else ["injected insufficient evidence"]
                    ),
                }
                for request in view.get("requests", [])
            ],
        }
    raise ValueError(f"unsupported fake W3 stage: {stage}")


def core_solve_payload(task: dict, problem: str) -> dict | None:
    if task.get("output_contract", {}).get("name") != "wuli.core-solve.v1":
        return None
    brief = json.loads((Path(task["entry_dir"]) / ".agent-context" / "target-brief.json").read_text(encoding="utf-8"))
    charged = "带电粒子" in problem
    final_answer = "临界磁感应强度为 $B^*=3mv_0/(qd)$" if charged else "物体的加速度为 $a=F/m$"
    derivation = (
        ["由 $qvB=mv^2/r$ 与临界相切条件联立，解得 $B^*=3mv_0/(qd)$"]
        if charged
        else ["水平方向由牛顿第二定律 $F=ma$，解得 $a=F/m$"]
    )
    return {
        "status": "completed",
        "message": "fake compact core solve",
        "target_brief_digest": brief["digest"],
        "targets": [
            {
                "id": item["id"],
                "final_answer": final_answer,
                "key_relations": [*derivation, "方向、量纲与题设边界复核通过"],
            }
            for item in brief["targets"]
        ],
        "model": "fake-e2e-core",
        "model_tier": "standard",
        "requested_tier": task.get("routing_tier", "auto"),
        "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
    }


def visualization_model(entry_id: str, problem: str) -> dict:
    return {
        "schema_version": 1,
        "model_type": "electric-to-bounded-magnetic",
        "entry_id": entry_id,
        "title": "带电粒子先经电场再进入有界磁场",
        "source": {
            "problem": problem,
            "diagram": "粒子从 P 点经电场到 Q 点，再进入垂直纸面向外的有界磁场。",
        },
        "regions": [
            {"id": "electric", "field": {"type": "uniform-electric", "direction": "down"}},
            {"id": "magnetic", "field": {"type": "uniform-magnetic", "direction": "out-of-page"}},
        ],
        "facts": {
            "xq_over_h": 2 * math.sqrt(3) / 3,
            "vq_over_v0": 2,
            "theta_deg": 60,
            "tangent_radius_over_d": 2 / 3,
            "b_coefficient": 3,
            "magnetic_sweep_deg": 120,
        },
        "technique_ids": ["charged-particle-electric-magnetic"],
        "event_model": {
            "stop_event_id": "lower-boundary",
            "timeline": [
                {
                    "id": "start-p",
                    "order": 0,
                    "label": "从 P 点出发",
                    "region": "electric",
                    "p_candidate": False,
                },
                {
                    "id": "enter-q",
                    "order": 1,
                    "label": "到达 Q 点并进入磁场",
                    "region": "magnetic",
                    "p_candidate": False,
                },
                {
                    "id": "lower-boundary",
                    "order": 2,
                    "label": "首次到达磁场下边界",
                    "region": "magnetic",
                    "p_candidate": False,
                },
            ],
            "cases": [{"id": "tangent", "label": "临界相切", "valid": True}],
        },
        "student_solution": {
            "quick_answers": ["临界磁感应强度满足 B*=3mv0/(qd)。"],
            "recognition": "先处理电场中的类平抛，再用洛伦兹力提供向心力。",
            "main_steps": [
                {
                    "title": "确定入磁场速度",
                    "formulae": ["v_Q=2v_0"],
                },
                {
                    "title": "使用临界相切条件",
                    "formulae": ["qv_QB=mv_Q^2/r", "B^*=3mv_0/(qd)"],
                },
            ],
            "pitfalls": ["磁场不做功，速度大小在磁场中不变。"],
            "self_check": ["B 的量纲为 T，临界轨迹与下边界相切。"],
        },
        "teacher_audit": {
            "checks": [
                "独立核对电场段速度分量",
                "核对磁场中圆心方向和相切半径",
            ]
        },
        "simulation": {
            "default_duration_seconds": 6,
            "layers": ["trajectory", "force", "geometry"],
            "default_layer": "trajectory",
            "default_case_id": "tangent",
            "pause_event_ids": ["enter-q", "lower-boundary"],
            "parameters": {
                "h_over_d": {"min": 0.5, "max": 1.5, "step": 0.05, "default": 1},
                "b_ratio": {"min": 0.7, "max": 1.3, "step": 0.05, "default": 1},
            },
        },
    }


def main() -> int:
    task = json.load(sys.stdin)
    entry = Path(task["entry_dir"])
    problem = (entry / "problem.md").read_text(encoding="utf-8")
    # A4.5 (w3-w3r work-tree): additive hard-kill fixture. The [e2e-hang]
    # marker makes a core-solve child ignore its deadlines entirely, so the
    # Gateway must hard-kill it and record timeout_layer=attempt_hard with an
    # empty child stdout (never any fabricated token usage). source.clean and
    # the W3 stages are untouched: the marker only affects the compact core
    # solve contract.
    if (
        "[e2e-hang]" in problem
        and isinstance(task.get("output_contract"), dict)
        and task["output_contract"].get("name") == "wuli.core-solve.v1"
    ):
        time.sleep(600)
        return 0
    stage_payload = w3_stage_payload(task, problem)
    if stage_payload is not None:
        stage_payload["model"] = "fake-claim-verifier" if task.get("w3_stage") == "claim-verifier" else "fake-w3-solver"
        print(json.dumps(stage_payload, ensure_ascii=False))
        return 0
    core_payload = core_solve_payload(task, problem)
    if core_payload is not None:
        print(json.dumps(core_payload, ensure_ascii=False))
        return 0
    if task["kind"] == "visualization.model":
        files = {
            "physics-model.json": json.dumps(
                visualization_model(task["entry_id"], problem),
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        }
        print(
            json.dumps(
                {
                    "status": "completed",
                    "message": "已生成并校验带电粒子电场—磁场交互模型",
                    "files": files,
                    "model": "fake-e2e",
                    "model_tier": "standard",
                    "requested_tier": task.get("routing_tier", "auto"),
                    "usage": {
                        "prompt_tokens": 180,
                        "completion_tokens": 90,
                        "total_tokens": 270,
                    },
                },
                ensure_ascii=False,
            )
        )
        return 0

    if task["kind"] == "diagram.scene":
        facts = json.loads((entry / "visual-facts.json").read_text(encoding="utf-8"))
        fact_ids = [item["id"] for item in facts.get("diagram_facts", [])]
        print(
            json.dumps(
                {
                    "status": "completed",
                    "message": "fake typed physics scene",
                    "title": "物理过程示意图",
                    "panels": [{"id": "p1", "title": "场区与轨迹"}],
                    "regions": [
                        {
                            "id": "r1",
                            "panel_id": "p1",
                            "kind": "magnetic",
                            "x": 8,
                            "y": 12,
                            "width": 84,
                            "height": 72,
                            "label": "场区",
                            "fact_ids": fact_ids,
                            "direction": "into-page",
                        }
                    ],
                    "objects": [
                        {
                            "id": "o1",
                            "panel_id": "p1",
                            "kind": "particle",
                            "x": 15,
                            "y": 55,
                            "width": 4,
                            "height": 6,
                            "label": "粒子",
                            "fact_ids": [],
                            "polarity": "positive",
                        }
                    ],
                    "paths": [
                        {
                            "id": "t1",
                            "panel_id": "p1",
                            "kind": "trajectory",
                            "geometry": "smooth",
                            "points": [{"x": 18, "y": 58}, {"x": 45, "y": 38}, {"x": 78, "y": 25}],
                            "label": "运动轨迹",
                            "direction": "right",
                            "fact_ids": [],
                        }
                    ],
                    "annotations": [],
                    "omissions": [],
                    "model": "fake-e2e",
                    "model_tier": "standard",
                    "requested_tier": task.get("routing_tier", "auto"),
                    "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
                },
                ensure_ascii=False,
            )
        )
        return 0

    record = json.loads((entry / "record.json").read_text(encoding="utf-8"))
    charged_particle = "带电粒子" in problem
    if charged_particle:
        record.update({
            "title": "带电粒子电场—磁场端到端测试",
            "grade": "高二",
            "difficulty": "中等",
            "knowledge_points": ["带电粒子在电场中的运动", "洛伦兹力", "圆周运动"],
            "error_types": ["临界条件"],
        })
        solution = (
            "# 解析\n\n"
            "## 答案速览\n\n临界磁感应强度为 $B^*=3mv_0/(qd)$。\n\n"
            "## 一眼识别\n\n- 最短主线：确定入场速度 → 用圆周运动和相切条件求临界磁场。\n\n"
            "## 详细解答\n\n### 第 1 步\n\n先分解电场段运动，粒子到达 Q 点时速度大小为 $2v_0$，"
            "方向与边界成 $60^\\circ$。进入磁场后洛伦兹力只改变速度方向，"
            "由 $qv_QB=mv_Q^2/r$ 与轨迹恰好和下边界相切的几何条件 $r=2d/3$，"
            "得到 $B^*=3mv_0/(qd)$。\n\n"
            "## 易错点\n\n磁场不做功；圆心必须位于洛伦兹力方向。\n\n"
            "## 30 秒自测\n\n若磁场方向反向，轨迹圆心位于速度方向的哪一侧？\n\n"
            "## 关联知识\n\n类平抛、洛伦兹力、圆周运动和临界相切。\n\n"
            "![受力示意](assets/explanation.svg)\n"
        )
    else:
        record.update({
            "title": "牛顿第二定律端到端测试",
            "grade": "高一",
            "difficulty": "基础",
            "knowledge_points": ["牛顿第二定律", "受力分析"],
            "error_types": ["公式应用"],
        })
        solution = (
            "# 解析\n\n"
            "## 答案速览\n\n物体的加速度为 $a=F/m$。\n\n"
            "## 一眼识别\n\n- 最短主线：受力分析 → 牛顿第二定律。\n\n"
            "## 详细解答\n\n### 第 1 步\n\n先对物体进行受力分析。水平方向合力为 $F$，"
            "由牛顿第二定律 $F=ma$，可得 $a=F/m$。结果方向与合力方向一致，量纲为加速度。\n\n"
            "## 易错点\n\n不能把力直接当成加速度，还要除以物体质量。\n\n"
            "## 30 秒自测\n\n合力方向改变时，加速度方向如何变化？\n\n"
            "## 关联知识\n\n牛顿第二定律、合力与加速度方向。\n\n"
            "![关键关系示意图](assets/explanatory.svg)\n"
        )
    if task["kind"] == "source.clean":
        cleaned_problem = problem.replace("# 题目（OCR 草稿）", f"# {record['title']}", 1)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "message": "已自动整理 OCR 题干草稿",
                    "files": {
                        "problem.md": cleaned_problem,
                        "record.json": json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    },
                    "model": "fake-e2e",
                    "model_tier": "economy",
                    "requested_tier": task.get("routing_tier", "economy"),
                    "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
                },
                ensure_ascii=False,
            )
        )
        return 0
    if task["kind"] == "analysis.generate":
        student_solution = solution.replace(
            "\n![受力示意](assets/explanation.svg)\n",
            "\n",
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "message": "已生成结构化分层解析",
                    "student_solution": student_solution,
                    "teacher_audit": (
                        "教师复核时应确认研究对象、正方向和边界条件保持一致，并逐步检查公式、量纲与最终结论。"
                    ),
                    "method_check": {
                        "selected_path": (
                            "确定入场速度后用圆周运动与相切条件求解。"
                            if charged_particle
                            else "受力分析后直接应用牛顿第二定律。"
                        ),
                        "high_school_basis": (
                            ["类平抛", "洛伦兹力", "圆周运动", "相切条件"]
                            if charged_particle
                            else ["受力分析", "牛顿第二定律"]
                        ),
                        "discarded_methods": ["舍弃逐时刻坐标展开"],
                        "physical_stages": (
                            ["电场加速阶段", "进入磁场后的圆周运动阶段"]
                            if charged_particle
                            else ["物体在恒定合力作用下运动"]
                        ),
                        "reasoning_steps": [
                            (
                                "确定入场速度后用圆周运动与相切条件求解"
                                if charged_particle
                                else "受力分析后直接应用牛顿第二定律"
                            )
                        ],
                        "decisive_relations": (
                            ["电场段运动关系", "洛伦兹力提供向心力", "轨迹与边界相切"]
                            if charged_particle
                            else ["合外力等于质量与加速度的乘积"]
                        ),
                        "representation_transforms": (
                            ["题干运动过程转换为分段模型", "磁场轨迹转换为相切圆几何"]
                            if charged_particle
                            else ["实际物体转换为受力模型"]
                        ),
                        "condition_checks": (
                            ["核对相切临界条件", "核对洛伦兹力方向"] if charged_particle else ["核对合力与加速度方向"]
                        ),
                        "type_distance": (
                            {
                                "mode": "standard_transfer",
                                "archetype": "电场加速后进入磁场的有界圆周运动",
                                "recognition_barrier": "需要把轨迹转换为相切圆几何",
                                "novel_bridge": "相切条件连接运动与边界",
                            }
                            if charged_particle
                            else {
                                "mode": "direct_archetype",
                                "archetype": "受力分析与牛顿第二定律教材母题",
                                "recognition_barrier": "研究对象明确",
                                "novel_bridge": "",
                            }
                        ),
                        "student_step_count": 1,
                    },
                    "metadata": {
                        "knowledge_points": record["knowledge_points"],
                        "error_types": record["error_types"],
                        "difficulty": record["difficulty"],
                        "grade": record["grade"],
                        "title": record["title"],
                    },
                    "diagram": None,
                    "model": "fake-e2e",
                    "model_tier": "standard",
                    "requested_tier": task.get("routing_tier", "auto"),
                    "usage": {
                        "prompt_tokens": 120,
                        "completion_tokens": 30,
                        "total_tokens": 150,
                    },
                },
                ensure_ascii=False,
            )
        )
        return 0

    files = {
        "record.json": json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        "student-solution.md": solution,
        "teacher-solution.md": solution,
        "solution.md": solution,
        "assets/explanation.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120" '
            'viewBox="0 0 320 120" role="img" aria-label="受力示意">'
            '<rect width="320" height="120" fill="white"/>'
            '<rect x="105" y="35" width="90" height="50" fill="#dbeafe" stroke="#1d4ed8"/>'
            '<path d="M195 60h80" stroke="#dc2626" stroke-width="4"/>'
            '<path d="m275 60-14-9v18z" fill="#dc2626"/>'
            '<text x="238" y="48" font-size="18">F</text></svg>'
        ),
    }
    print(
        json.dumps(
            {
                "status": "completed",
                "message": f"handled {task['kind']}",
                "files": files,
                "model": "fake-e2e",
                "model_tier": "standard",
                "requested_tier": task.get("routing_tier", "auto"),
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
