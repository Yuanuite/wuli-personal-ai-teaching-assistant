#!/usr/bin/env python3
"""Build the answer-safe CPhO 2021 W3 direct-API evaluation bundle."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import statistics
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path("/private/tmp/cpho-2021-web-pipeline-v1")
LIBRARY = ROOT / "web-workspace" / "student-error-library"
ENTRIES = LIBRARY / "entries"
JOBS = LIBRARY / ".cache" / "agent-jobs"
PACKAGE = ROOT / "report-package"

PROBLEM_URL = "https://cpho.pku.edu.cn/assets/file/2021cpho/3821fsst.pdf"
GOLD_URL = "https://cpho.pku.edu.cn/assets/file/2021cpho/3821fsstjd.pdf"
PAGE_URL = "https://cpho.pku.edu.cn/info/1053/1309.htm"

QUESTIONS = [
    (1, "20260730-question-01-p1-01627971"),
    (2, "20260730-question-02-p1-832dddbd"),
    (3, "20260730-question-03-p1-0df236a3"),
    (4, "20260730-question-04-complete-p1-f9a3416a"),
    (5, "20260730-question-05-p1-30f0cec9"),
    (6, "20260730-question-06-p1-4167f115"),
    (7, "20260730-question-07-p1-61942f3d"),
    (8, "20260730-question-08-complete-p1-3c56dc85"),
]

CANDIDATE_FILES = (
    "student-solution.md",
    "teacher-solution.md",
    "solution.md",
    "analysis-request.json",
    "w3-shadow-report.json",
)

# Codes only: neither candidate answers nor official answers are copied into the bundle.
GOLD_REVIEW = {
    1: {
        "final_answer_correct": True,
        "teaching_approved": False,
        "reason_codes": ["derivation-insufficient"],
    },
    2: {
        "final_answer_correct": False,
        "teaching_approved": False,
        "reason_codes": ["process-feasibility-wrong", "heat-result-mismatch", "derivation-insufficient"],
    },
    3: {
        "final_answer_correct": False,
        "teaching_approved": False,
        "reason_codes": ["collision-recurrence-wrong", "derived-results-wrong"],
    },
    4: {
        "final_answer_correct": False,
        "teaching_approved": False,
        "reason_codes": ["interaction-energy-wrong", "equilibrium-and-stability-wrong"],
    },
    5: {
        "final_answer_correct": False,
        "teaching_approved": False,
        "reason_codes": ["field-profile-wrong"],
    },
    6: {
        "final_answer_correct": False,
        "teaching_approved": False,
        "reason_codes": ["magnetic-field-wrong", "reach-condition-wrong", "induced-motion-wrong"],
    },
    7: {
        "final_answer_correct": False,
        "teaching_approved": False,
        "reason_codes": ["normal-force-model-wrong", "acceleration-model-wrong", "friction-threshold-wrong"],
    },
    8: {
        "final_answer_correct": False,
        "teaching_approved": False,
        "reason_codes": ["speed-time-law-wrong", "numerical-results-wrong"],
    },
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def entry_jobs(entry_id: str) -> list[dict]:
    result = []
    for path in JOBS.glob("*.json"):
        payload = load(path)
        if payload.get("result", {}).get("entry_id") == entry_id:
            payload["_path"] = str(path)
            result.append(payload)
    return sorted(result, key=lambda item: item.get("started_at", ""))


def completed_job(entry_id: str) -> tuple[str, dict]:
    matches = [
        payload
        for payload in entry_jobs(entry_id)
        if payload.get("status") == "completed"
        and payload.get("result", {}).get("status") == "completed"
        and payload.get("result", {}).get("adaptive_routing", {}).get("fallback") is None
    ]
    if not matches:
        raise RuntimeError(f"no completed W3 job for {entry_id}")
    payload = matches[-1]
    return Path(payload["_path"]).stem, payload


def freeze() -> dict:
    PACKAGE.mkdir(parents=True, exist_ok=True)
    rows = []
    for question, entry_id in QUESTIONS:
        job_id, _ = completed_job(entry_id)
        entry = ENTRIES / entry_id
        files = {}
        for name in CANDIDATE_FILES:
            path = entry / name
            if path.is_file():
                files[name] = {"sha256": digest(path), "bytes": path.stat().st_size}
        rows.append(
            {
                "question": question,
                "entry_id": entry_id,
                "job_id": job_id,
                "candidate_answer_present": (entry / "student-solution.md").is_file()
                and (entry / "teacher-solution.md").is_file(),
                "files": files,
            }
        )
    payload = {
        "schema_version": 1,
        "frozen_at": now(),
        "blindness": {
            "gold_path_outside_workspace": str(ROOT / "truth" / "cpho-2021-solutions.pdf"),
            "gold_not_used_in_uploads_prompts_context_or_jobs": True,
            "knowledge_store_started_empty": True,
        },
        "questions": rows,
    }
    (PACKAGE / "candidate-freeze.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def stage_sources(entry_id: str, frozen_job: dict) -> list[dict]:
    """Replace zero-cost checkpoints by the latest preceding real stage execution."""
    selected = []
    all_jobs = entry_jobs(entry_id)
    frozen_started = frozen_job.get("started_at", "")
    eligible = [job for job in all_jobs if job.get("started_at", "") <= frozen_started]
    for stage in frozen_job.get("result", {}).get("stages", []):
        stage_name = stage.get("stage")
        if not stage_name:
            continue
        source = stage
        if stage.get("provider") == "checkpoint":
            candidates = []
            for job in eligible:
                for prior in job.get("result", {}).get("stages", []):
                    if (
                        prior.get("stage") == stage_name
                        and prior.get("status") == "completed"
                        and prior.get("provider") != "checkpoint"
                    ):
                        candidates.append(prior)
            if candidates:
                source = candidates[-1]
        selected.append(source)
    return selected


def collect() -> dict:
    frozen = load(PACKAGE / "candidate-freeze.json")
    rows = []
    for question, entry_id in QUESTIONS:
        frozen_item = next(item for item in frozen["questions"] if item["question"] == question)
        job = load(JOBS / f"{frozen_item['job_id']}.json")
        sources = stage_sources(entry_id, job)
        started = parse_time(job["started_at"])
        completed = parse_time(job["completed_at"])
        usage = [stage.get("usage", {}) for stage in sources]
        review = GOLD_REVIEW[question]
        rows.append(
            {
                "question": question,
                "entry_id": entry_id,
                "job_id": frozen_item["job_id"],
                "status": job["status"],
                "warm_run_seconds": round((completed - started).total_seconds(), 3),
                "cold_equivalent_seconds": round(
                    sum(float(stage.get("duration_seconds", 0) or 0) for stage in sources), 3
                ),
                "provider_seconds": round(
                    sum(float(stage.get("provider_seconds", 0) or 0) for stage in sources), 3
                ),
                "input_tokens": sum(int(item.get("prompt_tokens", 0) or 0) for item in usage),
                "output_tokens": sum(int(item.get("completion_tokens", 0) or 0) for item in usage),
                "candidate_answer_present": frozen_item["candidate_answer_present"],
                **review,
            }
        )
    cold_times = [row["cold_equivalent_seconds"] for row in rows]
    summary = {
        "question_count": len(rows),
        "structural_success_count": sum(row["candidate_answer_present"] for row in rows),
        "structural_success_rate": sum(row["candidate_answer_present"] for row in rows) / len(rows),
        "sla_pass_count": sum(row["cold_equivalent_seconds"] <= 90 for row in rows),
        "sla_pass_rate": sum(row["cold_equivalent_seconds"] <= 90 for row in rows) / len(rows),
        "final_answer_correct_count": sum(row["final_answer_correct"] for row in rows),
        "final_answer_accuracy": sum(row["final_answer_correct"] for row in rows) / len(rows),
        "teaching_approved_count": sum(row["teaching_approved"] for row in rows),
        "teaching_approval_rate": sum(row["teaching_approved"] for row in rows) / len(rows),
        "median_cold_seconds": round(statistics.median(cold_times), 3),
        "mean_cold_seconds": round(statistics.mean(cold_times), 3),
        "max_cold_seconds": round(max(cold_times), 3),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
    }
    return {"generated_at": now(), "summary": summary, "questions": rows}


def markdown(data: dict, gold_hash: str) -> str:
    s = data["summary"]
    lines = [
        "# CPhO 2021 全年 W3 直接 API 盲测报告",
        "",
        f"- 生成时间：{data['generated_at']}",
        f"- 覆盖：第 38 届全国中学生物理竞赛复赛理论题，共 {s['question_count']} 题",
        "- 求解模型：`deepseek-v4-flash`；传输：OpenAI-compatible HTTP API（不经过 Claude Code）",
        "- 方法档：`olympiad_official`；标准答案在候选冻结后代替教师审核，全程无用户参与",
        "",
        "## 三层结论",
        "",
        f"- 结构成功率：**{s['structural_success_rate']:.0%}（{s['structural_success_count']}/8）**。",
        f"- 冷启动等价 90 秒 SLA：**{s['sla_pass_rate']:.0%}（{s['sla_pass_count']}/8）**；"
        f"中位 {s['median_cold_seconds']:.1f}s，最慢 {s['max_cold_seconds']:.1f}s。",
        f"- 严格整题正确率：**{s['final_answer_accuracy']:.1%}（{s['final_answer_correct_count']}/8）**。",
        f"- 教学成品批准率：**{s['teaching_approval_rate']:.0%}（{s['teaching_approved_count']}/8）**。",
        "",
        "两段紧凑契约已解决大型结构化输出失败，也取消了 W3 失败后的重复 W2 求解。"
        "直接 API 可以摆脱 Claude Code 的结构化调用限制，但本轮不能进入生产默认："
        "Flash 的物理推导错误未被 verifier/adjudicator 拦住，且学生版渲染过度压缩推导。",
        "",
        "## 每题判定",
        "",
        "| 题号 | 冷启动等价(s) | ≤90s | 结构候选 | 整题答案 | 教学批准 | 原因码 |",
        "|---:|---:|:---:|:---:|:---:|:---:|---|",
    ]
    for row in data["questions"]:
        lines.append(
            f"| {row['question']} | {row['cold_equivalent_seconds']:.1f} | "
            f"{'是' if row['cold_equivalent_seconds'] <= 90 else '否'} | "
            f"{'成功' if row['candidate_answer_present'] else '失败'} | "
            f"{'正确' if row['final_answer_correct'] else '错误'} | "
            f"{'批准' if row['teaching_approved'] else '拒绝'} | "
            f"`{', '.join(row['reason_codes'])}` |"
        )
    lines += [
        "",
        "## 审核规则",
        "",
        "- 整题正确要求所有问的结论与标准答案一致；部分小问正确不算整题通过。",
        "- 教学批准还要求关键推导足以复核。因此第 1 题虽结论等价，仍因推导不足被拒绝。",
        "- 审核只记录通过布尔值和原因码，不保存或打包候选/标准答案正文。",
        "- 审核结果未作为反馈重新调用模型，避免答案泄漏与测试集污染。",
        "",
        "## 瓶颈与优化顺序",
        "",
        "1. 已消除的瓶颈：Claude Code 大型 structured-output 生成、约 32K 冗长输出、失败后重复 W2。",
        "2. 当前首要瓶颈：Flash 求解可靠性；错误集中在过程可行性、碰撞递推、稳定性、场分布、约束动力学等核心关系。",
        "3. 当前次要瓶颈：verifier/adjudicator 只验证内部一致性，无法可靠发现共同的物理模型错误。",
        "4. 下一轮应先强化可判定的物理不变量与逐目标复算，再恢复完整但紧凑的教学推导；通过隐藏集后才重测 100% 正确率目标。",
        "",
        "## 忠实性与防泄漏",
        "",
        f"- 题目 PDF SHA-256：`{digest(ROOT / 'source' / 'cpho-2021-problems.pdf')}`",
        f"- 官方答案 PDF SHA-256：`{gold_hash}`",
        "- 官方答案保存在隔离 truth 目录，未进入上传、prompt、Knowledge Store、Gateway、checkpoint 或 ZIP。",
        "- 候选冻结清单及 SHA-256 先于官方答案审核生成。",
        "- ZIP 不含题目/答案正文、模型答案正文、API key、模型注册表或私有工作区。",
        "",
        "## 来源",
        "",
        f"- 年份页面：{PAGE_URL}",
        f"- 试题：{PROBLEM_URL}",
        f"- 官方答案（仅冻结后审核，不打包）：{GOLD_URL}",
        "",
    ]
    return "\n".join(lines)


def html_report(data: dict) -> str:
    s = data["summary"]
    rows = "".join(
        "<tr>"
        f"<td>{row['question']}</td><td>{row['cold_equivalent_seconds']:.1f}</td>"
        f"<td>{'✓' if row['cold_equivalent_seconds'] <= 90 else '✗'}</td>"
        f"<td class='ok'>✓</td><td class=\"{'ok' if row['final_answer_correct'] else 'bad'}\">"
        f"{'✓' if row['final_answer_correct'] else '✗'}</td><td class='bad'>✗</td>"
        f"<td><code>{html.escape(', '.join(row['reason_codes']))}</code></td></tr>"
        for row in data["questions"]
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CPhO 2021 W3 直接 API 盲测</title>
<style>
:root{{--ink:#172033;--muted:#65708a;--paper:#f4f6fb;--card:#fff;--blue:#3659d9;--bad:#b42318;--ok:#087a55}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"PingFang SC",sans-serif}}
main{{max-width:1100px;margin:auto;padding:42px 24px 70px}}header{{background:linear-gradient(125deg,#17265f,#4969df);color:white;border-radius:24px;padding:34px}}
h1{{margin:0 0 8px;font-size:32px}}header p{{margin:0;opacity:.86}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0}}
.metric,.card{{background:var(--card);border:1px solid #e1e6f1;border-radius:18px;padding:20px}}.metric b{{display:block;font-size:27px}}.metric span{{color:var(--muted)}}
.card{{margin:16px 0}}h2{{margin:0 0 12px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px 8px;border-bottom:1px solid #e6e9f2;text-align:left}}
.bad{{color:var(--bad);font-weight:700}}.ok{{color:var(--ok);font-weight:700}}code{{font-size:12px}}.callout{{border-left:5px solid var(--bad);background:#fff3f1;padding:14px 16px;border-radius:10px}}
@media(max-width:800px){{.grid{{grid-template-columns:repeat(2,1fr)}}.scroll{{overflow:auto}}}}
</style></head><body><main>
<header><h1>CPhO 2021 · W3 直接 API 盲测</h1><p>全年 8 题 · Flash · 候选冻结后由标准答案自动审核 · 无用户参与</p></header>
<section class="grid">
<div class="metric"><b>{s['structural_success_count']} / 8</b><span>结构成功</span></div>
<div class="metric"><b>{s['sla_pass_count']} / 8</b><span>冷启动 ≤90s</span></div>
<div class="metric"><b>{s['final_answer_correct_count']} / 8</b><span>整题正确</span></div>
<div class="metric"><b>{s['teaching_approved_count']} / 8</b><span>教学批准</span></div>
</section>
<section class="card"><h2>判定</h2><div class="callout">结构问题已经解决，但内容质量未恢复。直接 API 消除了 Claude Code 的协议瓶颈；当前阻断是 Flash 的物理推导与验证门禁。</div></section>
<section class="card"><h2>逐题结果</h2><div class="scroll"><table><thead><tr><th>题</th><th>冷启动等价(s)</th><th>SLA</th><th>结构</th><th>整题</th><th>教学</th><th>原因码</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section class="card"><h2>说明</h2><p>完整方法、遥测口径、防泄漏声明与来源见同包 <code>report.md</code>。报告不含候选或官方答案正文。</p></section>
<footer>生成于 {html.escape(data['generated_at'])}</footer></main></body></html>"""


def build(gold: Path, destination: Path) -> Path:
    frozen = load(PACKAGE / "candidate-freeze.json")
    if not all(item["candidate_answer_present"] for item in frozen["questions"]):
        raise RuntimeError("all eight frozen candidates are required")
    gold_hash = digest(gold)
    data = collect()
    review = {
        "schema_version": 1,
        "reviewed_at": now(),
        "reviewer": "official-standard-answer-surrogate",
        "gold_sha256": gold_hash,
        "frozen_candidate_count": 8,
        "answer_text_stored": False,
        "feedback_returned_to_model": False,
        "approvals": [
            {"question": question, **GOLD_REVIEW[question]} for question, _ in QUESTIONS
        ],
    }
    PACKAGE.mkdir(parents=True, exist_ok=True)
    (PACKAGE / "gold-review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PACKAGE / "telemetry.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (PACKAGE / "report.md").write_text(markdown(data, gold_hash), encoding="utf-8")
    (PACKAGE / "report.html").write_text(html_report(data), encoding="utf-8")
    (PACKAGE / "sources.json").write_text(
        json.dumps(
            {
                "page_url": PAGE_URL,
                "problem_url": PROBLEM_URL,
                "official_solution_url": GOLD_URL,
                "problem_sha256": digest(ROOT / "source" / "cpho-2021-problems.pdf"),
                "official_solution_sha256": gold_hash,
                "official_solution_in_archive": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (PACKAGE / "README.txt").write_text(
        "打开 report.html 查看结果；report.md 为文字报告。"
        "本包不含官方答案、模型答案正文、API key 或私有工作区。\n",
        encoding="utf-8",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PACKAGE / "report.md", destination.with_suffix(".md"))
    shutil.copy2(PACKAGE / "report.html", destination.with_suffix(".html"))
    archive_names = (
        "README.txt",
        "candidate-freeze.json",
        "gold-review.json",
        "report.html",
        "report.md",
        "sources.json",
        "telemetry.json",
    )
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in archive_names:
            path = PACKAGE / name
            archive.write(path, path.relative_to(PACKAGE.parent))
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.freeze:
        payload = freeze()
        print(json.dumps({"status": "frozen", "questions": len(payload["questions"])}, ensure_ascii=False))
        return 0
    if not args.gold or not args.output:
        parser.error("--gold and --output are required unless --freeze is used")
    path = build(args.gold, args.output)
    print(json.dumps({"status": "built", "zip": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
