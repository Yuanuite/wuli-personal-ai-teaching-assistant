#!/usr/bin/env node
// Wave 4 A4.5 scenario 1 (analysis-core-hard-timeout-route-visible): the
// core-first route plan is visible before the run, and a Gateway hard kill of
// a never-returning child records timeout_layer=attempt_hard with the
// timeout_summary contract while the canonical entry stays byte-identical.
//
// run_e2e.py registers e2e-hang-solver (a probed claude identity routed
// through the deterministic fake adapter). The problem carries the [e2e-hang]
// marker, which makes the fake adapter's core-solve child sleep past the
// attempt deadline, so the Gateway — not the adapter — acts first (work-tree
// A4.3). The slow openai-compatible mock cannot produce this layer: the
// adapter always soft-times-out first at its own HTTP deadline
// (http_soft < attempt), and that path records no timeout_summary in the
// current server. This scenario therefore asserts the deterministic
// attempt_hard branch.
//
// Flow: upload → approve-source (page) → route plan "Core（W3 未运行）" →
// custom tier = e2e-hang-solver → run-analysis (page) → job failed →
// timeout-summary block with the Gateway-hard-deadline copy → public job +
// private job contract (timeout_summary.timeout_layer=attempt_hard, single
// attempt, budget guard, empty child stdout, no fabricated usage) → canonical
// untouched.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { createSession, uploadAndApproveSource, writeJson } from "./support.mjs";
import {
  artifactDir,
  entryDirFor,
  getJson,
  jobFileFor,
  library,
  postJson,
  readJson,
  recordFailure,
} from "./analysis-common.mjs";

// Fixed, sufficiently long reviewed problem (multi-target charged-particle
// critical motion). The [e2e-hang] marker is inert to every pipeline gate and
// only switches the fake adapter's core-solve child into never-return mode.
const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。

[e2e-hang]`;

const HANG_MODEL = "e2e-hang-solver";
const CANONICAL_ANSWER_FILES = ["answer.md", "student-solution.md", "teacher-solution.md"];
const PRE_EXISTING_CANONICAL_FILES = ["problem.md", "record.json", "solution.md"];

const summary = { scenario: "analysis-core-hard-timeout-route-visible", status: "failed" };
let entryId = "";
let jobId = "";
let canonicalBefore = {};
let planText = "";
let timeoutText = "";

function digest(file) {
  return fs.existsSync(file)
    ? createHash("sha256").update(fs.readFileSync(file)).digest("hex")
    : null;
}

try {
  // --- route config: explicit core-first with a short attempt deadline ---
  // max_latency_seconds=15 → attempt_deadline=15s, http_soft=12s; the hanging
  // child is killed at 15s instead of the default 90s.
  const configDir = path.join(library, "config");
  fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(path.join(configDir, "analysis-production-routing.json"), JSON.stringify({
    schema_version: 1,
    policy_version: "wuli-core-first-routing-v1",
    mode: "core-first",
    max_latency_seconds: 15,
  }, null, 2) + "\n");
  fs.writeFileSync(path.join(configDir, "w3r-production-routing.json"), JSON.stringify({
    schema_version: 1,
    policy_version: "wuli-w3r-routing-v1",
    mode: "off",
    gray_entry_ids: [],
    evidence: {
      report_digest: "",
      paired_case_count: 0,
      teacher_reviewed_case_count: 0,
      fresh_holdout_case_count: 0,
      fresh_holdout_target_count: 0,
      final_answer_fidelity: 0.0,
      claim_support_coverage: 0.0,
      condition_retention: 0.0,
      target_coverage: 0.0,
      latex_validity: 0.0,
      unsupported_claim_rate: 1.0,
      teacher_readability_preference: 0.0,
      teacher_edit_rate_non_regression: false,
    },
  }, null, 2) + "\n");

  // --- (c) page: upload → approve → the answer tab renders the route plan ---
  const { browser, page, browserErrors } = await createSession();
  try {
    entryId = await uploadAndApproveSource(page, {
      filename: "core-hard-timeout-e2e.png",
      problem: PROBLEM,
      note: "E2E 已核对题干、公式与边界条件",
    });
    const planTarget = page.locator("#route-execution-plan");
    // The plan copy is rendered into this element's text; the element itself
    // keeps the initial hidden attribute (app.js only toggles the CSS class),
    // so we assert the rendered DOM text, not browser visibility.
    await planTarget.waitFor({ state: "attached", timeout: 20_000 });
    await page.waitForFunction(() =>
      document.querySelector("#route-execution-plan")?.textContent?.includes("W3 未运行"),
    );
    planText = String(await planTarget.textContent());
    assert.ok(planText.includes("Core"), `route plan must name Core: ${planText}`);
    assert.ok(planText.includes("W3 未运行"), `route plan must say W3 未运行: ${planText}`);
    assert.ok(planText.includes("渲染器 legacy"), `core plan must keep renderer legacy: ${planText}`);

    // --- (a) route-preview API contract ---
    const preview = await postJson(`/api/entries/${encodeURIComponent(entryId)}/route-preview`, {
      routing_tier: "auto",
    });
    assert.equal(preview.schema, "wuli.route-preview.v1");
    const plan = preview.route_execution_plan || {};
    assert.equal(plan.schema, "wuli.route-execution-plan.v1");
    assert.equal(plan.planned_solver_route, "core");
    assert.equal(plan.w3r_mode, "off");
    assert.equal(plan.planned_renderer_mode, "legacy");
    assert.ok(plan.expected_stages.includes("structured-generation"));
    assert.ok(!plan.expected_stages.includes("solver-a"), "core plan must not expect W3 stages");

    // --- page analyze through the hanging model (custom tier) ---
    canonicalBefore = Object.fromEntries(
      PRE_EXISTING_CANONICAL_FILES.map(name => [name, digest(path.join(entryDirFor(entryId), name))]),
    );
    await page.locator('#agent-model option[value="e2e-hang-solver"]').waitFor({ state: "attached" });
    await page.locator("#agent-tier").selectOption("custom");
    await page.locator("#agent-model").selectOption(HANG_MODEL);
    await page.locator("#run-analysis").click();

    // The Gateway hard-kill fires at the 15s attempt deadline; the page's job
    // polling then renders the timeout-summary block.
    await page.locator("#timeout-summary").waitFor({ state: "visible", timeout: 90_000 });
    timeoutText = String(await page.locator("#timeout-summary").textContent());
    assert.ok(
      timeoutText.includes("Gateway hard deadline"),
      `timeout summary must name the hard-deadline layer: ${timeoutText}`,
    );
    assert.ok(timeoutText.includes("W3/W3R 未运行"), `timeout summary must keep the core route honest: ${timeoutText}`);
    assert.ok(
      timeoutText.includes("canonical 未修改、未自动重试"),
      `timeout summary must keep the safe-stop copy: ${timeoutText}`,
    );
  } finally {
    await browser.close();
  }

  // --- public job contract ---
  const latest = await getJson(`/api/jobs?entry_id=${encodeURIComponent(entryId)}`);
  jobId = latest.job && latest.job.id;
  assert.ok(jobId, "a failed analyze job must be recorded for the entry");
  const job = await getJson(`/api/jobs/${encodeURIComponent(jobId)}`);
  assert.equal(job.status, "failed");
  assert.equal(job.failure_type, "provider_timeout", "hard kill must classify as provider_timeout");
  assert.equal(job.model_id, HANG_MODEL, "the job must have resolved the hanging model");
  assert.equal(job.kind, "analysis.generate");
  const publicResult = job.result || {};
  assert.equal(publicResult.status, "failed");
  assert.equal(publicResult.failure_type, "provider_timeout");
  assert.ok(publicResult.timeout_summary, "public job API must carry timeout_summary");
  assert.equal(publicResult.timeout_summary.schema, "wuli.timeout-summary.v1");
  assert.equal(publicResult.timeout_summary.timeout_layer, "attempt_hard");
  assert.equal(publicResult.timeout_summary.child_stdout_empty, true);
  assert.equal(publicResult.timeout_summary.usage_measurement, "unavailable");
  assert.ok(publicResult.budget_guard && publicResult.budget_guard.status === "stopped");

  // --- private job record: single hard-killed attempt, binding, no usage ---
  const record = readJson(jobFileFor(jobId));
  const result = record.result || {};
  assert.equal(result.status, "failed");
  assert.equal(result.failure_type, "provider_timeout");
  const budget = result.deadline_budget || {};
  assert.equal(budget.schema, "wuli.deadline-budget.v1");
  assert.ok(
    budget.http_soft_deadline + budget.cleanup_grace <= budget.attempt_deadline &&
      budget.attempt_deadline <= budget.task_deadline,
    `deadline layers must stay ordered: ${JSON.stringify(budget)}`,
  );
  assert.ok(Array.isArray(result.attempts) && result.attempts.length === 1, "hard kill must not retry");
  const attempt = result.attempts[0];
  assert.equal(attempt.status, "failed");
  assert.equal(attempt.failure_type, "provider_timeout");
  assert.equal(attempt.timeout_layer, "attempt_hard");
  assert.equal(attempt.child_stdout_empty, true);
  assert.ok(typeof attempt.timeout_seconds === "number", "the Gateway must record its kill deadline");
  assert.ok(
    attempt.timeout_seconds >= budget.http_soft_deadline &&
      attempt.timeout_seconds <= budget.attempt_deadline,
    `the kill must happen inside the attempt window (soft=${budget.http_soft_deadline} attempt=${budget.attempt_deadline}, got ${attempt.timeout_seconds})`,
  );
  const binding = attempt.deadline_binding || {};
  assert.equal(binding.schema, "wuli.provider-deadline-binding.v1");
  assert.equal(binding.timeout_layer, "attempt_hard");
  assert.ok(binding.http_soft_deadline < binding.attempt_deadline);
  assert.ok(!("token_usage" in attempt), "a hard kill must never fabricate token usage");
  assert.ok(!("adapter_failure_type" in attempt), "no adapter envelope exists for a hard kill");

  // --- (d) canonical entry unchanged ---
  const state = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
  assert.equal(state.state, "needs-analysis-and-answer");
  for (const name of CANONICAL_ANSWER_FILES) {
    assert.ok(
      !fs.existsSync(path.join(entryDirFor(entryId), name)),
      `canonical ${name} must not be written by a hard timeout`,
    );
  }
  for (const [name, before] of Object.entries(canonicalBefore)) {
    assert.equal(
      digest(path.join(entryDirFor(entryId), name)),
      before,
      `pre-existing ${name} must stay byte-identical`,
    );
  }

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.job_id = jobId;
  summary.route_plan_text = planText;
  summary.timeout_summary_text = timeoutText;
  summary.timeout_layer = publicResult.timeout_summary.timeout_layer;
  summary.attempt_duration_seconds = attempt.duration_seconds;
  summary.budget = budget;
  writeJson("analysis-core-hard-timeout-route-visible-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
