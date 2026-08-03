#!/usr/bin/env node
// Work-tree T1 (analysis-core-materializer-rejection): a provider success
// that fails the deterministic physics gate must be classified as
// materializer_rejected (never output_truncated), its stage ledger must show
// physics-quality-gate=failed, and the previously approved canonical answer
// must stay untouched with an explicit "current answer version" provenance
// block for the UI (work-tree A2/A3/A4).
//
// Flow: upload charged-particle problem with [gate-reject-second-attempt] →
// analyze (first provider call passes) → canonical created → delete the B1
// checkpoint so the second run bills the provider again → analyze (second
// call returns an undefined-symbol payload) → job failed with
// failure_type=materializer_rejected → canonical unchanged + version note.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import {
  entryDirFor,
  library,
  postJson,
  readJson,
  recordFailure,
  uploadAndApproveSource,
  waitForJob,
  writeJson,
} from "./analysis-common.mjs";

const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动 [gate-reject-second-attempt]

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。`;

const summary = { scenario: "analysis-core-materializer-rejection", status: "failed" };
let entryId = "";
let firstJobId = "";
let secondJobId = "";
try {
  entryId = await uploadAndApproveSource(PROBLEM);

  // --- attempt 1: provider succeeds, gate passes, canonical created ---------
  const firstQueued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "economy",
  }, { expect: 202 });
  firstJobId = firstQueued.job.id;
  const firstJob = await waitForJob(firstJobId);
  assert.equal(firstJob.status, "completed", "first attempt must succeed");

  const entryDir = entryDirFor(entryId);
  const canonicalBefore = fs.readFileSync(path.join(entryDir, "student-solution.md"), "utf-8");
  assert.ok(canonicalBefore.includes("临界磁感应强度"), "canonical must carry the solved answer");

  // Remove the B1 checkpoint so the second attempt really bills the
  // provider again (the fixture adapter rejects on its second core call).
  fs.rmSync(path.join(library, ".cache", "core-checkpoints", `${entryId}.json`), { force: true });

  // --- attempt 2: provider succeeds, deterministic gate rejects -------------
  const secondQueued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "economy",
  }, { expect: 202 });
  secondJobId = secondQueued.job.id;
  const secondJob = await waitForJob(secondJobId);

  assert.equal(secondJob.status, "failed");
  assert.equal(
    secondJob.failure_type,
    "materializer_rejected",
    "gate rejection must never be classified as output_truncated",
  );
  assert.notEqual(secondJob.failure_type, "output_truncated");

  const jobRecord = readJson(path.join(library, ".cache", "agent-jobs", `${secondJobId}.json`));
  const result = jobRecord.result || {};
  assert.equal(result.failure_type, "materializer_rejected");
  const stages = result.stages || [];
  const stageStatus = Object.fromEntries(stages.map((stage) => [stage.name, stage.status]));
  assert.equal(stageStatus["structured-generation"], "completed", "provider output was valid JSON");
  assert.equal(stageStatus["core-materialization"], "rejected");
  assert.equal(stageStatus["physics-quality-gate"], "failed");
  assert.equal(stageStatus["canonical-promotion"], "not-run");

  // A4: the canonical answer stays the previous successful version and the
  // request carries provenance so the UI can warn before approval.
  const canonicalAfter = fs.readFileSync(path.join(entryDir, "student-solution.md"), "utf-8");
  assert.equal(canonicalAfter, canonicalBefore, "failed regeneration must not replace the canonical answer");
  const request = readJson(path.join(entryDir, "analysis-request.json"));
  const version = request.current_answer_version;
  assert.ok(version, "analysis-request must carry current_answer_version");
  assert.equal(version.is_latest_attempt, false);
  assert.ok(version.source_job_id, "version must point at the previous successful job");
  assert.equal(version.latest_attempt.status, "failed");
  assert.equal(version.latest_attempt.failure_type, "materializer_rejected");

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.first_job_id = firstJobId;
  summary.second_job_id = secondJobId;
  writeJson("analysis-core-materializer-rejection-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.first_job_id = firstJobId;
  summary.second_job_id = secondJobId;
  recordFailure(summary, error);
  throw error;
}
