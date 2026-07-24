import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

export const baseUrl = process.env.E2E_BASE_URL;
export const library = process.env.E2E_LIBRARY;
export const publicSite = process.env.E2E_PUBLIC_SITE;
export const projectRoot = process.env.E2E_PROJECT_ROOT;
export const python = process.env.E2E_PYTHON || "python3";
export const artifactDir =
  process.env.E2E_ARTIFACT_DIR || path.join(projectRoot, "test-results", "e2e");

for (const [name, value] of Object.entries({ baseUrl, library, publicSite, projectRoot })) {
  if (!value) throw new Error(`missing required E2E environment: ${name}`);
}
fs.mkdirSync(artifactDir, { recursive: true });

export const png = process.env.E2E_FIXTURE_IMAGE
  ? fs.readFileSync(process.env.E2E_FIXTURE_IMAGE)
  : Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64",
    );

export function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

export async function getJson(relative) {
  const response = await fetch(new URL(relative, baseUrl));
  assert.equal(response.status, 200, `GET ${relative} returned ${response.status}`);
  return response.json();
}

export async function waitForState(entryId, expected, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  let detail;
  while (Date.now() < deadline) {
    detail = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
    if (detail.state === expected) return detail;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`entry ${entryId} did not reach ${expected}; last state=${detail?.state}`);
}

export async function waitForDetail(entryId, predicate, description, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  let detail;
  while (Date.now() < deadline) {
    detail = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
    if (predicate(detail)) return detail;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`entry ${entryId} did not reach ${description}; last state=${detail?.state}`);
}

export async function createSession() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const browserErrors = [];
  page.on("pageerror", error => browserErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await page.route("**/api/run-upload", async route => {
    const request = route.request();
    const body = JSON.parse(request.postData() || "{}");
    await route.continue({
      postData: JSON.stringify({ ...body, ocr: "none" }),
      headers: { ...request.headers(), "content-type": "application/json" },
    });
  });
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  return { browser, page, browserErrors };
}

export async function uploadAndApproveSource(page, { filename, problem, note }) {
  await page.locator(".upload-card > summary").click();
  await page.locator("#file-input").setInputFiles({
    name: filename,
    mimeType: "image/png",
    buffer: png,
  });
  await page.locator("#upload-run").click();
  await page.locator("#entry-view:not(.hidden)").waitFor();
  await page.waitForFunction(() => document.querySelector("#entry-id")?.textContent?.trim());
  const entryId = String(await page.locator("#entry-id").textContent()).trim();
  assert.ok(entryId, "uploaded entry id should be visible");
  await waitForState(entryId, "needs-source-review");

  await page.locator("#problem-editor").fill(problem);
  await page.locator("#source-note").fill(note);
  await page.locator("#approve-source").click();
  await waitForState(entryId, "needs-analysis-and-answer");
  return entryId;
}

export async function runAnalysisAndApprove(page, entryId) {
  await page.locator("#run-analysis").click();
  await waitForState(entryId, "needs-answer-review");
  await page.locator("#approve-answer").click();
  return waitForState(entryId, "ready-to-finish");
}

export async function finishEntry(page, entryId) {
  await page.locator('[data-tab="delivery"]').click();
  await page.locator("#finish-entry").click();
  const delivered = await waitForState(entryId, "delivered");
  await page.locator("#download-list .download-card").first().waitFor();
  return delivered;
}

export function inspectDelivery(entryId) {
  const entryDir = path.join(library, "entries", entryId);
  const delivery = readJson(path.join(entryDir, "delivery.json"));
  const manifestPath = path.join(delivery.output, "delivery-manifest.json");
  const manifest = readJson(manifestPath);
  const evaluation = readJson(path.join(entryDir, "evaluation.json"));
  const checks = Object.fromEntries(evaluation.checks.map(check => [check.id, check]));
  return { entryDir, delivery, manifestPath, manifest, evaluation, checks };
}

export function runPipelineQuality(entryId) {
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
  return JSON.parse(qualityProcess.stdout.trim().split("\n").at(-1));
}

export function writeJson(name, value) {
  fs.writeFileSync(path.join(artifactDir, name), `${JSON.stringify(value, null, 2)}\n`);
}

export async function recordFailure(page, entryId, browserErrors, error) {
  await page
    .screenshot({ path: path.join(artifactDir, "failure.png"), fullPage: true })
    .catch(() => {});
  writeJson("failure.json", {
    status: "failed",
    entry_id: entryId,
    error: String(error.stack || error),
    browser_errors: browserErrors,
  });
}
