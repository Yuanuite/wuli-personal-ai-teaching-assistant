#!/usr/bin/env python3
"""Deterministic JSON Agent adapter used only by teacher-console tests."""

import json
import sys


task = json.load(sys.stdin)
solution = (
    "# 解析\n\n## 答案速览\n测试结论。\n\n## 详细解答\n"
    "第一步建立模型，第二步列式，第三步检查量纲与边界条件，得到完整且可复核的测试答案。\n\n"
    "## 易错点\n注意方向。\n\n![解释图](assets/explanation.svg)\n"
)
files = {
    "student-solution.md": solution,
    "teacher-solution.md": solution,
    "solution.md": solution,
    "assets/explanation.svg": '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
}
tier = task.get("routing_tier", "auto")
print(json.dumps({
    "status": "completed",
    "message": f"handled {task['kind']}",
    "files": files,
    "model": f"fake-{tier}",
    "model_tier": "standard" if tier == "auto" else tier,
    "requested_tier": tier,
    "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
}, ensure_ascii=False))
