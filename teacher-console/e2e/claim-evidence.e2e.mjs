#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import {
  artifactDir,
  baseUrl,
  createSession,
  getJson,
  library,
  recordFailure,
  uploadAndApproveSource,
  writeJson,
} from "./support.mjs";

async function postJson(relative, body, expectedStatus = 202) {
  const response = await fetch(new URL(relative, baseUrl), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Teacher-Console": "1",
    },
    body: JSON.stringify(body),
  });
  assert.equal(
    response.status,
    expectedStatus,
    `POST ${relative} returned ${response.status}`,
  );
  return response.json();
}

async function waitForJob(url, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  let job;
  while (Date.now() < deadline) {
    job = await getJson(url);
    if (["completed", "failed"].includes(job?.status)) return job;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`job ${url} did not finish; last status=${job?.status}`);
}

const scenarios = [
  { name: "normal", marker: "[claim-normal]", aggregation: "VERIFIED", fuse: false },
  { name: "conflict", marker: "[claim-conflict]", aggregation: "UNRESOLVED", fuse: true },
  { name: "insufficient", marker: "[claim-insufficient]", aggregation: "PROVISIONAL", fuse: true },
  { name: "fuse", marker: "[claim-fuse]", aggregation: "PROVISIONAL", fuse: true },
];

const { browser, page, browserErrors } = await createSession();
const results = [];
let entryId = "";
try {
  const problemFor = scenario => (
    "# 多阶段首次事件测试\n\n" +
    "粒子先经过边界，再返回原区域；求全部可能结果，并核对第一次进入和首次返回事件。" +
    ` ${scenario.marker}`
  );
  entryId = await uploadAndApproveSource(page, {
    filename: "claim-evidence-e2e.png",
    problem: problemFor(scenarios[0]),
    note: "E2E normal 已核对题干与边界语义",
  });
  for (const [index, scenario] of scenarios.entries()) {
    if (index > 0) {
      await postJson(
        `/api/entries/${encodeURIComponent(entryId)}/approve-source`,
        {
          problem: problemFor(scenario),
          reviewer: "e2e",
          note: `E2E ${scenario.name} 已核对题干与边界语义`,
        },
        200,
      );
    }
    const entryDir = path.join(library, "entries", entryId);
    const canonicalPath = path.join(entryDir, "student-solution.md");
    const canonical = `# canonical sentinel ${scenario.name}\n`;
    fs.writeFileSync(canonicalPath, canonical);

    const queued = await postJson(
      `/api/entries/${encodeURIComponent(entryId)}/analyze-w3-shadow`,
      { routing_tier: "economy" },
    );
    const job = await waitForJob(queued.job.url);
    assert.equal(job.status, "completed");

    const raw = JSON.parse(
      fs.readFileSync(path.join(entryDir, "w3-shadow-report.json"), "utf8"),
    );
    assert.equal(raw.status, "completed");
    assert.equal(raw.canonical_answer_changed, false);
    assert.equal(fs.readFileSync(canonicalPath, "utf8"), canonical);
    const evidence = raw.report.claim_evidence_shadow;
    assert.equal(evidence.status, "completed");
    assert.equal(evidence.aggregation.status, scenario.aggregation);
    assert.equal(evidence.metrics.fuse_triggered, scenario.fuse);
    assert.equal(evidence.metrics.repeated_task_count, 0);
    if (scenario.fuse) {
      assert.match(evidence.loop.transition.action, /fuse$/);
      assert.notEqual(evidence.loop.transition.control.terminal_status, "VERIFIED");
    }

    const detail = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
    const teacherView = detail.w3_shadow.claim_evidence;
    assert.equal(teacherView.final_answers.length, 1);
    assert.equal(teacherView.claims.length, evidence.metrics.claim_count);
    assert.equal(teacherView.certificates.length, evidence.metrics.certificate_count);
    if (scenario.name !== "normal") {
      assert.ok(teacherView.unresolved_obligations.length > 0);
    }
    results.push({
      name: scenario.name,
      entry_id: entryId,
      aggregation_status: evidence.aggregation.status,
      challenge_count: evidence.metrics.challenge_count,
      unresolved_count: teacherView.unresolved_obligations.length,
      claim_count: evidence.metrics.claim_count,
      certificate_count: evidence.metrics.certificate_count,
      verified_claim_count: evidence.metrics.verified_claim_count,
      critical_certificate_coverage: evidence.metrics.critical_certificate_coverage,
      unresolved_claim_count: evidence.metrics.unresolved_claim_count,
      repeated_task_count: evidence.metrics.repeated_task_count,
      loop_transition_count: evidence.metrics.loop_transition_count,
      fuse_triggered: evidence.metrics.fuse_triggered,
      canonical_unchanged: true,
    });

    if (scenario.name === "conflict") {
      await page.reload({ waitUntil: "networkidle" });
      await page.locator(".entry-card").first().click();
      await page.waitForFunction(
        expected => document.querySelector("#entry-id")?.textContent?.trim() === expected,
        entryId,
      );
      await page.locator('[data-tab="answer"]').click();
      const ledger = page.locator("#claim-evidence-ledger");
      await assert.doesNotReject(() => ledger.waitFor({ state: "visible" }));
      assert.equal(await ledger.getAttribute("open"), null);
      await ledger.locator(":scope > summary").click();
      assert.equal(
        await page.locator("#claim-final-answers .claim-final-answer").count(),
        teacherView.final_answers.length,
      );
      assert.equal(
        await page.locator("#claim-unresolved-obligations .claim-unresolved-item").count(),
        teacherView.unresolved_obligations.length,
      );
      assert.equal(
        await page.locator("#claim-ledger-items .claim-ledger-item").count(),
        teacherView.claims.length,
      );
      assert.ok(await page.locator(".w3-focus-card").count() <= 2);
      await page.screenshot({
        path: path.join(artifactDir, "claim-evidence-conflict.png"),
        fullPage: true,
      });
    }
  }

  assert.deepEqual(browserErrors, []);
  const summary = { status: "passed", scenarios: results, browser_errors: browserErrors };
  writeJson("claim-evidence-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  await recordFailure(page, entryId, browserErrors, error);
  throw error;
} finally {
  await browser.close();
}
