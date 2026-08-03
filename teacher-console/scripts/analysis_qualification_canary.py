#!/usr/bin/env python3
"""Task-level qualification canary for analysis.generate (work-tree A1.2/A5.4).

Runs synthetic complex physics problems (six targets each, no student data)
through the production OpenAI-compatible adapter path against a real model,
using the production ``wuli.core-solve.v1`` contract (``core_analysis``:
Target Brief extraction, digest binding, ``normalize_payload`` gate), validates
structure/latency, aggregates a ``wuli.analysis-qualification.v1`` record, and
writes a redacted report.

Usage:
    python3 teacher-console/scripts/analysis_qualification_canary.py \
        --model deepseek-v4-flash-api [--problems 3] [--record]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SCRIPTS))

import core_analysis  # noqa: E402
import model_registry  # noqa: E402

ADAPTER = CONSOLE / "providers" / "openai_compatible_agent_adapter.py"
LIBRARY = ROOT / "student-error-library"

PROBLEMS = [
    {
        "id": "charged-particle-critical-motion",
        "title": "带电粒子在电场与有界磁场中的临界运动",
        "text": (
            "质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经电势差为 $U$ 的"
            "匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，"
            "磁场区域宽度为 $d$。忽略粒子重力。求：\n"
            "1. 粒子进入磁场时的速率；\n"
            "2. 粒子在磁场中做圆周运动的半径；\n"
            "3. 粒子首次到达磁场下边界时轨迹恰与下边界相切的临界磁感应强度 $B^*$；\n"
            "4. 该临界条件下粒子在磁场中的圆心角；\n"
            "5. 临界条件下粒子在磁场中的运动时间；\n"
            "6. 若粒子恰能穿出磁场右边界，所需磁感应强度的取值范围。"
        ),
    },
    {
        "id": "multi-body-energy-chain",
        "title": "连接体多过程能量问题",
        "text": (
            "光滑水平面上质量为 $m$ 的物块 A 以速度 $v_0$ 撞击静止的质量为 $2m$ 的物块 B，"
            "碰后 A 反向以 $v_0/4$ 运动，B 与劲度系数为 $k$ 的轻弹簧相连，弹簧另一端固定。求：\n"
            "1. 碰撞的恢复系数；\n"
            "2. 碰撞中损失的机械能；\n"
            "3. 碰后 B 的最大速度；\n"
            "4. 弹簧最大压缩量；\n"
            "5. B 第一次回到碰撞位置的速度；\n"
            "6. 系统最终机械能占总初动能的比例。"
        ),
    },
    {
        "id": "dual-rod-electromagnetic",
        "title": "双棒电磁感应多阶段问题",
        "text": (
            "两平行光滑金属导轨间距为 $L$，电阻不计，左端接电阻 $R$。质量均为 $m$、电阻均为 $r$"
            "的金属棒甲、乙置于导轨上，竖直向上的匀强磁场磁感应强度为 $B$。t=0 时给甲棒水平向右的"
            "初速度 $v_0$，乙棒静止。求：\n"
            "1. 初始瞬间甲棒产生的感应电动势；\n"
            "2. 初始瞬间回路电流；\n"
            "3. 初始瞬间甲棒的加速度；\n"
            "4. 两棒最终共同速度；\n"
            "5. 全过程通过电阻 $R$ 的电荷量；\n"
            "6. 全过程回路产生的焦耳热。"
        ),
    },
]


def _model_env(model_id: str) -> dict:
    model_registry.LIBRARY = LIBRARY
    config = model_registry.model_config_for_task("analysis.generate", model_id, "auto")
    if not isinstance(config, dict):
        raise SystemExit(f"model config unavailable: {model_id}")
    env = {
        "TEACHER_CONSOLE_AGENT_API_BASE_URL": str(config.get("base_url", "")).rstrip("/"),
        "TEACHER_CONSOLE_AGENT_API_MODEL": str(config.get("model", "")),
        "TEACHER_CONSOLE_AGENT_API_KEY": str(config.get("api_key", "")),
        "TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "70",
    }
    key = env["TEACHER_CONSOLE_AGENT_API_KEY"]
    if not key:
        env_name = str(config.get("api_key_env", ""))
        key = os.environ.get(env_name, "")
        env["TEACHER_CONSOLE_AGENT_API_KEY"] = key
    if not key:
        raise SystemExit("no API key available for the model")
    return env


def _build_task(problem: dict) -> tuple[dict, dict]:
    # Replicate the gateway's privacy gate (server.remote_agent_allowed):
    # a remote call is only permitted when the project authorizes it.
    model_registry.LIBRARY = LIBRARY
    if not model_registry.remote_agent_allowed():
        raise SystemExit("project privacy allow_remote_agent is not enabled; refusing remote canary")
    brief = core_analysis.build_target_brief(problem["text"], method_profile="high_school_standard")
    task = {
        "schema_version": 1,
        "id": f"canary-{problem['id']}",
        "kind": "analysis.generate",
        "entry_id": "canary-no-student-data",
        "entry_dir": str(ROOT),
        "working_dir": str(ROOT),
        "prompt": (
            "处理已复核的物理题。一次完成核心求解：覆盖 Target Brief 中的每个目标，"
            "写最终结论、决定性推导、适用条件和复算检查。不要生成学生版/教师版 Markdown，"
            "不要分解成阶段接口，不要模拟第二求解器或仲裁器。\n\n题目：\n" + problem["text"]
        ),
        "allow_remote": True,
        "allowed_paths": [],
        "input_paths": [],
        "output_contract": core_analysis.output_contract(brief),
        "timeout_seconds": 90,
        "deadline_budget": {
            "task_deadline": 90,
            "attempt_deadline": 86,
            "http_soft_deadline": 70,
            "cleanup_grace": 2,
        },
        "context_payloads": {
            ".agent-context/target-brief.json": brief,
        },
        "structured_context_paths": [".agent-context/target-brief.json"],
    }
    return task, brief


def _run_problem(problem: dict, env: dict) -> dict:
    task, brief = _build_task(problem)
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-B", str(ADAPTER)],
        input=json.dumps(task, ensure_ascii=False),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=110,
    )
    duration = round(time.monotonic() - started, 3)
    result = {
        "problem_id": problem["id"],
        "returncode": completed.returncode,
        "duration_seconds": duration,
        "brief": brief,
        "expected_ids": [item["id"] for item in brief["targets"]],
    }
    if completed.returncode == 0:
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            result.update({"structural_ok": False, "reason": "adapter stdout not JSON"})
            return result
        result["payload"] = payload
        targets = payload.get("targets") if isinstance(payload.get("targets"), list) else []
        result.update({
            "structural_ok": True,
            "status": payload.get("status"),
            "target_count": len(targets),
            "target_ids": [str(t.get("id")) for t in targets if isinstance(t, dict)],
            "digest_match": payload.get("target_brief_digest") == brief["digest"],
            "usage": payload.get("usage", {}),
        })
    else:
        envelope = None
        for line in completed.stderr.splitlines():
            if "WULI_AGENT_FAILURE_ENVELOPE:" in line:
                try:
                    envelope = json.loads(line.split("WULI_AGENT_FAILURE_ENVELOPE:", 1)[1])
                except json.JSONDecodeError:
                    envelope = None
                break
        result.update({
            "structural_ok": False,
            "failure_type": (envelope or {}).get("failure_type", "unknown"),
            "finish_reason": (envelope or {}).get("finish_reason", ""),
            "stderr_excerpt": completed.stderr[-300:],
        })
    return result


def _validate(problem: dict, run: dict) -> tuple[bool, list[str]]:
    if not run.get("structural_ok"):
        return False, [f"no structured JSON: {run.get('reason', run.get('failure_type', '?'))}"]
    payload = run.get("payload", {})
    try:
        # Run the full production gate, including the deterministic physics
        # quality gate (wuli.physics-quality-gate.v1), so qualification reflects
        # what promotion actually enforces.
        core_analysis.normalize_payload(payload, run["brief"], problem=problem["text"])
    except ValueError as error:
        return False, [str(error)]
    return True, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="deepseek-v4-flash-api")
    parser.add_argument("--problems", type=int, default=3)
    parser.add_argument("--record", action="store_true", help="write the qualification record into the registry")
    parser.add_argument("--report-dir", default=str(ROOT / "docs" / "reports"))
    args = parser.parse_args()

    env = _model_env(args.model)
    runs = []
    for problem in PROBLEMS[: max(1, min(args.problems, len(PROBLEMS)))]:
        print(f"[canary] {problem['id']} ...", flush=True)
        run = _run_problem(problem, env)
        ok, problems = _validate(problem, run)
        run["gate_ok"] = ok
        run["gate_problems"] = problems
        runs.append(run)
        print(
            f"  -> structural={run.get('structural_ok')} gate={ok} duration={run.get('duration_seconds')}s", flush=True
        )

    structural = sum(1 for r in runs if r.get("structural_ok"))
    gates = sum(1 for r in runs if r.get("gate_ok"))
    latencies = sorted(r["duration_seconds"] for r in runs)
    n = len(latencies)
    p50 = latencies[n // 2] if n else 0
    p95 = latencies[min(n - 1, int(n * 0.95))] if n else 0
    usage: dict[str, Any] = {}
    for r in runs:
        for key, value in (r.get("usage") or {}).items():
            if isinstance(value, int) and value >= 0:
                usage[key] = usage.get(key, 0) + value
    if n == args.problems and structural == n and gates == n:
        conclusion = "qualified"
    elif structural == 0:
        conclusion = "unqualified"
    else:
        conclusion = "provisional"

    qualification = {
        "provider": "openai-compatible",
        "sample_set_version": "canary-synthetic-v1",
        "sample_count": n,
        "structural_success_count": structural,
        "gate_success_count": gates,
        "p50_latency_ms": round(p50 * 1000),
        "p95_latency_ms": round(p95 * 1000),
        "usage": usage,
        "conclusion": conclusion,
        "notes": "synthetic complex physics problems; no student data; wuli.core-solve.v1 gate",
    }
    if args.record:
        model_registry.LIBRARY = LIBRARY
        model_registry.record_analysis_qualification(args.model, qualification)
        print(f"[canary] qualification recorded for {args.model}: {conclusion}", flush=True)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "analysis-qualification-canary-v1.md"
    summary = {
        "model_id": args.model,
        "qualification": qualification,
        "runs": [
            {
                "problem_id": r["problem_id"],
                "structural_ok": r.get("structural_ok"),
                "gate_ok": r.get("gate_ok"),
                "duration_seconds": r.get("duration_seconds"),
                "failure_type": r.get("failure_type", ""),
                "finish_reason": r.get("finish_reason", ""),
                "target_count": r.get("target_count"),
                "digest_match": r.get("digest_match"),
            }
            for r in runs
        ],
        "redactions": [
            "api-keys",
            "full-prompts",
            "reasoning-bodies",
            "student-data",
            "absolute-paths",
        ],
    }
    report_path.write_text(
        "# 分析资格 canary（A5.4，脱敏）\n\n"
        f"- 模型：`{args.model}`；结论：`{conclusion}`；样本：{n}（合成复杂物理题，无学生数据）\n"
        f"- 结构成功 {structural}/{n}；领域 Gate {gates}/{n}；p50 {round(p50 * 1000)}ms / p95 {round(p95 * 1000)}ms\n"
        f"- 契约：`wuli.core-solve.v1`（Target Brief digest + 顺序 + 方法策略）\n"
        f"- 逐题：\n"
        + "\n".join(
            f"  - {r['problem_id']}: structural={r.get('structural_ok')} gate={r.get('gate_ok')} "
            f"duration={r.get('duration_seconds')}s failure={r.get('failure_type', '-')}"
            for r in runs
        )
        + "\n",
        encoding="utf-8",
    )
    (report_dir / "analysis-qualification-canary-v1.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
