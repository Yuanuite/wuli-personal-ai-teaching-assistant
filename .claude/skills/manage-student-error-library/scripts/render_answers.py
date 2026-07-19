#!/usr/bin/env python3
"""Render layered answer Markdown from the shared physics model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import kb


def student_markdown(model: dict) -> str:
    source, solution = model["source"], model["student_solution"]
    lines = ["# 解析（学生版）", "", f"![关键关系示意图]({source['diagram']}){{width=62%}}", "", "## 答案速览", ""]
    lines.extend(f"- {item}" for item in solution["quick_answers"])
    lines.extend(["", "## 一眼识别", ""])
    lines.extend(f"- {item}" for item in solution["recognition"])
    lines.extend(["", "## 详细解答", ""])
    for step in solution["main_steps"]:
        lines.extend([f"### {step['heading']}", "", step["body"], ""])
        for formula in step.get("formulae", []):
            lines.extend(["$$", formula, "$$", ""])
    lines.extend(["## 易错点", ""])
    lines.extend(f"- {item}" for item in solution["pitfalls"])
    lines.extend(["", "## 30 秒自测", "", solution["self_check"], ""])
    return "\n".join(lines)


def teacher_markdown(model: dict) -> str:
    base = student_markdown(model).replace("# 解析（学生版）", "# 解析（教师版）", 1)
    audit, lines = model["teacher_audit"], [base, "## 教师审计", ""]
    lines.extend(f"- {item}" for item in audit.get("checks", []))
    if audit.get("duplicate_cases"):
        lines.extend(["", "### 重复情况", ""])
        lines.extend(f"- {item}" for item in audit["duplicate_cases"])
    if audit.get("valid_delta_range"):
        lines.extend(["", f"有效角度范围：${audit['valid_delta_range']}$。", ""])
    return "\n".join(lines)


def render(model_path: Path, entry_dir: Path, update_canonical: bool = True) -> dict:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    student_path, teacher_path = entry_dir / "student-solution.md", entry_dir / "teacher-solution.md"
    student_path.write_text(student_markdown(model), encoding="utf-8")
    teacher = teacher_markdown(model)
    teacher_path.write_text(teacher, encoding="utf-8")
    if update_canonical:
        (entry_dir / "solution.md").write_text(teacher, encoding="utf-8")
    rebuilt = None
    if entry_dir.parent.name == "entries":
        rebuilt = kb.rebuild_index(entry_dir.parent.parent)
    return {"student": str(student_path.resolve()), "teacher": str(teacher_path.resolve()), "kb_rebuild": bool(rebuilt)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--entry-dir", type=Path, required=True)
    parser.add_argument("--no-update-canonical", action="store_true")
    args = parser.parse_args()
    print(json.dumps(render(args.model.resolve(), args.entry_dir.resolve(), not args.no_update_canonical), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
