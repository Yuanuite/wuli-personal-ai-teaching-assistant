#!/usr/bin/env node
// Wave 4 A4.5 scenario 3 (analysis-w3-verified-w3r-shadow): a legacy-adaptive
// W3 run that reaches a VERIFIED proof attaches the deterministic W3R shadow
// render, keeps the canonical answer written by the LEGACY renderer
// byte-identical to what a non-W3R run would produce, and never lets the W3R
// output replace the canonical (work-tree T5/V7, A2.4/A3.1).
//
// run_e2e.py keeps TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW=1, so the W3 shadow
// runs the claim evidence path and the deterministic fake adapter answers
// claim-verifier with "pass" verdicts → aggregation VERIFIED →
// attach_w3r_shadow renders the shadow (zero solver tokens). The adaptive
// analyze promotes the W3 candidate through the legacy renderer with an
// explicit logic-flowchart diagram plugin (the production candidate always
// references assets/explanatory.svg, so the promotion needs it to exist).
//
// Flow: upload → approve-source (page) → route plan "W3 已验证 + W3R shadow"
// → route-preview API (w3 + w3r-shadow) → analyze (adaptive, VERIFIED proof)
// → job completed → w3-shadow-report VERIFIED + w3r_shadow completed +
// production_candidate_unchanged → renderer decision legacy/w3r-shadow-only →
// canonical == deterministic legacy render, never the W3R render output.

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

const W3R_MODE = "shadow";
const DIAGRAM_PLUGIN_ID = "logic-flowchart";
const IMAGE_BLOCK = "![解题流程图（可选插件）](assets/explanatory.svg)\n\n";

const summary = { scenario: "analysis-w3-verified-w3r-shadow", status: "failed" };
let entryId = "";
let jobId = "";

function writeConfig(name, value) {
  const target = path.join(library, "config", name);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`);
}

try {
  // --- temp routing configs: legacy-adaptive W3 + W3R shadow ---
  writeConfig("analysis-production-routing.json", {
    schema_version: 1,
    policy_version: "wuli-core-first-routing-v1",
    mode: "legacy-adaptive",
    max_latency_seconds: 90,
  });
  writeConfig("w3r-production-routing.json", {
    schema_version: 1,
    policy_version: "wuli-w3r-routing-v1",
    mode: W3R_MODE,
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

  // --- page: upload → approve → route plan must read "W3 已验证 + W3R shadow" ---
  const { browser, page, browserErrors } = await createSession();
  let planText = "";
  try {
    entryId = await uploadAndApproveSource(page, {
      filename: "w3-verified-w3r-shadow-e2e.png",
      problem: PROBLEM,
      note: "E2E 已核对题干、公式与边界条件",
    });
    const planTarget = page.locator("#route-execution-plan");
    // The plan copy is rendered into this element's text; the element itself
    // keeps the initial hidden attribute (app.js only toggles the CSS class),
    // so we assert the rendered DOM text, not browser visibility.
    await planTarget.waitFor({ state: "attached", timeout: 20_000 });
    await page.waitForFunction(() =>
      document.querySelector("#route-execution-plan")?.textContent?.includes("W3 已验证 + W3R shadow"),
    );
    planText = String(await planTarget.textContent());
    assert.ok(planText.includes("计划解析路线：W3"), `route plan must plan W3: ${planText}`);
    assert.ok(planText.includes("W3 已验证 + W3R shadow"), `w3+shadow plan must read W3 已验证 + W3R shadow: ${planText}`);
    assert.ok(planText.includes("w3r-shadow"), `renderer must read w3r-shadow: ${planText}`);
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

  // --- route-preview API: w3 + w3r shadow + w3r-shadow renderer ---
  const preview = await postJson(`/api/entries/${encodeURIComponent(entryId)}/route-preview`, {
    routing_tier: "auto",
  });
  const plan = preview.route_execution_plan || {};
  assert.equal(plan.schema, "wuli.route-execution-plan.v1");
  assert.equal(plan.planned_solver_route, "w3");
  assert.equal(plan.w3r_mode, "shadow");
  assert.equal(plan.planned_renderer_mode, "w3r-shadow");
  assert.ok(plan.expected_stages.includes("proof-aggregation"));

  // --- adaptive analyze: W3 + VERIFIED proof + W3R shadow attach ---
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "auto",
    diagram_plugin_id: DIAGRAM_PLUGIN_ID,
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze should return a queued job id");
  const job = await waitForJob(jobId);
  assert.equal(job.status, "completed", `adaptive W3 analyze must complete: ${JSON.stringify(job)}`);
  assert.equal(job.model_id, "e2e-claude-solver");

  // --- W3 shadow report: VERIFIED proof + completed W3R shadow ---
  const w3 = readJson(`${entryDirFor(entryId)}/w3-shadow-report.json`);
  assert.equal(w3.status, "completed");
  assert.equal(w3.kind, "w3-shadow-analysis");
  assert.equal(w3.canonical_answer_changed, false);
  const report = w3.report || {};
  const claimEvidence = report.claim_evidence_shadow || {};
  assert.equal(claimEvidence.status, "completed");
  assert.equal(claimEvidence.aggregation && claimEvidence.aggregation.status, "VERIFIED");
  const w3rShadow = report.w3r_shadow || {};
  assert.equal(w3rShadow.status, "completed", "the W3R shadow render must complete on a VERIFIED proof");
  assert.equal(w3rShadow.production_candidate_unchanged, true, "the W3R shadow must not alter the W3 candidate");
  assert.equal(w3rShadow.solver_fallback_allowed, false);
  assert.equal(report.evaluation_layers && report.evaluation_layers.proof_fidelity.status, "VERIFIED");
  assert.equal(report.evaluation_layers && report.evaluation_layers.teaching_render.status, "completed");
  const w3rStudent = String((w3rShadow.render_result || {}).student_solution_md || "").trim();
  assert.ok(w3rStudent.length > 0, "the W3R shadow render must carry a student document");

  // --- renderer decision: shadow mode keeps the legacy canonical ---
  const record = readJson(jobFileFor(jobId));
  const result = record.result || {};
  const renderer = (result.adaptive_routing || {}).renderer || {};
  assert.equal((result.adaptive_routing || {}).selected_route, "w3");
  assert.equal(renderer.selected_renderer, "legacy", "shadow mode must never select the W3R renderer");
  assert.equal(renderer.reason, "w3r-shadow-only");
  assert.equal(renderer.mode, "shadow");

  // --- canonical invariance: the legacy render, not the W3R render ---
  const studentPath = path.join(entryDirFor(entryId), "student-solution.md");
  assert.ok(fs.existsSync(studentPath), "the adaptive promotion must write the canonical student solution");
  const canonical = fs.readFileSync(studentPath, "utf8");
  const recommended = String(report.recommended_student_solution || "").trim();
  const expectedLegacy = `# 解析（学生版）\n\n${IMAGE_BLOCK}${recommended}\n`;
  assert.equal(canonical, expectedLegacy, "canonical must be byte-identical to the legacy W3 render");
  assert.notEqual(canonical, `# 解析（学生版）\n\n${IMAGE_BLOCK}${w3rStudent}\n`, "canonical must not be the W3R render");
  assert.ok(!canonical.includes("建模与符号"), "the W3R render must not leak into the canonical answer");

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.job_id = jobId;
  summary.route_plan_text = planText;
  summary.planned_renderer_mode = plan.planned_renderer_mode;
  summary.renderer = renderer;
  summary.aggregation = claimEvidence.aggregation && claimEvidence.aggregation.status;
  summary.w3r_shadow_status = w3rShadow.status;
  summary.w3r_shadow_production_candidate_unchanged = w3rShadow.production_candidate_unchanged;
  summary.canonical_equals_legacy_render = true;
  summary.canonical_equals_w3r_render = false;
  writeJson("analysis-w3-verified-w3r-shadow-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
