#!/usr/bin/env node
// Work-tree T1/B1 (analysis-core-checkpoint-replay): after a gate rejection
// the structured payload must be checkpointed, and the next run replays it
// zero-token (resumed_from_checkpoint=true) instead of billing the provider
// again, while the deterministic gate keeps rejecting the same payload.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
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

const PROBLEM = `# 带电粒子在电场与有界磁场中的临界运动 [gate-reject-always]

如图，质量为 $m$、电荷量为 $q$ 的带电粒子从 $P$ 点由静止出发，经匀强电场加速后以速度 $v_0$ 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 $B$，磁场区域宽度为 $d$。

（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 $B^*$。`;

const summary = { scenario: "analysis-core-checkpoint-replay", status: "failed" };
let entryId = "";
let firstJobId = "";
let secondJobId = "";
try {
  entryId = await uploadAndApproveSource(PROBLEM);
  const callCountFile = path.join(os.tmpdir(), `wuli-fake-core-calls-${entryId}.txt`);

  // --- attempt 1: provider succeeds, gate rejects, checkpoint saved --------
  const firstQueued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "economy",
  }, { expect: 202 });
  firstJobId = firstQueued.job.id;
  const firstJob = await waitForJob(firstJobId);
  assert.equal(firstJob.status, "failed");
  assert.equal(firstJob.failure_type, "materializer_rejected");
  assert.equal(fs.readFileSync(callCountFile, "utf-8").trim(), "1", "first run must bill the provider once");
  const checkpointFile = path.join(library, ".cache", "core-checkpoints", `${entryId}.json`);
  assert.ok(fs.existsSync(checkpointFile), "gate rejection must leave a replay checkpoint");
  const checkpoint = readJson(checkpointFile);
  assert.equal(checkpoint.contract, "wuli.core-solve.v1");
  assert.equal(checkpoint.entry_id, entryId);

  // --- attempt 2: zero-token replay, provider untouched --------------------
  const secondQueued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "economy",
  }, { expect: 202 });
  secondJobId = secondQueued.job.id;
  const secondJob = await waitForJob(secondJobId);

  // The replayed payload still carries the undefined symbol, so the gate
  // keeps rejecting; the repair scenario in production is a fixed gate.
  assert.equal(secondJob.status, "failed");
  assert.equal(secondJob.failure_type, "materializer_rejected");
  assert.equal(
    fs.readFileSync(callCountFile, "utf-8").trim(),
    "1",
    "checkpoint replay must not call the provider again",
  );
  const request = readJson(path.join(entryDirFor(entryId), "analysis-request.json"));
  assert.equal(request.resumed_from_checkpoint, true, "second run must be a checkpoint replay");
  const version = request.current_answer_version;
  assert.ok(version, "analysis-request must carry current_answer_version");
  assert.equal(version.is_latest_attempt, false);

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.first_job_id = firstJobId;
  summary.second_job_id = secondJobId;
  writeJson("analysis-core-checkpoint-replay-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.first_job_id = firstJobId;
  summary.second_job_id = secondJobId;
  recordFailure(summary, error);
  throw error;
}
