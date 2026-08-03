#!/usr/bin/env node
// Wave 4 A4.5 scenario 4 (analysis-w3r-off-not-run): with W3R mode=off, the
// same VERIFIED W3 run must never select the W3R renderer — the renderer
// decision is legacy (w3r-disabled), the route plan reads "W3R off · 渲染器
// legacy", and the canonical answer is byte-identical to the legacy W3 render
// (exactly what the w3r-shadow scenario produced), never the W3R output.
//
// Honest scope note: the internal ``w3r_shadow`` telemetry attach inside
// w3_pipeline.run_shadow is driven by the claim-evidence flag
// (TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW=1), not by the w3r config mode, so
// the report carries that field in both the shadow and off modes. What the
// w3r config mode actually controls — and what this scenario asserts — is the
// OBSERVABLE renderer selection (select_renderer → legacy/w3r-disabled), the
// planned renderer mode (legacy) and the canonical bytes (legacy render).
//
// Flow: upload → approve-source (page) → route plan "W3 · W3R off · 渲染器
// legacy" → route-preview API (w3 + off + legacy) → analyze (adaptive,
// VERIFIED proof) → job completed → renderer decision legacy/w3r-disabled →
// canonical == deterministic legacy render.

import assert from "node:assert/strict";
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
// completeness language); the deterministic adapter answers every stage. The
// [w3r-ready] marker switches the deterministic W3 fixture to a single-value
// target so the W3R shadow render completes (see fake_agent_adapter).
const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。

[w3r-ready]`;

const DIAGRAM_PLUGIN_ID = "logic-flowchart";
const IMAGE_BLOCK = "![解题流程图（可选插件）](assets/explanatory.svg)\n\n";

const summary = { scenario: "analysis-w3r-off-not-run", status: "failed" };
let entryId = "";
let jobId = "";

function writeConfig(name, value) {
  const target = path.join(library, "config", name);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`);
}

try {
  // --- temp routing configs: legacy-adaptive W3 + W3R OFF ---
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

  // --- page: upload → approve → route plan must read "W3R off · 渲染器 legacy" ---
  const { browser, page, browserErrors } = await createSession();
  let planText = "";
  try {
    entryId = await uploadAndApproveSource(page, {
      filename: "w3r-off-not-run-e2e.png",
      problem: PROBLEM,
      note: "E2E 已核对题干、公式与边界条件",
    });
    const planTarget = page.locator("#route-execution-plan");
    // The plan copy is rendered into this element's text; the element itself
    // keeps the initial hidden attribute (app.js only toggles the CSS class),
    // so we assert the rendered DOM text, not browser visibility.
    await planTarget.waitFor({ state: "attached", timeout: 20_000 });
    await page.waitForFunction(() =>
      document.querySelector("#route-execution-plan")?.textContent?.includes("W3R off"),
    );
    planText = String(await planTarget.textContent());
    assert.ok(planText.includes("计划解析路线：W3"), `route plan must plan W3: ${planText}`);
    assert.ok(planText.includes("W3R off"), `w3r mode must read off: ${planText}`);
    assert.ok(planText.includes("渲染器 legacy"), `w3r off must keep the legacy renderer: ${planText}`);
    assert.ok(!planText.includes("W3R shadow"), `w3r off must never claim a W3R shadow: ${planText}`);
  } finally {
    await browser.close();
  }

  // --- w3 gray cohort for this entry ---
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

  // --- adaptive analyze: W3 + VERIFIED proof, W3R mode off ---
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "auto",
    diagram_plugin_id: DIAGRAM_PLUGIN_ID,
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze should return a queued job id");
  const job = await waitForJob(jobId);
  assert.equal(job.status, "completed", `adaptive W3 analyze must complete: ${JSON.stringify(job)}`);
  assert.equal(job.model_id, "e2e-claude-solver");

  // --- W3 shadow report: still VERIFIED (the W3 route itself runs) ---
  const w3 = readJson(`${entryDirFor(entryId)}/w3-shadow-report.json`);
  assert.equal(w3.status, "completed");
  assert.equal(w3.canonical_answer_changed, false);
  const report = w3.report || {};
  const claimEvidence = report.claim_evidence_shadow || {};
  assert.equal(claimEvidence.status, "completed");
  assert.equal(claimEvidence.aggregation && claimEvidence.aggregation.status, "VERIFIED");

  // --- renderer decision: W3R off → legacy, never w3r ---
  const record = readJson(jobFileFor(jobId));
  const result = record.result || {};
  const renderer = (result.adaptive_routing || {}).renderer || {};
  assert.equal((result.adaptive_routing || {}).selected_route, "w3");
  assert.equal(renderer.selected_renderer, "legacy", "W3R off must keep the legacy renderer");
  assert.equal(renderer.reason, "w3r-disabled");
  assert.equal(renderer.mode, "off");

  // --- canonical invariance: the legacy render, never the W3R output ---
  const studentPath = path.join(entryDirFor(entryId), "student-solution.md");
  assert.ok(fs.existsSync(studentPath), "the adaptive promotion must write the canonical student solution");
  const canonical = fs.readFileSync(studentPath, "utf8");
  const recommended = String(report.recommended_student_solution || "").trim();
  const expectedLegacy = `# 解析（学生版）\n\n${IMAGE_BLOCK}${recommended}\n`;
  assert.equal(canonical, expectedLegacy, "canonical must be byte-identical to the legacy W3 render");
  // The claim-evidence shadow may attach internal w3r_shadow telemetry even in
  // off mode (it is claim-evidence-driven, not w3r-config-driven); the
  // observable contract is that the W3R output never becomes canonical.
  const w3rShadow = report.w3r_shadow || {};
  if (w3rShadow.status === "completed") {
    const w3rStudent = String((w3rShadow.render_result || {}).student_solution_md || "").trim();
    assert.notEqual(canonical, `# 解析（学生版）\n\n${IMAGE_BLOCK}${w3rStudent}\n`, "W3R off must never promote the W3R render");
    assert.ok(!canonical.includes("建模与符号"), "the W3R render must not leak into the canonical answer");
  }

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.job_id = jobId;
  summary.route_plan_text = planText;
  summary.planned_renderer_mode = plan.planned_renderer_mode;
  summary.renderer = renderer;
  summary.aggregation = claimEvidence.aggregation && claimEvidence.aggregation.status;
  summary.w3r_shadow_telemetry_status = w3rShadow.status || "missing";
  summary.canonical_equals_legacy_render = true;
  summary.canonical_equals_w3r_render = false;
  writeJson("analysis-w3r-off-not-run-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
