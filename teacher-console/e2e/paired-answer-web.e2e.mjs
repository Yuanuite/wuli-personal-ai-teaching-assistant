import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseUrl = process.env.E2E_BASE_URL;
const entryId = process.env.E2E_ENTRY_ID;
const artifactDir = process.env.E2E_ARTIFACT_DIR;
const artifactPrefix = process.env.E2E_ARTIFACT_PREFIX || "web-current";
const timeoutMs = Number(process.env.E2E_TIMEOUT_MS || 1_800_000);
const routingTier = process.env.E2E_ROUTING_TIER || "auto";
const modelId = process.env.E2E_MODEL_ID || "auto";
for (const [name, value] of Object.entries({ baseUrl, entryId, artifactDir })) {
  if (!value) throw new Error(`missing required environment: ${name}`);
}
fs.mkdirSync(artifactDir, { recursive: true });

async function getJson(relative) {
  const response = await fetch(new URL(relative, baseUrl));
  assert.equal(response.status, 200, `GET ${relative} returned ${response.status}`);
  return response.json();
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const browserErrors = [];
page.on("pageerror", error => browserErrors.push(error.message));
page.on("console", message => {
  if (message.type() === "error") browserErrors.push(message.text());
});

try {
  await page.route(
    `**/api/entries/${encodeURIComponent(entryId)}/analyze`,
    async route => {
      const request = route.request();
      const body = request.postDataJSON() || {};
      await route.continue({
        postData: JSON.stringify({
          ...body,
          routing_tier: routingTier,
          model_id: modelId,
        }),
        headers: {
          ...request.headers(),
          "content-type": "application/json",
        },
      });
    },
  );
  await page.goto(baseUrl, { waitUntil: "networkidle", timeout: 60_000 });
  await page.locator(".entry-card").first().waitFor({ timeout: 30_000 });
  await page.locator(".entry-card").first().click();
  await page.waitForFunction(
    expected => document.querySelector("#entry-id")?.textContent?.trim() === expected,
    entryId,
  );
  await page.locator('[data-tab="answer"]').click();
  await page.locator("#run-analysis").waitFor({ state: "visible" });
  await page.locator("#run-analysis").click();

  const deadline = Date.now() + timeoutMs;
  let job;
  while (Date.now() < deadline) {
    const snapshot = await getJson(`/api/jobs?entry_id=${encodeURIComponent(entryId)}`);
    job = snapshot.job;
    if (job && ["completed", "failed"].includes(job.status)) break;
    await new Promise(resolve => setTimeout(resolve, 750));
  }
  assert.ok(job, "browser click did not create an analysis job");
  assert.equal(job.status, "completed", JSON.stringify(job.result || job, null, 2));
  const detail = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
  assert.equal(detail.state, "needs-answer-review");
  await page.screenshot({
    path: path.join(artifactDir, `${artifactPrefix}-completed.png`),
    fullPage: true,
  });
  fs.writeFileSync(
    path.join(artifactDir, `${artifactPrefix}-browser.json`),
    `${JSON.stringify({
      status: "completed",
      entry_id: entryId,
      action: "clicked #run-analysis",
      routing_tier: routingTier,
      model_id: modelId,
      job_id: job.id,
      job_kind: job.kind,
      browser_errors: browserErrors,
    }, null, 2)}\n`,
  );
} catch (error) {
  await page.screenshot({
    path: path.join(artifactDir, `${artifactPrefix}-failure.png`),
    fullPage: true,
  }).catch(() => {});
  fs.writeFileSync(
    path.join(artifactDir, `${artifactPrefix}-browser.json`),
    `${JSON.stringify({
      status: "failed",
      entry_id: entryId,
      error: String(error.stack || error),
      browser_errors: browserErrors,
    }, null, 2)}\n`,
  );
  throw error;
} finally {
  await browser.close();
}
