#!/usr/bin/env node
// Wave E2 scenario 2 (analysis-core-truncated): a reasoning-only truncation
// must be classified as output_truncated and its usage must be preserved.
//
// The scenario registers (in run_e2e.py) an openai-compatible model pointing
// at a local mock endpoint that always answers finish_reason=length with
// message.content="" and message.reasoning_content="x"*500
// (usage.completion_tokens=6000), then passes model_id on the analyze call so
// only this job routes through the truncating adapter.
//
// Flow: upload → approve-source → analyze(model_id=e2e-mock-truncated) →
// job failed → run analysis_run_report.py --job-id <id> --verify.

import assert from "node:assert/strict";

import {
  artifactDir,
  getJson,
  jobFileFor,
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

const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。`;

const TRUNCATION_MODEL_ID = "e2e-mock-truncated";

const summary = { scenario: "analysis-core-truncated", status: "failed" };
let entryId = "";
let jobId = "";
try {
  entryId = await uploadAndApproveSource(PROBLEM);
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "economy",
    model_id: TRUNCATION_MODEL_ID,
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze should return a queued job id");
  const job = await waitForJob(jobId);

  // --- deterministic skeleton assertions (independent of the report script) --
  assert.equal(job.status, "failed");
  assert.equal(job.failure_type, "output_truncated", "job must classify truncation, not candidate_no_change");
  assert.equal(job.model_id, TRUNCATION_MODEL_ID);
  assert.equal(job.provider, "openai-compatible");
  assert.ok(job.result && job.result.usage, "failed job must carry usage, never measurement=unavailable");
  assert.equal(job.result.usage.completion_tokens, 6000);

  const jobRecord = readJson(jobFileFor(jobId));
  const result = jobRecord.result || {};
  assert.equal(result.status, "failed");
  assert.equal(result.failure_type, "output_truncated");
  assert.ok(Array.isArray(result.attempts) && result.attempts.length >= 1);
  const attempt = result.attempts[0];
  assert.equal(attempt.finish_reason, "length");
  assert.equal(attempt.content_chars, 0);
  assert.equal(attempt.reasoning_chars, 500);
  assert.equal(attempt.failure_type, "output_truncated");
  assert.ok(attempt.token_usage, "adapter failure envelope must attach usage to the attempt");
  assert.equal(attempt.token_usage.completion_tokens, 6000);
  assert.equal(attempt.token_usage.total_tokens, 6100);

  // --- report script (Wave D) with --verify ---
  const dependency = reportScriptStatus();
  const reportEvidence = {
    job_id: jobId,
    entry_id: entryId,
    job_status: job.status,
    job_failure_type: job.failure_type,
    job_result_diagnosed_failure_type: result.diagnosed_failure_type || null,
    attempt_finish_reason: attempt.finish_reason,
    attempt_reasoning_chars: attempt.reasoning_chars,
    attempt_usage_completion_tokens: attempt.token_usage && attempt.token_usage.completion_tokens,
    recorded_failure_type: job.failure_type,
    expected_verify_exit_code: 3,
  };
  if (dependency.present) {
    // Exit code 3 = job failed / report evidence incomplete (doc section 5).
    const verifyRun = runReportScript(["--job-id", jobId, "--verify"]);
    assert.equal(
      verifyRun.status,
      3,
      `--verify should exit 3 on a failed job (got ${verifyRun.status}): ${verifyRun.stderr || verifyRun.stdout}`,
    );
    const reportRun = runReportScript(["--job-id", jobId, "--format", "json"]);
    assert.equal(reportRun.status, 3, `--format json should exit 3 on a failed job: ${reportRun.stderr || reportRun.stdout}`);
    const report = JSON.parse(reportRun.stdout.trim());
    assert.equal(report.schema, "wuli.analysis-run-report.v1");
    assert.equal(report.run.job_id, jobId);
    assert.equal(report.run.entry_id, entryId);
    assert.equal(report.run.status, "failed");
    assert.equal(report.run.mode, "core-first");
    // Diagnostic must be output_truncated; the recorded type is preserved as
    // recorded (both coexist when the recorded type was candidate_no_change).
    const diagnosis = report.verification_summary.diagnosis;
    assert.equal(diagnosis.diagnosed_failure_type, "output_truncated");
    assert.ok(
      ["output_truncated", "candidate_no_change"].includes(diagnosis.recorded_failure_type),
      `recorded_failure_type must be preserved: ${diagnosis.recorded_failure_type}`,
    );
    // Usage must not be unavailable and must keep the truncated completion
    // token count from the failure envelope.
    const usage = report.terminal.usage || {};
    assert.notEqual(usage.measurement, "unavailable", "usage must not be unavailable");
    assert.equal(usage.completion_tokens, 6000);
    assert.equal(usage.total_tokens, 6100);
    const violations = privacyViolations(JSON.stringify(report));
    assert.deepEqual(violations, [], `report leaked sensitive fields: ${violations}`);

    // Web/CLI consistency on the failed job.
    const jobDetail = await getJson(`/api/jobs/${encodeURIComponent(jobId)}`);
    assert.equal(jobDetail.status, "failed");
    assert.equal(jobDetail.failure_type, "output_truncated");
    reportEvidence.report = report;
    reportEvidence.exit_code = verifyRun.status;
    reportEvidence.exit_reason = report.verification_summary.exit_reason;
  }
  summary.status = dependency.present ? "passed" : "dependency-missing";
  summary.dependency = dependency.reason || "present";
  summary.job_id = jobId;
  summary.entry_id = entryId;
  summary.report_evidence = reportEvidence;
  writeJson("analysis-core-truncated-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
  if (!dependency.present) {
    console.error(`analysis-core-truncated: ${dependency.reason}`);
    process.exit(1);
  }
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
