#!/usr/bin/env node
// Wave E3 scenario 4 (analysis-w3-backjump): rollback counting semantics.
//
// The fake adapter honours the [claim-conflict] problem marker, so with
// TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW=1 the claim verifier returns
// "conflict" for every semantic claim. The pipeline produces named challenges
// and a control transition but (by design) does NOT re-execute tasks after a
// challenge — it fuses instead. Per the work-tree counting semantics
// (section 4) this is Challenge-only: rollback_count must be 0 and must match
// the w3 report's rollback evidence (challenges present, no re-execution).
//
// Flow: upload → approve-source (with [claim-conflict] marker) →
// analyze-w3-shadow → job completed → analysis_run_report.py --entry-id <id>
// --latest --format json → assert counts.rollback_count.

import assert from "node:assert/strict";

import {
  artifactDir,
  assertReportExit,
  entryDirFor,
  postJson,
  privacyViolations,
  readJson,
  recordFailure,
  reportScriptStatus,
  runReportScript,
  uploadAndApproveSource,
  waitForJob,
  writeJson,
} from "./analysis-common.mjs";

const PROBLEM = `# 多阶段首次事件测试（[claim-conflict]）

粒子先经过边界，再返回原区域；质量为 $m$ 的物体在恒力 $F$ 作用下运动，求全部可能结果，并核对第一次进入和首次返回事件。

（1）求首次进入边界时速度的大小；
（2）求首次返回原区域时位置与初始位置的间距。`;

const summary = { scenario: "analysis-w3-backjump", status: "failed" };
let entryId = "";
let jobId = "";
try {
  entryId = await uploadAndApproveSource(PROBLEM);
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze-w3-shadow`, {
    routing_tier: "economy",
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze-w3-shadow should return a queued job id");
  const job = await waitForJob(jobId);
  assert.equal(job.status, "completed");

  // --- private W3 report: the rollback evidence (challenge-only) ---
  const w3 = readJson(`${entryDirFor(entryId)}/w3-shadow-report.json`);
  assert.equal(w3.status, "completed");
  assert.equal(w3.canonical_answer_changed, false);
  const claimEvidence = (w3.report || {}).claim_evidence_shadow || {};
  assert.equal(claimEvidence.status, "completed");
  assert.equal(claimEvidence.aggregation && claimEvidence.aggregation.status, "UNRESOLVED");
  const loop = claimEvidence.loop || {};
  const metrics = claimEvidence.metrics || {};
  assert.ok(
    Number(metrics.challenge_count) >= 1,
    "conflict shadow must produce named challenges (trigger evidence)",
  );
  assert.equal(metrics.repeated_task_count, 0, "no re-execution in this integration slice");
  assert.equal(metrics.fuse_triggered, true, "unresolved conflict must end in a fuse");
  assert.ok(loop.transition && loop.transition.action, "control loop must record a transition");

  // Challenge-only → rollback_count = 0. There is no accepted-claim
  // invalidation followed by re-execution in the evidence.
  const expectedRollbackCount = 0;
  const rollbackEvidence = {
    challenge_count: Number(metrics.challenge_count),
    localized_conflict_count: Number((loop.progress || {}).localized_conflict_count || 0),
    repeated_task_count: metrics.repeated_task_count,
    provider_calls_reused: loop.provider_calls_reused,
    fuse_triggered: metrics.fuse_triggered,
    loop_transition_action: loop.transition && loop.transition.action,
    loop_transition_terminal: loop.transition && loop.transition.control && loop.transition.control.terminal_status,
    expected_rollback_count: expectedRollbackCount,
  };

  const dependency = reportScriptStatus();
  const reportEvidence = {
    job_id: jobId,
    entry_id: entryId,
    job_status: job.status,
    rollback_evidence: rollbackEvidence,
  };
  if (dependency.present) {
    const reportRun = runReportScript(["--entry-id", entryId, "--latest", "--format", "json"]);
    const reportExit = assertReportExit(reportRun);
    const run = JSON.parse(reportRun.stdout.trim());
    assert.equal(run.schema, "wuli.analysis-run-report.v1");
    assert.equal(run.run.entry_id, entryId);
    assert.equal(run.run.job_id, jobId);
    assert.equal(run.run.status, "completed");
    // The report's rollback count must be consistent with the w3 report's
    // rollback evidence: challenges exist but no task was invalidated and
    // re-executed, so rollback_count stays 0.
    assert.equal(
      run.counts.rollback_count,
      expectedRollbackCount,
      `rollback_count must reflect challenge-only evidence (got ${run.counts.rollback_count})`,
    );
    assert.equal(run.verification_summary.rollback_observed, false);
    const violations = privacyViolations(JSON.stringify(run));
    assert.deepEqual(violations, [], `report leaked sensitive fields: ${violations}`);
    reportEvidence.report = run;
    reportEvidence.exit_code = reportExit;
    reportEvidence.exit_reason = run.verification_summary.exit_reason;
    reportEvidence.step_results = Object.fromEntries(
      run.steps.map(step => [step.step_id, step.verification_result]),
    );
  }
  summary.status = dependency.present ? "passed" : "dependency-missing";
  summary.dependency = dependency.reason || "present";
  summary.job_id = jobId;
  summary.entry_id = entryId;
  summary.report_evidence = reportEvidence;
  writeJson("analysis-w3-backjump-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
  if (!dependency.present) {
    console.error(`analysis-w3-backjump: ${dependency.reason}`);
    process.exit(1);
  }
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
