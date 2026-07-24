#!/usr/bin/env node

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseUrl = process.env.E2E_BASE_URL;
const library = process.env.E2E_LIBRARY;
const projectRoot = process.env.E2E_PROJECT_ROOT;
const python = process.env.E2E_PYTHON || "python3";
const artifactDir = process.env.E2E_ARTIFACT_DIR || path.join(projectRoot, "test-results", "e2e");

for (const [name, value] of Object.entries({ baseUrl, library, projectRoot })) {
  if (!value) throw new Error(`missing required E2E environment: ${name}`);
}
fs.mkdirSync(artifactDir, { recursive: true });

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

async function getJson(relative) {
  const response = await fetch(new URL(relative, baseUrl));
  assert.equal(response.status, 200, `GET ${relative} returned ${response.status}`);
  return response.json();
}

async function waitForState(entryId, expected, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let detail;
  while (Date.now() < deadline) {
    detail = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
    if (detail.state === expected) return detail;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`entry ${entryId} did not reach ${expected}; last state=${detail?.state}`);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const browserErrors = [];
page.on("pageerror", error => browserErrors.push(error.message));
page.on("console", message => {
  if (message.type() === "error") browserErrors.push(message.text());
});

let entryId = "";
try {
  await page.route("**/api/run-upload", async route => {
    const request = route.request();
    const body = JSON.parse(request.postData() || "{}");
    await route.continue({
      postData: JSON.stringify({ ...body, ocr: "none" }),
      headers: { ...request.headers(), "content-type": "application/json" },
    });
  });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator(".upload-card > summary").click();
  await page.locator("#file-input").setInputFiles({
    name: "newton-second-law-e2e.png",
    mimeType: "image/png",
    buffer: png,
  });
  await page.locator("#upload-run").click();
  await page.locator("#entry-view:not(.hidden)").waitFor();
  await page.waitForFunction(() => document.querySelector("#entry-id")?.textContent?.trim());
  entryId = String(await page.locator("#entry-id").textContent()).trim();
  assert.ok(entryId, "uploaded entry id should be visible");
  await waitForState(entryId, "needs-source-review");

  await page.locator("#problem-editor").fill(
    "# 牛顿第二定律\n\n质量为 $m$ 的物体在水平合力 $F$ 作用下运动，求物体加速度的大小和方向，并说明所用物理规律。",
  );
  await page.locator("#source-note").fill("E2E 已核对题干、公式和方向");
  await page.locator("#approve-source").click();
  await waitForState(entryId, "needs-analysis-and-answer");

  await page.locator("#run-analysis").click();
  await waitForState(entryId, "needs-answer-review");
  await page.locator("#approve-answer").click();
  await waitForState(entryId, "ready-to-finish");

  await page.locator('[data-tab="delivery"]').click();
  await page.locator("#finish-entry").click();
  const delivered = await waitForState(entryId, "delivered");
  await page.locator("#download-list .download-card").first().waitFor();

  const entryDir = path.join(library, "entries", entryId);
  const delivery = readJson(path.join(entryDir, "delivery.json"));
  const manifestPath = path.join(delivery.output, "delivery-manifest.json");
  const manifest = readJson(manifestPath);
  assert.equal(manifest.status, "delivered");
  assert.equal(delivered.state, "delivered");
  assert.ok(manifest.files.includes("带答案错题.md"));
  assert.ok(manifest.files.includes("student-package.zip"));
  assert.ok(fs.existsSync(path.join(delivery.output, "带答案错题.md")));

  const evaluation = readJson(path.join(entryDir, "evaluation.json"));
  const checks = Object.fromEntries(evaluation.checks.map(check => [check.id, check]));
  assert.equal(checks.source_review.status, "passed");
  assert.equal(checks.answer_review_current.status, "passed");
  assert.equal(checks.local_reference_safety.status, "passed");
  assert.notEqual(checks.delivery_artifacts.status, "failed");
  assert.equal(manifest.evaluation.file, path.join(delivery.output, "evaluation.json"));

  const qualityProcess = spawnSync(
    python,
    [
      path.join(projectRoot, "teacher-console", "scripts", "pipeline_quality_eval.py"),
      entryId,
      "--library",
      library,
      "--jsonl",
    ],
    { encoding: "utf8" },
  );
  assert.equal(qualityProcess.status, 0, qualityProcess.stderr || qualityProcess.stdout);
  const quality = JSON.parse(qualityProcess.stdout.trim().split("\n").at(-1));
  assert.equal(quality.entry_id, entryId);
  assert.equal(quality.dimensions.pipeline_accuracy.score, 100);
  assert.ok(quality.telemetry.requests >= 1);
  assert.equal(quality.telemetry.token_efficiency.total_tokens, 150);

  const summary = {
    status: "passed",
    entry_id: entryId,
    state: delivered.state,
    manifest: manifestPath,
    evaluator_status: evaluation.status,
    evaluator_checks: Object.fromEntries(
      Object.entries(checks).map(([id, check]) => [id, check.status]),
    ),
    pipeline_quality: quality,
    browser_errors: browserErrors,
  };
  fs.writeFileSync(path.join(artifactDir, "lifecycle-summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
  fs.writeFileSync(path.join(artifactDir, "evaluation.json"), `${JSON.stringify(evaluation, null, 2)}\n`);
  fs.writeFileSync(path.join(artifactDir, "pipeline-quality.json"), `${JSON.stringify(quality, null, 2)}\n`);
  await page.screenshot({ path: path.join(artifactDir, "delivered.png"), fullPage: true });
  assert.deepEqual(browserErrors, []);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  await page.screenshot({ path: path.join(artifactDir, "failure.png"), fullPage: true }).catch(() => {});
  fs.writeFileSync(
    path.join(artifactDir, "failure.json"),
    `${JSON.stringify({ status: "failed", entry_id: entryId, error: String(error.stack || error), browser_errors: browserErrors }, null, 2)}\n`,
  );
  throw error;
} finally {
  await browser.close();
}
