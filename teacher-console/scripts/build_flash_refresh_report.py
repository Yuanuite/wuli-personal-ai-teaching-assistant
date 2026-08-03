#!/usr/bin/env python3
"""Build an answer-safe report for the refreshed Flash competition test."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import statistics
import zipfile
from datetime import datetime
from pathlib import Path


REVIEW = {
    1: ["optical-surface-equation-wrong", "beam-radius-ratio-wrong"],
    2: ["work-result-wrong", "process-feasibility-wrong", "heat-result-wrong"],
    3: ["collision-recurrence-wrong", "no-slip-check-inconsistent"],
    4: ["dipole-interaction-energy-wrong", "equilibrium-angle-wrong"],
    5: ["thermal-speed-wrong", "scattering-acceleration-wrong"],
    6: ["reach-condition-wrong", "induced-motion-wrong"],
    7: ["vehicle-dynamics-wrong", "friction-threshold-wrong"],
    8: ["motor-numerics-wrong", "energy-efficiency-wrong"],
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build(primary_path: Path, retry_path: Path, gold_path: Path, output: Path) -> None:
    primary = load(primary_path)
    retry = load(retry_path)
    retry_by_question = {item["question"]: item for item in retry["questions"]}
    rows = []
    for item in primary["questions"]:
        question = item["question"]
        recovery = retry_by_question.get(question)
        recovered = item["status"] != "completed" and bool(
            recovery and recovery.get("status") == "completed"
        )
        elapsed = (
            item.get("elapsed_seconds")
            if item["status"] == "completed"
            else (recovery or {}).get("elapsed_seconds")
        )
        rows.append(
            {
                "question": question,
                "first_attempt_status": item["status"],
                "first_attempt_elapsed_seconds": item.get("elapsed_seconds"),
                "failure_type": item.get("error_type", ""),
                "recovered_by_budget_retry": recovered,
                "recovery_elapsed_seconds": (recovery or {}).get("elapsed_seconds"),
                "candidate_available": item["status"] == "completed" or recovered,
                "strict_whole_question_correct": False,
                "teaching_approved": False,
                "reason_codes": REVIEW[question],
                "display_elapsed_seconds": elapsed,
            }
        )
    first_success = sum(row["first_attempt_status"] == "completed" for row in rows)
    recovered_success = sum(row["candidate_available"] for row in rows)
    first_latencies = [
        row["first_attempt_elapsed_seconds"]
        for row in rows
        if row["first_attempt_elapsed_seconds"] is not None
    ]
    summary = {
        "question_count": 8,
        "first_attempt_structural_success_count": first_success,
        "first_attempt_structural_success_rate": first_success / 8,
        "after_one_budget_retry_success_count": recovered_success,
        "after_one_budget_retry_success_rate": recovered_success / 8,
        "first_attempt_90s_end_to_end_pass_count": first_success,
        "first_attempt_90s_end_to_end_pass_rate": first_success / 8,
        "strict_whole_question_correct_count": 0,
        "strict_whole_question_accuracy": 0.0,
        "teaching_approved_count": 0,
        "median_completed_first_attempt_seconds": round(
            statistics.median(first_latencies), 3
        ),
        "max_completed_first_attempt_seconds": round(max(first_latencies), 3),
    }
    telemetry = {
        "schema_version": 1,
        "generated_at": now(),
        "model_id": primary["model_id"],
        "model": primary["model"],
        "transport": primary["transport"],
        "gate_profile": primary["gate_profile"],
        "reviewer": "official-standard-answer-surrogate",
        "gold_sha256": digest(gold_path),
        "gold_used_before_freeze": False,
        "feedback_returned_to_model": False,
        "summary": summary,
        "questions": rows,
    }
    lines = [
        "# CPhO 2021 新版 Flash 最小门禁盲测",
        "",
        f"- 生成时间：{telemetry['generated_at']}",
        "- 覆盖：第 38 届全国中学生物理竞赛复赛理论题，全年 8 题",
        "- 模型：`deepseek-v4-flash`，OpenAI-compatible API 直连",
        "- 门禁：仅保留完整小问覆盖、最终答案、至多四条关键关系和合法 JSON",
        "- 已移除：W3 拆解、RAG、风险验证、Solver B、仲裁、阶段接口和首次教学渲染",
        "- 标准答案在候选冻结后代替教师审核；无用户参与、无答案回灌",
        "",
        "## 结果",
        "",
        f"- 一次调用结构成功：**{first_success}/8（{first_success/8:.1%}）**",
        f"- 允许一次纯预算重试后有候选：**{recovered_success}/8（{recovered_success/8:.0%}）**",
        f"- 一次调用 90 秒端到端通过：**{first_success}/8（{first_success/8:.1%}）**",
        f"- 已完成首轮中位耗时：**{summary['median_completed_first_attempt_seconds']:.1f}s**；"
        f"最慢 **{summary['max_completed_first_attempt_seconds']:.1f}s**",
        "- 严格整题正确：**0/8（0%）**",
        "- 教学成品批准：**0/8（0%）**",
        "",
        "第 7 题首轮因 6K 输出上限失败；10K 预算重试在 7.2 秒形成候选。"
        "该候选只计结构恢复，不计首次 SLA 通过。其余七题虽在 4.6–11.2 秒形成核心候选，"
        "但标准答案审核均发现至少一个决定整题结论的物理或数值错误。",
        "",
        "## 逐题审核",
        "",
        "| 题 | 首轮结构 | 首轮耗时(s) | 重试恢复 | 整题正确 | 原因码 |",
        "|---:|:---:|---:|:---:|:---:|---|",
    ]
    for row in rows:
        elapsed = row["first_attempt_elapsed_seconds"]
        lines.append(
            f"| {row['question']} | {'成功' if row['first_attempt_status']=='completed' else '失败'} | "
            f"{f'{elapsed:.1f}' if elapsed is not None else '不可用'} | "
            f"{'是' if row['recovered_by_budget_retry'] else '—'} | 错误 | "
            f"`{', '.join(row['reason_codes'])}` |"
        )
    lines += [
        "",
        "## 结论",
        "",
        "新版 Flash 的最小核心契约显著改善了输出速度和结构成功率，但没有恢复内容正确率。"
        "因此可以删除复杂的首次教学渲染门禁，却不能删除正确性门禁。推荐生产顺序为："
        "核心答案一次生成 → 标准答案/隐藏真值审核 → 仅对通过答案生成教学稿。"
        "当前模型不能切为无人值守生产解题默认。",
        "",
        "报告不包含题目、候选或标准答案正文，不包含 API key；只保存冻结摘要、哈希、"
        "耗时、通过状态和错误类型。",
        "",
    ]
    md = "\n".join(lines)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".md").write_text(md, encoding="utf-8")
    table_rows = "".join(
        "<tr>"
        f"<td>{row['question']}</td>"
        f"<td>{'成功' if row['first_attempt_status']=='completed' else '失败'}</td>"
        f"<td>{row['first_attempt_elapsed_seconds'] if row['first_attempt_elapsed_seconds'] is not None else '—'}</td>"
        f"<td>{'是' if row['recovered_by_budget_retry'] else '—'}</td>"
        "<td class='bad'>错误</td>"
        f"<td><code>{html.escape(', '.join(row['reason_codes']))}</code></td></tr>"
        for row in rows
    )
    html_text = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>新版 Flash 最小门禁盲测</title>
<style>body{{margin:0;background:#f4f6fb;color:#172033;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif}}main{{max-width:1050px;margin:auto;padding:40px 24px}}header{{padding:30px;border-radius:22px;background:linear-gradient(125deg,#17265f,#4969df);color:#fff}}h1{{margin:0}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}.metric,.card{{background:#fff;border:1px solid #e1e6f1;border-radius:16px;padding:18px}}.metric b{{display:block;font-size:26px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px;border-bottom:1px solid #e6e9f2;text-align:left}}.bad{{color:#b42318;font-weight:700}}@media(max-width:750px){{.grid{{grid-template-columns:repeat(2,1fr)}}.scroll{{overflow:auto}}}}</style></head>
<body><main><header><h1>CPhO 2021 · 新版 Flash 最小门禁盲测</h1><p>全年 8 题 · 直连 API · 冻结后标准答案自动审核</p></header>
<section class="grid"><div class="metric"><b>{first_success}/8</b><span>一次结构成功</span></div><div class="metric"><b>{recovered_success}/8</b><span>重试后有候选</span></div><div class="metric"><b>{first_success}/8</b><span>一次 ≤90s</span></div><div class="metric"><b>0/8</b><span>严格整题正确</span></div></section>
<section class="card"><p>最小核心契约恢复了速度，但没有恢复物理正确率。当前不能切为无人值守生产默认。</p></section>
<section class="card scroll"><table><thead><tr><th>题</th><th>首轮结构</th><th>耗时(s)</th><th>重试恢复</th><th>整题</th><th>原因码</th></tr></thead><tbody>{table_rows}</tbody></table></section>
</main></body></html>"""
    output.with_suffix(".html").write_text(html_text, encoding="utf-8")
    telemetry_path = output.with_suffix(".json")
    telemetry_path.write_text(
        json.dumps(telemetry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with zipfile.ZipFile(output.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(output.with_suffix(".md"), "report.md")
        archive.write(output.with_suffix(".html"), "report.html")
        archive.write(telemetry_path, "telemetry.json")
        archive.write(primary_path, "primary-freeze.json")
        archive.write(retry_path, "retry-freeze.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primary", required=True, type=Path)
    parser.add_argument("--retry", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.primary, args.retry, args.gold, args.output)
    print(json.dumps({"status": "built", "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
