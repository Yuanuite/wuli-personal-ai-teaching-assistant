#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import {
  artifactDir,
  createSession,
  finishEntry,
  inspectDelivery,
  library,
  readJson,
  recordFailure,
  runAnalysisAndApprove,
  runPipelineQuality,
  uploadAndApproveSource,
  waitForDetail,
  waitForState,
  writeJson,
} from "./support.mjs";

const { browser, page, browserErrors } = await createSession();
let entryId = "";
try {
  entryId = await uploadAndApproveSource(page, {
    filename: "charged-particle-visualization-e2e.png",
    problem:
      "# 带电粒子在电场和磁场中的运动\n\n" +
      "正粒子从 P 点以速度 $v_0$ 进入竖直向下的匀强电场，在 Q 点进入垂直纸面向外的有界匀强磁场。" +
      "已知磁场下边界与 Q 点距离为 $d$，求轨迹恰与下边界相切时的临界磁感应强度，并展示完整运动过程。",
    note: "E2E 已核对电场、磁场方向与临界相切条件",
  });
  await runAnalysisAndApprove(page, entryId);

  await page.locator('[data-tab="visualization"]').click();
  await page.locator("#visualization-message").fill(
    "请生成可交互可视化，展示电场段、进入 Q 点、磁场圆周运动和首次到达下边界。",
  );
  await page.locator("#send-visualization-message").click();
  await waitForState(entryId, "needs-answer-review", 60_000);
  const built = await waitForDetail(
    entryId,
    detail => detail.visualization?.build_current && Boolean(detail.visualization?.preview_url),
    "a current visualization preview",
    60_000,
  );
  assert.equal(built.visualization.has_model, true);

  await page.locator('[data-tab="answer"]').click();
  await page.locator("#approve-answer").click();
  await waitForState(entryId, "needs-visualization-review");

  await page.locator('[data-tab="visualization"]').click();
  await page.locator("#visualization-frame:not(.hidden)").waitFor();
  const simulator = page.frameLocator("#visualization-frame");
  await simulator.locator("#field-trajectory-sim").waitFor();
  await simulator.locator("#play-field").click();
  await simulator.locator("#next-event-field").click();
  await simulator.locator('[data-layer="force"]').click();
  await simulator.locator("#b-ratio-field").fill("0.9");
  await page.screenshot({ path: path.join(artifactDir, "visualization-review.png"), fullPage: true });

  await page.locator("#visualization-note").fill("E2E 已核对轨迹、场方向、事件跳转和参数控件");
  await page.locator("#approve-visualization").click();
  await waitForState(entryId, "ready-to-finish");
  const delivered = await finishEntry(page, entryId);

  const inspected = inspectDelivery(entryId);
  assert.equal(inspected.manifest.status, "delivered");
  assert.equal(delivered.state, "delivered");
  assert.ok(
    inspected.manifest.files.some(name => name.endsWith("simulation/physics-simulator.html")),
    "delivery manifest should include the reviewed simulator HTML",
  );
  assert.ok(
    inspected.manifest.files.some(name => name.endsWith("simulation/physics-simulator.zip")),
    "delivery manifest should include the reviewed simulator ZIP",
  );
  assert.equal(inspected.checks.interactive_visualization.status, "passed");

  const review = readJson(path.join(library, "entries", entryId, "visualization-review.json"));
  const build = readJson(
    path.join(library, "entries", entryId, "visualization", "simulation-build.json"),
  );
  assert.equal(review.status, "passed");
  assert.equal(build.status, "ok");
  assert.equal(review.artifact_digest, built.visualization.artifact_digest);

  const quality = runPipelineQuality(entryId);
  assert.equal(quality.dimensions.pipeline_accuracy.score, 100);
  assert.ok(quality.telemetry.requests >= 2);
  assert.equal(quality.telemetry.token_efficiency.total_tokens, 420);
  assert.deepEqual(browserErrors, []);

  const summary = {
    status: "passed",
    entry_id: entryId,
    state: delivered.state,
    manifest: inspected.manifestPath,
    visualization_build: build.status,
    visualization_review: review.status,
    runtime_check: build.runtime_check?.status,
    evaluator_status: inspected.evaluation.status,
    interactive_visualization: inspected.checks.interactive_visualization.status,
    pipeline_quality: quality,
    browser_errors: browserErrors,
  };
  writeJson("visualization-summary.json", summary);
  fs.copyFileSync(
    path.join(library, "entries", entryId, "evaluation.json"),
    path.join(artifactDir, "evaluation.json"),
  );
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  await recordFailure(page, entryId, browserErrors, error);
  throw error;
} finally {
  await browser.close();
}
