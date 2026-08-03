#!/usr/bin/env python3
"""Deterministic JSON Agent adapter used only by teacher-console tests."""

import json
import sys
import tempfile
from pathlib import Path


def w3_stage_payload(task):
    stage = str(task.get("w3_stage", ""))
    if not stage:
        return None
    context_root = Path(task["entry_dir"]) / ".agent-context"
    problem = ""
    try:
        with open(f"{task['entry_dir']}/problem.md", encoding="utf-8") as handle:
            problem = handle.read()
    except OSError:
        pass
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
                "prompt": "求全部可能结果并核对首次事件",
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


def _fake_core_call_path(entry_dir: Path) -> Path:
    return Path(tempfile.gettempdir()) / f"wuli-fake-core-calls-{entry_dir.name}.txt"


def _fake_core_call_count(entry_dir: Path) -> int:
    """Work-tree T1: deterministic per-entry provider call counter."""
    try:
        return int(_fake_core_call_path(entry_dir).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _fake_rich_student_markdown(claims):
    quick = "\n".join(f"- **{claim['id']}**：{claim['final_answer']}" for claim in claims)
    # The deterministic student gate requires numbered 第 N 步 headings (max
    # five), so group claims exactly like the compact renderer does.
    groups = [[item] for item in claims[:4]]
    if len(claims) > 4:
        groups.append(claims[4:])
    blocks = []
    for index, group in enumerate(groups, 1):
        lines = [f"### 第 {index} 步"]
        for claim in group:
            if len(claims) > 1:
                lines.append(f"\n#### {claim['id']}")
            lines.extend(f"\n- {relation}" for relation in claim["key_relations"])
            lines.append(f"\n因此，{claim['final_answer']}。")
        blocks.append("\n".join(lines))
    return (
        f"## 答案速览\n\n{quick}\n\n"
        "## 一眼识别\n\n"
        "- **最短主线**：逐问建立决定性关系，得到结论后检查题设边界。\n\n"
        "## 详细解答\n\n"
        + "\n\n".join(blocks)
        + "\n\n## 易错点\n\n"
        "- 复核本题的符号、方向、分支与题设边界是否一致。\n\n"
        "## 30 秒自测\n\n"
        "能否只用上述决定性关系，独立复算每个最终结论并检查适用条件？\n"
    )


def core_solve_payload(task, problem):
    contract = task.get("output_contract", {})
    if contract.get("name") not in ("wuli.core-solve.v1", "wuli.core-rich.v2"):
        return None
    rich = contract.get("name") == "wuli.core-rich.v2"
    entry_dir = Path(task["entry_dir"])
    brief = json.loads((entry_dir / ".agent-context" / "target-brief.json").read_text(encoding="utf-8"))
    calls = _fake_core_call_count(entry_dir)
    _fake_core_call_path(entry_dir).write_text(str(calls + 1), encoding="utf-8")
    charged = "带电粒子" in problem
    final_answer = "临界磁感应强度为 $B^*=3mv_0/(qd)$" if charged else "物体的加速度为 $a=F/m$"
    derivation = (
        ["由 $qvB=mv^2/r$ 与临界相切条件联立，解得 $B^*=3mv_0/(qd)$"]
        if charged
        else ["水平方向由牛顿第二定律 $F=ma$，解得 $a=F/m$"]
    )
    gate_reject = (
        "[gate-reject-always]" in problem
        or ("[gate-reject-first-attempt]" in problem and calls == 0)
        or ("[gate-reject-second-attempt]" in problem and calls >= 1)
    )
    if gate_reject:
        # ``v2`` is a subscripted token never defined in any fixture problem,
        # so the deterministic physics gate must reject it with symbol-undefined.
        final_answer = "到达底端的速度为 $v=\\sqrt{2gh}+v2$"
        derivation = ["由机械能守恒列式求解"]
    cross_ref = "[cross-target-ref]" in problem and len(brief["targets"]) >= 2
    first_id = brief["targets"][0]["id"] if brief["targets"] else ""

    def target_answer(item):
        if cross_ref and item["id"] != first_id:
            # A1 scenario: later sub-questions legitimately cite an earlier
            # target id; the physics gate must not flag it as undefined.
            return f"代入 {first_id} 的结果，得临界磁感应强度 $B^*=3mv_0/(qd)$"
        return final_answer

    claims = [
        {
            "id": item["id"],
            "final_answer": target_answer(item),
            "key_relations": [*derivation, "方向、量纲与题设边界复核通过"],
        }
        for item in brief["targets"]
    ]
    base = {
        "status": "completed",
        "message": "fake compact core solve",
        "target_brief_digest": brief["digest"],
        "model": "fake-core-solver",
        "model_tier": "standard",
        "requested_tier": task.get("routing_tier", "auto"),
        "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
    }
    if rich:
        base.update(
            {
                "claims": claims,
                "student_solution": _fake_rich_student_markdown(claims),
                "teacher_audit": "fake 教师审计：核对题设边界与量纲后，本解法为高中范围内的最短主线。",
                "method_check": {
                    "selected_path": "逐问建立决定性关系并核对边界",
                    "decisive_relations": [claim["key_relations"][0] for claim in claims],
                },
            }
        )
        return base
    base["targets"] = claims
    return base


task = json.load(sys.stdin)
stage_payload = w3_stage_payload(task)
if stage_payload is not None:
    stage_payload["model"] = "fake-claim-verifier" if task.get("w3_stage") == "claim-verifier" else "fake-w3-solver"
    print(json.dumps(stage_payload, ensure_ascii=False))
    raise SystemExit(0)

problem = ""
try:
    problem = (Path(task["entry_dir"]) / "problem.md").read_text(encoding="utf-8")
except OSError:
    pass
if task.get("kind") == "source.clean":
    entry = Path(task["entry_dir"])
    record = json.loads((entry / "record.json").read_text(encoding="utf-8"))
    record["title"] = "自动整理后的物理测试题"
    cleaned_problem = problem.replace("# 题目（OCR 草稿）", "# 自动整理后的物理测试题", 1)
    print(
        json.dumps(
            {
                "status": "completed",
                "message": "已自动整理 OCR 题干草稿",
                "files": {
                    "problem.md": cleaned_problem,
                    "record.json": json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                },
                "model": "fake-source-clean",
                "model_tier": "economy",
                "requested_tier": task.get("routing_tier", "economy"),
                "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0)
core_payload = core_solve_payload(task, problem)
if core_payload is not None:
    print(json.dumps(core_payload, ensure_ascii=False))
    raise SystemExit(0)

if task.get("kind") == "diagram.scene":
    facts_path = Path(task["entry_dir"]) / "visual-facts.json"
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    fact_ids = [item["id"] for item in facts.get("diagram_facts", [])]
    print(
        json.dumps(
            {
                "status": "completed",
                "message": "fake typed physics scene",
                "title": "物理过程示意图",
                "panels": [{"id": "p1", "title": "运动区域"}],
                "regions": [
                    {
                        "id": "r1",
                        "panel_id": "p1",
                        "kind": "electric",
                        "x": 8,
                        "y": 12,
                        "width": 84,
                        "height": 72,
                        "label": "物理场区",
                        "fact_ids": fact_ids,
                        "direction": "right",
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
                        "points": [{"x": 18, "y": 58}, {"x": 45, "y": 45}, {"x": 78, "y": 22}],
                        "label": "运动轨迹",
                        "direction": "right",
                        "fact_ids": [],
                    }
                ],
                "annotations": [],
                "omissions": [],
                "model": "fake-diagram",
                "model_tier": "standard",
                "requested_tier": task.get("routing_tier", "auto"),
                "usage": {"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0)

student_solution = (
    "# 解析（学生版）\n\n## 答案速览\n测试结论：按照受力关系建立方程即可得到结果。\n\n"
    "## 一眼识别\n- 最短主线：识别对象 → 建立受力关系 → 检查结论。\n\n"
    "## 详细解答\n### 第 1 步\n识别研究对象并建立物理模型，根据题目条件列出关键关系，"
    "完成计算后检查量纲、方向和边界条件，得到完整且可复核的测试答案。\n\n"
    "## 易错点\n注意正方向的约定，代入数据后还要检查结果是否符合题目给出的物理范围。\n\n"
    "## 30 秒自测\n改变正方向后，方程中的哪些符号需要同步改变？\n"
)
tier = task.get("routing_tier", "auto")
print(
    json.dumps(
        {
            "status": "completed",
            "message": f"handled {task['kind']}",
            "student_solution": student_solution,
            "teacher_audit": (
                "复核时需要确认研究对象、正方向和边界条件保持一致，并检查每一步公式的量纲。"
                "若题目包含多个物理阶段，还应逐段核对状态衔接。"
            ),
            "method_check": {
                "selected_path": "识别对象后直接建立受力关系并检查结论。",
                "high_school_basis": ["受力分析", "牛顿第二定律"],
                "discarded_methods": ["舍弃逐时刻展开"],
                "physical_stages": ["物体在合力作用下运动"],
                "reasoning_steps": ["识别研究对象并建立受力关系"],
                "decisive_relations": ["合外力等于质量与加速度的乘积"],
                "representation_transforms": ["实际物体转换为受力模型"],
                "condition_checks": ["核对方向和题设边界"],
                "type_distance": {
                    "mode": "direct_archetype",
                    "archetype": "受力分析与牛顿第二定律教材母题",
                    "recognition_barrier": "研究对象明确",
                    "novel_bridge": "",
                },
                "student_step_count": 1,
            },
            "metadata": {
                "knowledge_points": ["测试知识点"],
                "error_types": ["方向判断"],
                "difficulty": "中等",
                "grade": "高中",
                "title": "后台任务测试",
            },
            "diagram": None,
            "model": f"fake-{tier}",
            "model_tier": "standard" if tier == "auto" else tier,
            "requested_tier": tier,
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        },
        ensure_ascii=False,
    )
)
