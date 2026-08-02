#!/usr/bin/env node
// Wave 5 scenario 2 (analysis-soft-timeout, work-tree A2.1/A2.4): a provider
// that hangs past the adapter's HTTP soft deadline must produce a single
// provider_timeout attempt, a frozen ordered three-layer deadline budget, no
// second paid retry, and zero canonical writes.
//
// run_e2e.py registers e2e-mock-slow (probed + task-level qualified,
// openai-compatible) against a local mock endpoint that sleeps longer than
// the adapter's HTTP timeout and points defaults.analysis.generate at it; the
// adapter — not the Gateway subprocess kill — is the first to time out.
//
// Flow: upload → approve-source → analyze (routing_tier=auto) → job failed →
// assert provider_timeout, ordered deadline_budget, single attempt, budget
// guard, canonical untouched.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import {
  artifactDir,
  entryDirFor,
  getJson,
  jobFileFor,
  postJson,
  readJson,
  recordFailure,
  uploadAndApproveSource,
  waitForJob,
  writeJson,
} from "./analysis-common.mjs";

// Fixed, sufficiently long reviewed problem (multi-target charged-particle
// critical motion).
const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。`;

const SLOW_MODEL = "e2e-mock-slow";
// Files that only a completed analysis may create; solution.md is a
// pre-existing ingest placeholder and must not be treated as analysis output.
const CANONICAL_ANSWER_FILES = ["answer.md", "student-solution.md", "teacher-solution.md"];

const summary = { scenario: "analysis-soft-timeout", status: "failed" };
let entryId = "";
let jobId = "";
try {
  entryId = await uploadAndApproveSource(PROBLEM);
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "auto",
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze should return a queued job id");
  const job = await waitForJob(jobId, 120_000);

  // --- public job contract ---
  assert.equal(job.status, "failed");
  assert.equal(job.failure_type, "provider_timeout", "job must classify the HTTP timeout, not a generic failure");
  assert.equal(job.model_id, SLOW_MODEL, "the job must have resolved the mock model default");
  assert.equal(job.provider, "openai-compatible");
  assert.equal(job.route_snapshot && job.route_snapshot.resolved_model_id, SLOW_MODEL);
  assert.equal(job.route_snapshot && job.route_snapshot.provider, "openai-compatible");
  const publicResult = job.result || {};
  assert.equal(publicResult.status, "failed");
  assert.equal(publicResult.failure_type, "provider_timeout");
  assert.ok(publicResult.budget_guard, "job must carry the budget guard");
  assert.equal(publicResult.budget_guard.status, "stopped");

  // --- private job record: ordered deadline budget + single attempt ---
  const record = readJson(jobFileFor(jobId));
  const result = record.result || {};
  assert.equal(result.status, "failed");
  assert.equal(result.failure_type, "provider_timeout");
  const budget = result.deadline_budget || null;
  if (budget) {
    assert.equal(budget.schema, "wuli.deadline-budget.v1");
    assert.ok(
      budget.http_soft_deadline + budget.cleanup_grace <= budget.attempt_deadline &&
        budget.attempt_deadline <= budget.task_deadline,
      `deadline layers must satisfy http_soft+grace <= attempt <= task: ${JSON.stringify(budget)}`,
    );
    assert.deepEqual(result.deadline_budget_problems || [], [], "the frozen budget must be valid");
  }
  assert.ok(
    Array.isArray(result.attempts) && result.attempts.length === 1,
    "the adapter-first timeout must not trigger a second paid attempt",
  );
  const attempt = result.attempts[0];
  assert.equal(attempt.provider, "openai-compatible");
  assert.equal(attempt.status, "failed");
  assert.equal(attempt.failure_type, "provider_timeout");
  assert.equal(attempt.returncode, 1, "the adapter subprocess must have exited by itself");
  assert.equal(attempt.timeout_seconds, undefined, "the Gateway must not have killed the subprocess (adapter timed out first)");
  assert.ok(
    attempt.duration_seconds >= 6 && attempt.duration_seconds < 14,
    `the adapter must have timed out at its HTTP soft deadline, before the Gateway attempt deadline ` +
      `(duration ${attempt.duration_seconds}s in [6, 14)s)`,
  );
  const stderrText = String(attempt.stderr || "");
  assert.ok(
    /timeout|timed out/i.test(stderrText),
    "the adapter failure must be the HTTP timeout (TimeoutError/timed out) in stderr",
  );

  // No secret material may leak into the attempt record.
  const attemptText = JSON.stringify(attempt);
  for (const marker of ["sk-", "api_key", "Bearer ", "Authorization"]) {
    assert.ok(!attemptText.includes(marker), `attempt must not leak ${marker}`);
  }
  // A2.4 expects the slow response to become a structured, redacted timeout
  // envelope before the Gateway finalizes the attempt. The adapter only wraps
  // URLError-shaped timeouts; a hang during the response read escapes as a raw
  // socket.timeout traceback (observed: stderr ends in "TimeoutError: timed
  // out", no WULI_AGENT_FAILURE_ENVELOPE marker, no adapter_failure_type).
  const structuredEnvelope = Boolean(attempt.adapter_failure_type);

  // --- V4: a provider failure must not touch canonical answer files ---
  const state = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
  assert.equal(
    state.state,
    "needs-analysis-and-answer",
    "the failed analysis must not move the pipeline forward",
  );
  for (const name of CANONICAL_ANSWER_FILES) {
    assert.ok(
      !fs.existsSync(path.join(entryDirFor(entryId), name)),
      `canonical ${name} must not be written by a provider timeout`,
    );
  }

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.job_id = jobId;
  summary.budget_guard = publicResult.budget_guard;
  summary.deadline_budget = budget;
  summary.structured_envelope_emitted = structuredEnvelope;
  summary.attempt_duration_seconds = attempt.duration_seconds;
  summary.attempt_returncode = attempt.returncode;
  summary.attempt_stderr_excerpt = stderrText.slice(0, 300);
  const limitations = [];
  if (!budget) {
    // A2.4 requires the frozen three-layer deadline to be recorded on the job
    // ("job 记录冻结后的三层期限和策略 digest"). The current server drops it
    // between the Gateway result and the persisted job record
    // (server.gateway_routing_fields whitelists budget_guard but not
    // deadline_budget / deadline_budget_problems). Every other assertion above
    // passed; this contract piece is not yet wired end to end, so the scenario
    // must not report a clean pass.
    limitations.push(
      "job 记录未持久化 deadline_budget（A2.4 契约未端到端落地）：" +
        "Gateway result 内含冻结期限，但 server.gateway_routing_fields 白名单缺少 " +
        "deadline_budget/deadline_budget_problems，job 文件与 analysis-request.json 均无该字段。" +
        "已通过可观测代理断言期限传播：adapter 在自身 HTTP soft deadline 先超时（duration=" +
        `${attempt.duration_seconds}s < 14s，未到 Gateway attempt 硬截止）且无第二次尝试。`,
    );
  }
  if (!structuredEnvelope) {
    // A2.4/A3.x: "慢响应先形成脱敏结构化 timeout envelope"。adapter 只捕获
    // URLError 形态的超时；响应读取阶段挂起（socket.timeout）会以原始
    // traceback 逃逸（stderr 尾部 TimeoutError: timed out），未产出
    // WULI_AGENT_FAILURE_ENVELOPE 脱敏信封。分类（provider_timeout）与
    // 预算保护仍正确，但信封契约对挂起型超时不成立。
    limitations.push(
      "adapter 未为挂起型超时输出脱敏结构化 timeout envelope：" +
        "响应读取阶段超时抛出原始 socket.timeout（TimeoutError），未命中 adapter " +
        "的 URLError 分支，stderr 无 WULI_AGENT_FAILURE_ENVELOPE 标记，" +
        "attempt.adapter_failure_type 为空；终态分类仍为 provider_timeout。",
    );
  }
  if (limitations.length > 0) {
    summary.status = "limitation";
    summary.limitations = limitations;
  }
  writeJson("analysis-soft-timeout-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
  if (limitations.length > 0) {
    console.error(`analysis-soft-timeout: ${limitations.join(" | ")}`);
    process.exit(1);
  }
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
