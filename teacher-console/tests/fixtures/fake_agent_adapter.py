#!/usr/bin/env python3
"""Deterministic JSON Agent adapter used only by teacher-console tests."""

import json
import sys

task = json.load(sys.stdin)
student_solution = (
    "# 解析（学生版）\n\n## 答案速览\n测试结论：按照受力关系建立方程即可得到结果。\n\n"
    "## 详细解答\n第一步识别研究对象并建立物理模型；第二步根据题目条件列出关键关系；"
    "第三步完成计算，并检查量纲、方向和边界条件，得到完整且可复核的测试答案。\n\n"
    "## 易错点\n注意正方向的约定，代入数据后还要检查结果是否符合题目给出的物理范围。\n"
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
            "metadata": {
                "knowledge_points": ["测试知识点"],
                "error_types": ["方向判断"],
                "difficulty": "中等",
                "grade": "高中",
                "title": "后台任务测试",
            },
            "diagram": {
                "title": "测试解题逻辑",
                "nodes": ["识别对象", "建立关系", "计算检查", "得到结论"],
            },
            "model": f"fake-{tier}",
            "model_tier": "standard" if tier == "auto" else tier,
            "requested_tier": tier,
            "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
        },
        ensure_ascii=False,
    )
)
