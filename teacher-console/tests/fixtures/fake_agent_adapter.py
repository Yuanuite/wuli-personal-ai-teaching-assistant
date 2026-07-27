#!/usr/bin/env python3
"""Deterministic JSON Agent adapter used only by teacher-console tests."""

import json
import sys

task = json.load(sys.stdin)
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
