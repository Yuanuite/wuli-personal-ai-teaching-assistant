#!/usr/bin/env node
// C5.5 explicit-static-diagram-collaboration: the web and CLI build-diagram
// entries share one application service. Full chain: upload clear fixture →
// MiMo facts → approve source → DeepSeek Core (fake adapter) → approve answer
// → explicit build-diagram → scene/SVG/hard gates → non-blocking soft review →
// approval invalidated back to needs-answer-review.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import process from "node:process";

import {
  baseUrl,
  entryDirFor,
  getJson,
  library,
  python,
  projectRoot,
  readJson,
  recordFailure,
  uploadEntry,
  waitForState,
  writeSummary,
} from "./visual-common.mjs";

const summary = { status: "failed", scenario: "explicit-static-diagram-collaboration" };

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

try {
  // Web chain.
  const web = await uploadEntry("clear-question.png", { ocr: "none" });
  const entryId = web.entryId;
  summary.entry_id = entryId;
  await waitForState(entryId, "needs-source-review");
  const facts = readJson(path.join(entryDirFor(entryId), "visual-facts.json"));
  assert.equal(facts.schema, "wuli.visual-facts.v1");

  // Approve source with a fixed, sufficiently long reviewed question text.
  const reviewed =
    "质量为 m 的物体在水平恒力 F 作用下沿光滑水平面从静止开始运动，求 t 秒后的速度与位移。";
  const approveSource = await postAction(entryId, "approve-source", {
    problem: reviewed,
    reviewer: "teacher",
    note: "C5.5 e2e",
  });
  assert.equal(approveSource.status, 200, `approve-source returned ${approveSource.status}: ${JSON.stringify(approveSource.payload)}`);

  // Core analysis via the fake adapter.
  const analyze = await postAction(entryId, "analyze", {
    routing_tier: "economy",
  });
  assert.equal(analyze.payload.status, "queued", JSON.stringify(analyze.payload).slice(0, 200));
  const job = await waitForJob(analyze.payload.job.url);
  assert.equal(job.status, "completed", `analysis job failed: ${job.error || job.result?.message}`);
  await waitForState(entryId, "needs-answer-review");

  // Approve the answer (its digest is the pre-diagram baseline).
  const approveAnswer = await postAction(entryId, "approve-answer", {
    reviewer: "teacher",
    note: "C5.5 e2e",
  });
  assert.equal(approveAnswer.payload.status, "approved", JSON.stringify(approveAnswer.payload).slice(0, 300));

  // Explicit build-diagram (web entry).
  const buildWeb = await postAction(entryId, "build-diagram", {
    routing_tier: "economy",
  });
  assert.equal(buildWeb.status, 200);
  assert.equal(buildWeb.payload.status, "completed", JSON.stringify(buildWeb.payload).slice(0, 400));
  const entryDir = entryDirFor(entryId);
  assert.ok(fs.existsSync(path.join(entryDir, "assets", "explanatory.svg")), "explanatory.svg must exist");
  assert.ok(fs.existsSync(path.join(entryDir, "physics-diagram-scene.json")), "scene must exist");
  assert.ok(fs.existsSync(path.join(entryDir, "physics-diagram-gate.json")), "gate report must exist");
  assert.ok(fs.existsSync(path.join(entryDir, "svg-provenance.json")), "provenance must exist");
  assert.ok("soft_review" in buildWeb.payload, "soft review must be recorded (any status)");
  assert.ok(fs.existsSync(path.join(entryDir, "diagram-build.json")), "diagram-build.json must exist");

  // C4.6: a *changed* diagram must invalidate the previous answer approval.
  // A deterministic rebuild produces identical SVG bytes, so force a real
  // change and assert the digest-based invalidation flips back to review.
  const svgPath = path.join(entryDir, "assets", "explanatory.svg");
  const svg = fs.readFileSync(svgPath, "utf8");
  fs.writeFileSync(svgPath, svg.replace("</svg>", "<!-- e2e-diagram-change --></svg>"), "utf8");
  await waitForState(entryId, "needs-answer-review");
  summary.web_diagram = "completed";
  summary.web_soft_review = buildWeb.payload.soft_review?.status;
  summary.approval_invalidated = "passed";

  // Re-approve so the CLI action runs against a consistent reviewed state.
  const approveAgain = await postAction(entryId, "approve-answer", {
    reviewer: "teacher",
    note: "C5.5 e2e re-approve after diagram change",
  });
  assert.equal(approveAgain.payload.status, "approved", JSON.stringify(approveAgain.payload).slice(0, 300));

  // CLI entry shares the same application service against the same entry.
  const adapter = path.join(projectRoot, "teacher-console", "e2e", "fake_agent_adapter.py");
  const cli = spawnSync(
    python,
    [
      path.join(projectRoot, "teacher-console", "scripts", "entry_action.py"),
      "build-diagram",
      entryId,
      "--library",
      library,
      "--tier",
      "economy",
    ],
    {
      encoding: "utf8",
      env: {
        ...process.env,
        TEACHER_CONSOLE_AGENT_PROVIDER: "adapter",
        TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND: `${python} ${adapter}`,
      },
    },
  );
  assert.equal(cli.status, 0, `entry_action build-diagram failed: ${cli.stderr || cli.stdout}`);
  const cliResult = JSON.parse(cli.stdout.trim().split("\n").at(-1));
  assert.equal(cliResult.status, "completed", JSON.stringify(cliResult).slice(0, 300));
  assert.equal(cliResult.entry_id, entryId);
  summary.cli_diagram = "completed";

  summary.status = "passed";
  writeSummary(summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  recordFailure(summary, error);
  throw error;
}
