#!/usr/bin/env node
// Wave E2 scenario 1 (analysis-core-report): one successful Core analysis
// whose full run ledger must be reproducible by
// teacher-console/scripts/analysis_run_report.py --job-id <id> --format json
// (contract wuli.analysis-run-report.v1, work-tree sections 5/10).
//
// Flow: web upload of a synthetic clear image (ocr=none) → approve-source →
// analyze (routing_tier=economy) → wait for job → run the report script →
// assert run identity/mode/counts/steps and privacy redaction.

import assert from "node:assert/strict";

import {
  artifactDir,
  assertReportExit,
  getJson,
  jobFileFor,
  postJson,
  privacyViolations,
  projectRoot,
  readJson,
  recordFailure,
  reportScriptStatus,
  runReportScript,
  uploadAndApproveSource,
  waitForJob,
  writeJson,
} from "./analysis-common.mjs";

// Fixed, sufficiently long reviewed problem (multi-target charged-particle
// critical motion). The deterministic adapter answers the charged branch.
const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。`;

const summary = {
  scenario: "analysis-core-report",
  status: "failed",
  problem_targets: 2,
};
let entryId = "";
let jobId = "";
try {
  entryId = await uploadAndApproveSource(PROBLEM);
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "economy",
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze should return a queued job id");
  const job = await waitForJob(jobId);

  // --- deterministic skeleton assertions (independent of the report script) --
  assert.equal(job.status, "completed");
  assert.equal(job.kind, "analysis.generate");
  assert.equal(job.entry_id, entryId);
  assert.ok(job.result && job.result.usage, "completed job should carry aggregated usage");
  assert.equal(job.result.usage.completion_tokens, 40);
  assert.equal(job.result.usage.total_tokens, 120);

  const jobRecord = readJson(jobFileFor(jobId));
  const result = jobRecord.result || {};
  assert.equal(result.status, "completed");
  assert.ok(Array.isArray(result.attempts) && result.attempts.length >= 1);
  assert.equal(result.attempts[0].status, "completed");
  assert.equal(result.attempts[0].provider, "adapter");
  assert.ok(result.adaptive_routing, "core-first run should persist adaptive_routing");
  assert.equal(result.adaptive_routing.mode, "core-first");
  assert.equal(result.adaptive_routing.route, "core");
  assert.equal(jobRecord.model_id, "e2e-claude-solver", "resolved registry model id");
  assert.ok(result.model, "gateway result should expose the adapter model identity");

  // --- report script (Wave D) ---
  const dependency = reportScriptStatus();
  const reportEvidence = {
    job_id: jobId,
    entry_id: entryId,
    job_status: job.status,
    job_model_id: jobRecord.model_id,
    job_provider: jobRecord.provider,
    resolved_model_id: result.model_id || jobRecord.model_id,
    upstream_model: result.model,
    expected_mode: "core-first",
    expected_route: "core",
    provider_attempt_count: result.attempts.length,
    step_count: (result.stages || []).length,
    steps: (result.stages || []).map(stage => ({
      name: stage.name,
      status: stage.status,
      verification_result: stage.verification_result || null,
    })),
  };
  if (dependency.present) {
    const reportRun = runReportScript(["--job-id", jobId, "--format", "json"]);
    const reportExit = assertReportExit(reportRun);
    const report = JSON.parse(reportRun.stdout.trim());
    assert.equal(report.schema, "wuli.analysis-run-report.v1");
    assert.equal(report.run.job_id, jobId);
    assert.equal(report.run.entry_id, entryId);
    assert.equal(report.run.status, "completed");
    assert.equal(report.run.mode, "core-first");
    assert.equal(report.run.selected_route, "core");
    assert.ok(Array.isArray(report.runtime_identities) && report.runtime_identities.length >= 1);
    // Production carries deepseek-* model identities through this same field;
    // the E2E runtime is the deterministic registry model + adapter.
    const identities = JSON.stringify(report.runtime_identities);
    assert.ok(
      identities.includes("e2e-claude-solver") || identities.includes("fake-e2e-core"),
      `runtime_identities should contain the resolved model identity: ${identities}`,
    );
    assert.ok(report.counts.provider_attempt_count >= 1);
    assert.ok(Array.isArray(report.steps) && report.steps.length >= 1);
    for (const step of report.steps) {
      assert.ok(
        ["passed", "failed", "provisional", "not-run"].includes(step.verification_result),
        `step ${step.step_id || step.name} must carry a verification_result`,
      );
    }
    const violations = privacyViolations(JSON.stringify(report));
    assert.deepEqual(violations, [], `report leaked sensitive fields: ${violations}`);

    // Web/CLI consistency: the job detail endpoint must agree with the report.
    const jobDetail = await getJson(`/api/jobs/${encodeURIComponent(jobId)}`);
    assert.equal(jobDetail.status, report.run.status);
    assert.equal(jobDetail.result && jobDetail.result.model, result.model);
    reportEvidence.report = report;
    reportEvidence.exit_code = reportExit;
    reportEvidence.exit_reason = report.verification_summary.exit_reason;
    reportEvidence.step_results = Object.fromEntries(
      report.steps.map(step => [step.step_id, step.verification_result]),
    );
  }
  summary.status = dependency.present ? "passed" : "dependency-missing";
  summary.dependency = dependency.reason || "present";
  summary.job_id = jobId;
  summary.entry_id = entryId;
  summary.report_evidence = reportEvidence;
  writeJson("analysis-core-report-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
  if (!dependency.present) {
    // Clear environment limitation: the scenario skeleton is complete but the
    // Wave D script it must exercise does not exist yet. Not a pass.
    console.error(`analysis-core-report: ${dependency.reason}`);
    process.exit(1);
  }
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
