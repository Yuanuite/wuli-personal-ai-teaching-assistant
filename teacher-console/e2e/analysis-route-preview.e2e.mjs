#!/usr/bin/env node
// Wave 5 scenario 3 (analysis-route-preview, work-tree A4.1): the read-only
// route preview must agree with the subsequently queued job's frozen route
// snapshot (resolved model, provider, config digest), expose a qualified
// model with an ordered deadline budget, and report an unqualified default as
// blocked without guessing a route.
//
// Flow: upload → approve-source → route-preview (routing_tier=economy) →
// ready with resolved model / qualification=qualified / ordered budget →
// analyze (economy) → job completed → job.route_snapshot matches the preview
// → point defaults.economy at an unqualified model (direct registry-file edit
// in the isolated E2E library) → route-preview (economy) → blocked with the
// stable qualification reason; the unqualified status is grounded in the
// registry state (no qualification record), not guessed.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import {
  artifactDir,
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

const summary = { scenario: "analysis-route-preview", status: "failed" };
let entryId = "";
let jobId = "";
try {
  entryId = await uploadAndApproveSource(PROBLEM);

  // --- 1) Qualified default preview (routing_tier=economy) ---
  const preview = await postJson(`/api/entries/${encodeURIComponent(entryId)}/route-preview`, {
    routing_tier: "economy",
  });
  assert.equal(preview.schema, "wuli.route-preview.v1");
  assert.equal(preview.kind, "analysis.generate");
  assert.equal(preview.status, "ready");
  assert.equal(preview.resolved_model_id, QUALIFIED_MODEL);
  assert.equal(preview.provider, "claude");
  assert.equal(preview.upstream_model, "e2e-solver-model");
  assert.equal(preview.qualification && preview.qualification.status, "qualified");
  assert.equal(
    preview.qualification.record && preview.qualification.record.conclusion,
    "qualified",
  );
  const budget = preview.deadline_budget || {};
  assert.equal(budget.schema, "wuli.deadline-budget.v1");
  assert.ok(
    budget.http_soft_deadline + budget.cleanup_grace <= budget.attempt_deadline &&
      budget.attempt_deadline <= budget.task_deadline,
    `deadline layers must satisfy http_soft+grace <= attempt <= task: ${JSON.stringify(budget)}`,
  );
  assert.ok(
    typeof preview.route_config_digest === "string" && preview.route_config_digest.length > 0,
    "preview must carry a route config digest",
  );

  // --- 2) The job queued right after must freeze the same resolved identity ---
  const queued = await postJson(`/api/entries/${encodeURIComponent(entryId)}/analyze`, {
    routing_tier: "economy",
  }, { expect: 202 });
  jobId = queued.job.id;
  assert.ok(jobId, "analyze should return a queued job id");
  const job = await waitForJob(jobId);
  assert.equal(job.status, "completed");
  const snapshot = job.route_snapshot || {};
  assert.equal(snapshot.resolved_model_id, preview.resolved_model_id, "job model must match the preview");
  assert.equal(snapshot.provider, preview.provider, "job provider must match the preview");
  assert.equal(snapshot.upstream_model, preview.upstream_model);
  assert.equal(
    snapshot.config_digest,
    preview.route_config_digest,
    "job route snapshot digest must equal the preview's route_config_digest",
  );

  // --- 3) Unqualified default: preview must block, not guess a route ---
  const registry = readJson(registryPath);
  if (!registry.models.some(item => item && item.id === UNQUALIFIED_MODEL)) {
    registry.models.push({
      id: UNQUALIFIED_MODEL,
      display_name: "E2E 未资格化模型",
      provider: "claude",
      model: "e2e-unqualified-model",
      capabilities: ["analysis.generate"],
    });
  }
  registry.defaults.economy = UNQUALIFIED_MODEL;
  fs.writeFileSync(registryPath, `${JSON.stringify(registry, null, 2)}\n`);

  const blocked = await postJson(`/api/entries/${encodeURIComponent(entryId)}/route-preview`, {
    routing_tier: "economy",
  });
  assert.equal(blocked.schema, "wuli.route-preview.v1");
  assert.equal(blocked.status, "blocked");
  assert.ok(
    blocked.error && blocked.error.includes("任务级资格验证"),
    `blocked preview must carry the stable qualification reason: ${JSON.stringify(blocked)}`,
  );
  assert.ok(
    blocked.error.includes(UNQUALIFIED_MODEL),
    `blocked preview must name the unqualified model: ${JSON.stringify(blocked)}`,
  );

  // "不猜测": ground the unqualified status in the registry state — the model
  // has no qualification record and the qualified model's record survived.
  const after = readJson(registryPath);
  const unqualifiedEntry = after.models.find(item => item && item.id === UNQUALIFIED_MODEL);
  assert.ok(unqualifiedEntry, "unqualified model must be present in the registry");
  assert.equal(
    unqualifiedEntry.analysis_qualification || null,
    null,
    "the model must carry no qualification record (hence status=unqualified)",
  );
  const qualifiedEntry = after.models.find(item => item && item.id === QUALIFIED_MODEL);
  assert.ok(
    qualifiedEntry && qualifiedEntry.analysis_qualification,
    "the qualified model's qualification record must survive the registry edit",
  );

  summary.status = "passed";
  summary.entry_id = entryId;
  summary.job_id = jobId;
  summary.preview = preview;
  summary.job_snapshot = snapshot;
  summary.blocked_error = blocked.error;
  writeJson("analysis-route-preview-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  summary.entry_id = entryId;
  summary.job_id = jobId;
  recordFailure(summary, error);
  throw error;
}
