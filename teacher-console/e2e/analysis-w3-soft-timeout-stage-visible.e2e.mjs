#!/usr/bin/env node
// Wave 4 A4.5 scenario 2 (analysis-w3-soft-timeout-stage-visible): a legacy-
// adaptive W3 route whose decompose stage soft-times-out on the hanging
// openai-compatible mock must surface the real stage name in the W3 shadow
// report and the job, while the route preview/page plan the W3 route and the
// canonical entry stays untouched.
//
// run_e2e.py registers e2e-mock-slow (probed openai-compatible, timeout_seconds
// = 8) against the slow mock WITHOUT re-pointing the default; the scenario
// passes model_id=e2e-mock-slow on the adaptive analyze call. The W3 solver
// stages resolve that model, so the decompose stage's adapter call times out
// at its HTTP soft deadline (~8s) and the stage fails with its real stage
// name. w3_failure_policy=stop prevents a W2 fallback.
//
// Flow: upload → approve-source (page) → route plan "W3 · W3R off" →
// route-preview API (planned_solver_route=w3) → analyze(model_id=e2e-mock-slow)
// → job failed → w3-shadow-report.json records the failed decompose stage →
// canonical untouched → no W3R.

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
  waitForJob,
} from "./analysis-common.mjs";

// Fixed, sufficiently long reviewed problem (multi-target charged-particle
// critical motion). The complexity screen routes this to W3 ("首次"/"临界"
// completeness language).
const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。`;

const SLOW_MODEL = "e2e-mock-slow";
const CANONICAL_ANSWER_FILES = ["answer.md", "student-solution.md", "teacher-solution.md"];
const PRE_EXISTING_CANONICAL_FILES = ["problem.md", "record.json", "solution.md"];

const summary = { scenario: "analysis-w3-soft-timeout-stage-visible", status: "failed" };
let entryId = "";
let jobId = "";

function digest(file) {
  return fs.existsSync(file)
    ? createHash("sha256").update(fs.readFileSync(file)).digest("hex")
    : null;
}

function writeConfig(name, value) {
  const target = path.join(library, "config", name);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`);
}

try {
  // --- temp routing configs: legacy-adaptive W3 + W3R off (preview-time) ---
  writeConfig("analysis-production-routing.json", {
    schema_version: 1,
    policy_version: "wuli-core-first-routing-v1",
    mode: "legacy-adaptive",
    max_latency_seconds: 90,
  });
  writeConfig("w3r-production-routing.json", {
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
  });

  // --- page: upload → approve → route plan must read as the W3 route ---
  const { browser, page, browserErrors } = await createSession();
  let planText = "";
  try {
    entryId = await uploadAndApproveSource(page, {
      filename: "w3-soft-timeout-stage-e2e.png",
      problem: PROBLEM,
      note: "E2E 已核对题干、公式与边界条件",
    });
    const planTarget = page.locator("#route-execution-plan");
    // The plan copy is rendered into this element's text; the element itself
    // keeps the initial hidden attribute (app.js only toggles the CSS class),
    // so we assert the rendered DOM text, not browser visibility.
    await planTarget.waitFor({ state: "attached", timeout: 20_000 });
    await page.waitForFunction(() =>
      document.querySelector("#route-execution-plan")?.textContent?.includes("W3"),
    );
    planText = String(await planTarget.textContent());
    assert.ok(planText.includes("计划解析路线：W3"), `route plan must plan W3: ${planText}`);
    assert.ok(planText.includes("W3R off"), `w3r mode must read off: ${planText}`);
    assert.ok(planText.includes("渲染器 legacy"), `w3r off must keep renderer legacy: ${planText}`);
  } finally {
    await browser.close();
  }

  // --- w3 gray cohort for this entry + stop after a W3 stage failure ---
  writeConfig("w3-production-routing.json", {
    schema_version: 1,
    policy_version: "wuli-analysis-adaptive-v1",
    mode: "gray",
    gray_entry_ids: [entryId],
    max_agent_calls: 6,
    max_teacher_focus: 2,
    max_latency_seconds: 900,
    w3_failure_policy: "stop",
  });

  // --- route-preview API: w3 + off + legacy ---
  const preview = await postJson(`/api/entries/${encodeURIComponent(entryId)}/route-preview`, {
    routing_tier: "auto",
  });
  const plan = preview.route_execution_plan || {};
  assert.equal(plan.schema, "wuli.route-execution-plan.v1");
  assert.equal(plan.planned_solver_route, "w3");
  assert.equal(plan.w3r_mode, "off");
  assert.equal(plan.planned_renderer_mode, "legacy");
  assert.ok(plan.expected_stages.includes("decompose"));
  assert.ok(plan.expected_stages.includes("solver-a"));

  // --- adaptive analyze through the hanging solver model ---
  const canonicalBefore = Object.fromEntries(
    PRE_EXISTING_CANONICAL_FILES.map(name => [name, digest(path.join(entryDirFor(entryId), name))]),
  );
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "auto",
    model_id: SLOW_MODEL,
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze should return a queued job id");
  const job = await waitForJob(jobId);
  assert.equal(job.status, "failed", "a slow W3 stage must fail the adaptive job");
  assert.ok(job.error && job.error.includes("provider_timeout"), `job error must classify the timeout: ${job.error}`);
  assert.equal(job.model_id, SLOW_MODEL);
  // The job-level message is the gateway failure detail (the stage name lives
  // in the W3 shadow report telemetry below); the adaptive routing record must
  // show the W3 route stopped without a W2 fallback.
  const jobRecord = readJson(jobFileFor(jobId));
  const result = jobRecord.result || {};
  assert.equal(result.kind, "w3-shadow-analysis");
  const routing = result.adaptive_routing || {};
  assert.equal(routing.selected_route, "w3");
  assert.equal(routing.fallback && routing.fallback.to, "none");
  assert.equal(routing.fallback && routing.fallback.policy, "stop-after-w3-failure");
  assert.equal(routing.observed_metrics && routing.observed_metrics.fallback_used, false);

  // --- W3 shadow report: the real stage name and its provider failure ---
  const w3 = readJson(`${entryDirFor(entryId)}/w3-shadow-report.json`);
  assert.equal(w3.status, "failed");
  assert.equal(w3.kind, "w3-shadow-analysis");
  assert.equal(w3.canonical_answer_changed, false);
  const stages = Array.isArray(w3.stages) ? w3.stages : [];
  assert.ok(stages.length >= 1, "the failed run must keep stage telemetry");
  const decompose = stages.find(item => item.stage === "decompose");
  assert.ok(decompose, "the decompose stage must be recorded with its real name");
  assert.equal(decompose.status, "failed");
  assert.equal(decompose.failure_type, "provider_timeout");
  assert.equal(decompose.provider, "openai-compatible");
  assert.equal(decompose.model_id, SLOW_MODEL);
  // The pipeline stopped at the first failed stage: no later stage ran.
  const completedStages = stages.filter(item => item.status === "completed");
  assert.ok(completedStages.length === 0, "no stage may complete after the soft timeout");
  assert.ok(!stages.some(item => item.stage === "claim-verifier"), "claim evidence must not run after decompose fails");
  // No W3R: the shadow never reached a VERIFIED proof, so no renderer shadow
  // and no evaluation layers.
  assert.equal(w3.report || null, null, "failed shadow must not carry a report");
  assert.ok(!("evaluation_layers" in w3), "no W3R evaluation layers on a failed stage");

  // --- canonical untouched ---
  const state = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
  assert.equal(state.state, "needs-analysis-and-answer");
  for (const name of CANONICAL_ANSWER_FILES) {
    assert.ok(
      !fs.existsSync(path.join(entryDirFor(entryId), name)),
      `canonical ${name} must not be written by a W3 stage timeout`,
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
  summary.planned_route = plan.planned_solver_route;
  summary.failed_stage = decompose.stage;
  summary.failed_stage_failure_type = decompose.failure_type;
  summary.stage_telemetry = stages.map(item => ({
    stage: item.stage,
    status: item.status,
    failure_type: item.failure_type,
  }));
  writeJson("analysis-w3-soft-timeout-stage-visible-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
