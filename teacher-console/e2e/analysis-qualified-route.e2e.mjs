#!/usr/bin/env node
// Wave 5 scenario 1 (analysis-qualified-route, work-tree A2.2): the
// analysis.generate default must fail closed in front of any provider call
// when its model carries no current task-level qualification record, with a
// stable reason and zero canonical writes; restoring a qualified default makes
// the same analyze call queue and complete normally.
//
// The scenario edits the model-registry file directly inside the isolated E2E
// library (read/write of library/config/model-registry.json): it must add an
// unqualified model without disturbing e2e-claude-solver's qualification
// record, which save_model_registry_settings would drop on a full round-trip.
//
// Flow: web upload → approve-source → point defaults.analysis.generate at an
// unqualified model → analyze (routing_tier=auto) → 400 blocked with the
// stable gate reason, no job queued, canonical untouched → restore the
// qualified default → analyze → 202 → job completed with the qualified model
// in its route snapshot.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  artifactDir,
  entryDirFor,
  getJson,
  jobFileFor,
  jobsDir,
  library,
  postJson,
  readJson,
  recordFailure,
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

const QUALIFIED_MODEL = "e2e-claude-solver";
const UNQUALIFIED_MODEL = "e2e-unqualified";
const registryPath = path.join(library, "config", "model-registry.json");
// Files that only a completed analysis may create.
const ANALYSIS_ANSWER_FILES = ["answer.md", "student-solution.md", "teacher-solution.md"];
// Entry files that exist before analysis (ingest writes a solution.md
// placeholder); the blocked analyze must leave their bytes untouched.
const PRE_EXISTING_CANONICAL_FILES = ["problem.md", "record.json", "solution.md"];

const summary = { scenario: "analysis-qualified-route", status: "failed" };
let entryId = "";
let jobId = "";

function readRegistry() {
  return readJson(registryPath);
}

function writeRegistry(registry) {
  fs.writeFileSync(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
}

function listJobFiles() {
  return fs.existsSync(jobsDir)
    ? fs.readdirSync(jobsDir).filter(name => name.endsWith(".json")).sort()
    : [];
}

function fileDigests(id, names) {
  const digests = {};
  for (const name of names) {
    const file = path.join(entryDirFor(id), name);
    digests[name] = fs.existsSync(file)
      ? createHash("sha256").update(fs.readFileSync(file)).digest("hex")
      : null;
  }
  return digests;
}

function analysisAnswers(id) {
  return ANALYSIS_ANSWER_FILES.filter(name => fs.existsSync(path.join(entryDirFor(id), name)));
}

try {
  entryId = await uploadAndApproveSource(PROBLEM);
  const canonicalBefore = fileDigests(entryId, PRE_EXISTING_CANONICAL_FILES);
  const jobsBefore = listJobFiles();

  // --- 1) Unqualified default: the analyze action must fail closed ---
  const registry = readRegistry();
  if (!registry.models.some(item => item && item.id === UNQUALIFIED_MODEL)) {
    registry.models.push({
      id: UNQUALIFIED_MODEL,
      display_name: "E2E 未资格化模型",
      provider: "claude",
      model: "e2e-unqualified-model",
      capabilities: ["analysis.generate"],
    });
  }
  registry.defaults["analysis.generate"] = UNQUALIFIED_MODEL;
  writeRegistry(registry);

  const blocked = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "auto",
  }, { expect: 400 });
  assert.equal(blocked.status, "error", `blocked analyze must be an error envelope: ${JSON.stringify(blocked)}`);
  assert.ok(
    Array.isArray(blocked.errors) && blocked.errors.length === 1,
    `blocked analyze must carry exactly one error: ${JSON.stringify(blocked)}`,
  );
  const reason = blocked.errors[0];
  assert.ok(reason.includes("任务级资格验证"), `reason must name the qualification gate: ${reason}`);
  assert.ok(reason.includes(UNQUALIFIED_MODEL), `reason must name the unqualified model: ${reason}`);
  assert.ok(reason.includes("未执行任务级资格验证"), `reason must be the no-record status, not a guess: ${reason}`);

  // Stable reason: a second attempt returns the identical message.
  const blockedAgain = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "auto",
  }, { expect: 400 });
  assert.equal(blockedAgain.errors[0], reason, "gate failure reason must be stable across calls");

  // The gate fired before any provider involvement: no job queued, the
  // pipeline state is unchanged, pre-existing entry files are byte-identical
  // and no analysis-only answer file was created.
  assert.deepEqual(listJobFiles(), jobsBefore, "blocked analyze must not queue a job");
  const stateAfterBlocked = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
  assert.equal(stateAfterBlocked.state, "needs-analysis-and-answer");
  assert.deepEqual(analysisAnswers(entryId), [], "blocked analyze must not write analysis answer files");
  assert.deepEqual(
    fileDigests(entryId, PRE_EXISTING_CANONICAL_FILES),
    canonicalBefore,
    "blocked analyze must leave pre-existing entry files byte-identical",
  );

  // --- 2) Restore the qualified default: the same analyze call completes ---
  const restored = readRegistry();
  restored.defaults["analysis.generate"] = QUALIFIED_MODEL;
  writeRegistry(restored);

  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "auto",
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "qualified analyze should return a queued job id");
  const job = await waitForJob(jobId);
  assert.equal(job.status, "completed");
  assert.equal(job.model_id, QUALIFIED_MODEL);
  assert.equal(job.route_snapshot && job.route_snapshot.resolved_model_id, QUALIFIED_MODEL);
  // The resolved registry identity is claude; the E2E runtime executes it
  // through the deterministic adapter provider (see E2EAgentGateway).
  assert.equal(job.route_snapshot && job.route_snapshot.provider, "claude");
  assert.equal(job.provider, "adapter");
  const jobRecord = readJson(jobFileFor(jobId));
  assert.equal(jobRecord.result && jobRecord.result.attempts[0].provider, "adapter");

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.job_id = jobId;
  summary.unqualified_model = UNQUALIFIED_MODEL;
  summary.blocked_reason = reason;
  summary.job_files_before = jobsBefore.length;
  summary.job_files_total_at_end = listJobFiles().length;
  summary.blocked_phase_added_jobs = false;
  summary.canonical_digests_unchanged = true;
  writeJson("analysis-qualified-route-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
