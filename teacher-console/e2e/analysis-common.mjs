#!/usr/bin/env node
// Shared helpers for the analysis-run observability E2E scenarios
// (docs/analysis-run-observability-w3-pipeline-work-tree.md, Wave E2/E3).
// These scenarios are pure HTTP/CLI checks: upload → approve-source →
// analyze | analyze-w3-shadow → wait for job → inspect job file and
// w3-shadow-report.json → run teacher-console/scripts/analysis_run_report.py.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

export const baseUrl = process.env.E2E_BASE_URL;
export const library = process.env.E2E_LIBRARY;
export const projectRoot = process.env.E2E_PROJECT_ROOT;
export const python = process.env.E2E_PYTHON || "python3";
export const artifactDir =
  process.env.E2E_ARTIFACT_DIR || path.join(projectRoot, "test-results", "e2e");
// Web-upload fixture used by the analysis scenarios (a synthetic clear image).
export const analysisFixture =
  process.env.E2E_ANALYSIS_FIXTURE ||
  path.join(
    projectRoot,
    "teacher-console",
    "tests",
    "fixtures",
    "visual-routing",
    "clear-question.png",
  );
// Wave D target: the read-only run-ledger script whose contract is fixed in
// section 5 of the work-tree doc (wuli.analysis-run-report.v1).
export const reportScript = path.join(
  projectRoot,
  "teacher-console",
  "scripts",
  "analysis_run_report.py",
);
// The report script resolves its library from the project root and has no
// --library flag; E2E scenarios run it against their temporary isolated
// library through this wrapper (which passes library= through main()).
export const reportWrapper = path.join(
  projectRoot,
  "teacher-console",
  "e2e",
  "run_analysis_report_isolated.py",
);
export const jobsDir = path.join(library, ".cache", "agent-jobs");

for (const [name, value] of Object.entries({ baseUrl, library, projectRoot })) {
  if (!value) throw new Error(`missing required E2E environment: ${name}`);
}
fs.mkdirSync(artifactDir, { recursive: true });

export function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

export async function getJson(relative) {
  const response = await fetch(new URL(relative, baseUrl));
  assert.equal(response.status, 200, `GET ${relative} returned ${response.status}`);
  return response.json();
}

// POST /api/entries/<id>/<action> style calls must carry the local-console
// header and the action lives in the URL path (not the body). Queueing
// actions (analyze / analyze-w3-shadow) answer 202 Accepted with the job;
// synchronous actions answer 200.
export async function postJson(relative, body, { expect = [200, 202], timeoutMs = 60_000 } = {}) {
  const accepted = Array.isArray(expect) ? expect : [expect];
  const deadline = Date.now() + timeoutMs;
  let lastResponse;
  let lastBody;
  while (Date.now() < deadline) {
    lastResponse = await fetch(new URL(relative, baseUrl), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Teacher-Console": "1" },
      body: JSON.stringify(body),
    });
    lastBody = await lastResponse.json();
    if (lastResponse.status !== 409) break;
    // 409 = a background job (e.g. source.clean) is still active for the
    // entry; the analyze/approve action is blocked until it finishes.
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  assert.ok(
    accepted.includes(lastResponse.status),
    `POST ${relative} returned ${lastResponse.status} (wanted ${accepted.join("/")}): ${JSON.stringify(lastBody)}`,
  );
  return lastBody;
}

export async function waitForState(entryId, expected, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs;
  let detail;
  while (Date.now() < deadline) {
    detail = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
    if (detail.state === expected) return detail;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`entry ${entryId} did not reach ${expected}; last state=${detail?.state}`);
}

export async function waitForJob(jobId, timeoutMs = 240_000) {
  const deadline = Date.now() + timeoutMs;
  let job;
  while (Date.now() < deadline) {
    job = await getJson(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (job.status === "completed" || job.status === "failed") return job;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`job ${jobId} did not finish; last status=${job?.status}`);
}

// Web upload path used by the teacher UI: POST /api/upload?filename=...
// (raw image bytes) then POST /api/run-upload (JSON {filename, ocr, subject}).
// run-upload ingests the fixture and queues a background source.clean job.
export async function uploadAndApproveSource(problem) {
  const filename = `analysis-${Date.now()}-${Math.random().toString(16).slice(2, 8)}.png`;
  const uploadResponse = await fetch(
    new URL(`/api/upload?filename=${encodeURIComponent(filename)}`, baseUrl),
    {
      method: "POST",
      headers: { "Content-Type": "image/png", "X-Teacher-Console": "1" },
      body: fs.readFileSync(analysisFixture),
    },
  );
  assert.equal(uploadResponse.status, 200, `upload ${filename} returned ${uploadResponse.status}`);
  const uploaded = await uploadResponse.json();
  assert.equal(uploaded.status, "uploaded");
  const report = await postJson("/api/run-upload", {
    filename: uploaded.filename,
    ocr: "none",
    subject: "高中物理",
  });
  const item = (report.results || []).find(entry => entry.status === "ingested");
  assert.ok(item, "run-upload should have ingested the fixture image");
  const entryId = item.entry_id;
  await waitForState(entryId, "needs-source-review");

  // run-upload queues source.clean asynchronously. Wait for it to reach a
  // terminal state BEFORE approving the source: otherwise it could overwrite
  // the approved problem.md afterwards, and an active job would 409-block the
  // subsequent approve/analyze actions.
  const cleanJob = item.source_clean && item.source_clean.job;
  if (cleanJob && cleanJob.id) {
    await waitForJob(cleanJob.id, 60_000);
  }
  await postJson(`/api/entries/${encodeURIComponent(entryId)}/approve-source`, {
    reviewer: "e2e",
    note: "E2E 已核对题干、公式与边界条件",
    problem,
  });
  await waitForState(entryId, "needs-analysis-and-answer");
  return entryId;
}

export function entryDirFor(entryId) {
  return path.join(library, "entries", entryId);
}

export function jobFileFor(jobId) {
  return path.join(jobsDir, `${jobId}.json`);
}

// ---------------------------------------------------------------------------
// Wave D dependency (teacher-console/scripts/analysis_run_report.py)
// ---------------------------------------------------------------------------

export function reportScriptStatus() {
  if (!fs.existsSync(reportScript)) {
    return {
      present: false,
      reason:
        `依赖缺失：${path.relative(projectRoot, reportScript)} 不存在。` +
        "该脚本属于 Work-Tree Wave D（--job-id/--entry-id/--latest/--format/--verify，" +
        "契约 wuli.analysis-run-report.v1，见 docs/analysis-run-observability-w3-pipeline-work-tree.md 第 5/10 节）；" +
        "Wave E2/E3 场景骨架按该契约断言，脚本落地后无需改动本场景。",
    };
  }
  return { present: true, reason: "" };
}

export function runReportScript(args) {
  return spawnSync(python, ["-B", reportWrapper, library, ...args], { encoding: "utf8" });
}

// Privacy scan shared by every scenario: the report must never carry API
// keys, Authorization headers, data-URL images, absolute host paths, or the
// reasoning body. Returns the list of offending substrings found.
export function privacyViolations(text) {
  const markers = ["sk-", "api_key", "Authorization", "data:"];
  const found = [];
  for (const marker of markers) {
    if (text.includes(marker)) found.push(marker);
  }
  if (text.includes(library)) found.push("absolute-library-path");
  const reasoningBody = /x{20,}/;
  if (reasoningBody.test(text)) found.push("reasoning-body");
  return found;
}

// Exit codes 0/2/3 are legitimate report verdicts (0=all gates pass,
// 2=PROVISIONAL evidence, 3=job failed / evidence incomplete); 4 means the
// script itself rejected the input (missing job/schema/leak) and must fail.
export function assertReportExit(processResult) {
  assert.ok(
    [0, 2, 3].includes(processResult.status),
    `report script returned ${processResult.status} (4 = input/schema/leak): ${processResult.stderr || processResult.stdout}`,
  );
  return processResult.status;
}

export function writeJson(name, value) {
  fs.writeFileSync(path.join(artifactDir, name), `${JSON.stringify(value, null, 2)}\n`);
}

export function recordFailure(summary, error) {
  writeJson("failure.json", {
    status: "failed",
    ...summary,
    error: String(error.stack || error),
  });
}
