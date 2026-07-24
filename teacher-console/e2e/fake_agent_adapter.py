#!/usr/bin/env python3
"""Deterministic Agent adapter for the isolated teacher-console E2E flow."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


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
    if task["kind"] == "visualization.model":
        problem = (entry / "problem.md").read_text(encoding="utf-8")
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

    problem = (entry / "problem.md").read_text(encoding="utf-8")
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
            "## 详细解答\n\n先分解电场段运动，粒子到达 Q 点时速度大小为 $2v_0$，"
            "方向与边界成 $60^\\circ$。进入磁场后洛伦兹力只改变速度方向，"
            "由 $qv_QB=mv_Q^2/r$ 与轨迹恰好和下边界相切的几何条件 $r=2d/3$，"
            "得到 $B^*=3mv_0/(qd)$。\n\n"
            "## 易错点\n\n磁场不做功；圆心必须位于洛伦兹力方向。\n\n"
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
            "## 详细解答\n\n先对物体进行受力分析。水平方向合力为 $F$，"
            "由牛顿第二定律 $F=ma$，可得 $a=F/m$。结果方向与合力方向一致，量纲为加速度。\n\n"
            "## 易错点\n\n不能把力直接当成加速度，还要除以物体质量。\n\n"
            "## 关联知识\n\n牛顿第二定律、合力与加速度方向。\n\n"
            "![受力示意](assets/explanation.svg)\n"
        )
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
