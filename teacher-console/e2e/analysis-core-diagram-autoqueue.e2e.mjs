#!/usr/bin/env node
// Work-tree core-failure-attribution-repair D2: complex problems get the
// static physics diagram automatically after a successful core solve, while
// simple problems keep the manual, teacher-triggered enhancement path.
//
// Flow (complex): upload clear fixture → MiMo facts → approve source with a
// multi-target critical charged-particle problem → analyze → rich solve +
// auto-queued diagram.scene in ONE job → diagram artifacts + two-call budget.
// Flow (simple): same fixture, simple problem → analyze → diagram not-run.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import {
  baseUrl,
  entryDirFor,
  readJson,
  recordFailure,
  uploadEntry,
  waitForState,
  writeSummary,
} from "./visual-common.mjs";

const summary = { status: "failed", scenario: "analysis-core-diagram-autoqueue" };

async function postJson(relative, body) {
  const response = await fetch(new URL(relative, baseUrl), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Teacher-Console": "1" },
    body: JSON.stringify(body || {}),
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = JSON.parse(text);
  } catch {
    payload = { raw: text.slice(0, 300) };
  }
  return { status: response.status, payload };
}

async function postAction(entryId, action, body) {
  return postJson(`/api/entries/${encodeURIComponent(entryId)}/${action}`, body);
}

async function waitForJob(jobUrl, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  let job;
  while (Date.now() < deadline) {
    const response = await fetch(new URL(jobUrl, baseUrl));
    job = await response.json();
    if (job.status === "completed" || job.status === "failed") return job;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`job ${jobUrl} did not finish; last=${job?.status}`);
}

const COMPLEX_PROBLEM = `如图，质量为 m、电荷量为 q 的带电粒子从 P 点由静止出发，经匀强电场加速后以速度 v_0 进入垂直纸面向外的匀强磁场区域，磁感应强度大小为 B，磁场区域宽度为 d。
（1）求粒子进入磁场时速度的大小；
（2）为使粒子首次到达磁场下边界时轨迹恰好与下边界相切，求临界磁感应强度 B*。`;

const SIMPLE_PROBLEM = "质量为 m 的物体在水平恒力 F 作用下沿光滑水平面从静止开始运动，求 t 秒后的速度与位移。";

async function analyzeApprovedEntry(problem) {
  const uploaded = await uploadEntry("clear-question.png", { ocr: "none" });
  const entryId = uploaded.entryId;
  await waitForState(entryId, "needs-source-review");
  const approveSource = await postAction(entryId, "approve-source", {
    problem,
    reviewer: "teacher",
    note: "D2 e2e",
  });
  assert.equal(approveSource.status, 200, `approve-source returned ${approveSource.status}: ${JSON.stringify(approveSource.payload)}`);
  const analyze = await postAction(entryId, "analyze", { routing_tier: "economy" });
  assert.equal(analyze.payload.status, "queued", JSON.stringify(analyze.payload).slice(0, 200));
  const job = await waitForJob(analyze.payload.job.url);
  assert.equal(job.status, "completed", `analysis job failed: ${job.error || job.result?.message}`);
  return entryId;
}

// A distinct 1x1 PNG so the simple entry survives source-hash dedup against
// the clear fixture. Simple problems never auto-queue a diagram, so this
// entry does not need reviewed visual facts.
const SIMPLE_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";

async function analyzeSimpleEntry(problem) {
  const filename = `d2-simple-${Date.now()}.png`;
  const uploadResponse = await fetch(new URL(`/api/upload?filename=${encodeURIComponent(filename)}`, baseUrl), {
    method: "POST",
    headers: { "Content-Type": "image/png", "X-Teacher-Console": "1" },
    body: Buffer.from(SIMPLE_PNG_BASE64, "base64"),
  });
  assert.equal(uploadResponse.status, 200, `upload ${filename} returned ${uploadResponse.status}`);
  const uploaded = await uploadResponse.json();
  const report = await postJson("/api/run-upload", { filename: uploaded.filename, ocr: "none", subject: "高中物理" });
  const item = (report.payload.results || []).find(entry => entry.status === "ingested");
  assert.ok(item, "run-upload should have ingested the simple fixture");
  const entryId = item.entry_id;
  await waitForState(entryId, "needs-source-review");
  const cleanJob = item.source_clean && item.source_clean.job;
  if (cleanJob && cleanJob.id) {
    await waitForJob(`/api/jobs/${encodeURIComponent(cleanJob.id)}`);
  }
  const approveSource = await postAction(entryId, "approve-source", {
    problem,
    reviewer: "teacher",
    note: "D2 e2e simple",
  });
  assert.equal(approveSource.status, 200, `approve-source returned ${approveSource.status}: ${JSON.stringify(approveSource.payload)}`);
  const analyze = await postAction(entryId, "analyze", { routing_tier: "economy" });
  assert.equal(analyze.payload.status, "queued", JSON.stringify(analyze.payload).slice(0, 200));
  const job = await waitForJob(analyze.payload.job.url);
  assert.equal(job.status, "completed", `analysis job failed: ${job.error || job.result?.message}`);
  return entryId;
}

try {
  // --- complex problem: rich solve + auto-queued diagram --------------------
  const complexId = await analyzeApprovedEntry(COMPLEX_PROBLEM);
  summary.complex_entry_id = complexId;
  const complexRequest = readJson(path.join(entryDirFor(complexId), "analysis-request.json"));
  assert.equal(complexRequest.complexity?.decision, "decompose", "charged critical problem must screen as complex");
  assert.equal(complexRequest.complexity?.contract, "wuli.core-rich.v2");
  assert.equal(complexRequest.diagram_task?.status, "completed", JSON.stringify(complexRequest.diagram_task).slice(0, 300));
  assert.equal(complexRequest.diagram_task?.reason, "auto-queued-complex");
  assert.equal(complexRequest.agent_call_count, 2, "solve + diagram must bill exactly two calls");

  const routing = complexRequest.adaptive_routing || {};
  assert.equal(routing.limits?.max_agent_calls, 2, "complex runs get a two-call budget");
  assert.equal(routing.observed_metrics?.agent_call_count, 2, "observed calls must match the real run");

  const complexDir = entryDirFor(complexId);
  assert.ok(fs.existsSync(path.join(complexDir, "physics-diagram-scene.json")), "auto diagram scene must exist");
  assert.ok(fs.existsSync(path.join(complexDir, "assets", "explanatory.svg")), "auto diagram SVG must exist");
  assert.ok(fs.existsSync(path.join(complexDir, "physics-diagram-gate.json")), "auto diagram gate must exist");
  summary.complex_diagram = "completed";

  // --- simple problem: diagram stays a manual enhancement -------------------
  const simpleId = await analyzeSimpleEntry(SIMPLE_PROBLEM);
  summary.simple_entry_id = simpleId;
  const simpleRequest = readJson(path.join(entryDirFor(simpleId), "analysis-request.json"));
  assert.equal(simpleRequest.complexity?.decision, "w2", "simple problem must stay on the compact route");
  assert.equal(simpleRequest.diagram_task?.status, "not-run");
  assert.equal(simpleRequest.diagram_task?.reason, "optional-post-answer-enhancement");
  assert.equal(simpleRequest.agent_call_count, 1);
  assert.equal(simpleRequest.adaptive_routing?.limits?.max_agent_calls, 1);
  assert.ok(
    !fs.existsSync(path.join(entryDirFor(simpleId), "physics-diagram-scene.json")),
    "simple problems must not auto-generate a diagram scene",
  );
  summary.simple_diagram = "not-run";

  summary.status = "passed";
  writeSummary(summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  recordFailure(summary, error);
  throw error;
}
