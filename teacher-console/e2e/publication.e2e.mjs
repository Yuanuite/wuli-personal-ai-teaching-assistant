#!/usr/bin/env node

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  artifactDir,
  createSession,
  finishEntry,
  getJson,
  inspectDelivery,
  library,
  publicSite,
  recordFailure,
  runAnalysisAndApprove,
  uploadAndApproveSource,
  waitForDetail,
  writeJson,
} from "./support.mjs";

function sha256(file) {
  return crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function textFiles(root) {
  const files = [];
  for (const item of fs.readdirSync(root, { withFileTypes: true })) {
    const target = path.join(root, item.name);
    if (item.isDirectory()) files.push(...textFiles(target));
    else if (/\.(?:html|js|json|md|css|svg)$/i.test(item.name)) files.push(target);
  }
  return files;
}

const { browser, page, browserErrors } = await createSession();
let entryId = "";
try {
  entryId = await uploadAndApproveSource(page, {
    filename: "student-name-school-private-e2e.png",
    problem:
      "# 牛顿第二定律\n\n" +
      "质量为 $m$ 的物体在水平合力 $F$ 作用下运动，求物体加速度的大小和方向，并说明所用物理规律。",
    note: "E2E 已核对题干；公开前需遮挡图片顶部的学生姓名区域",
  });
  await runAnalysisAndApprove(page, entryId);
  await finishEntry(page, entryId);

  const detailBefore = await waitForDetail(
    entryId,
    detail => (detail.publication_images?.sources || []).length === 1,
    "publication image editor source",
  );
  const originalRelative = detailBefore.publication_images.sources[0].relative;
  const originalPath = path.join(library, "entries", entryId, originalRelative);
  const originalDigest = sha256(originalPath);

  await page.locator('[data-tab="delivery"]').click();
  await page.locator("#publication-image-editor:not(.hidden)").waitFor();
  await page.waitForFunction(() => {
    const canvas = document.querySelector("#publication-image-canvas");
    return canvas && canvas.width >= 100 && canvas.height >= 60;
  });
  const canvas = page.locator("#publication-image-canvas");
  const box = await canvas.boundingBox();
  assert.ok(box && box.width > 20 && box.height > 20, "publication canvas should be interactive");
  await page.mouse.move(box.x + box.width * 0.05, box.y + box.height * 0.05);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.22);
  await page.mouse.up();

  await page.locator("#publication-image-reviewer").fill("e2e-teacher");
  await page.locator("#publication-image-note").fill("已用不透明遮挡覆盖姓名和学校区域");
  await page.locator("#publication-image-confirmed").check();
  await page.locator("#save-publication-images").click();
  const imageReviewed = await waitForDetail(
    entryId,
    detail => detail.publication_images?.status === "passed",
    "approved public image copy",
  );
  assert.equal(imageReviewed.publication_images.included_count, 1);

  const entryDir = path.join(library, "entries", entryId);
  const publicImage = path.join(entryDir, "publication-assets", "question-1.webp");
  assert.ok(fs.existsSync(publicImage));
  assert.equal(sha256(originalPath), originalDigest, "privacy editing must not modify the original upload");
  const imageReview = JSON.parse(
    fs.readFileSync(path.join(entryDir, "publication-images.json"), "utf8"),
  );
  assert.equal(imageReview.status, "passed");
  assert.equal(imageReview.pages[0].redactions.length, 1);

  await page.locator("#prepare-publication").click();
  const prepared = await waitForDetail(
    entryId,
    detail => detail.publication?.preview_ready === true,
    "privacy-audited public preview",
    60_000,
  );
  assert.equal(prepared.publication.public_question_images, 1);
  await page.locator("#publication-preview-frame:not(.hidden)").waitFor();
  const preview = page.frameLocator("#publication-preview-frame");
  await preview.locator("#reader-title").filter({ hasText: "牛顿第二定律" }).waitFor();
  assert.match(await preview.locator("body").innerText(), /牛顿第二定律/);
  await page.screenshot({ path: path.join(artifactDir, "publication-preview.png"), fullPage: true });

  await page.locator("#publication-reviewer").fill("e2e-teacher");
  await page.locator("#publication-note").fill("已核对公开预览、题图遮挡和教师内部内容边界");
  await page.locator("#publication-privacy-confirmed").check();
  await page.locator("#publish-publication").click();
  const published = await waitForDetail(
    entryId,
    detail => detail.publication?.published_local === true,
    "local student-site publication",
    60_000,
  );
  assert.equal(published.publication.status, "published-local");
  assert.equal(published.publication.git_status, "not-pushed");
  await page.locator("#publication-local-link:not(.hidden)").waitFor();

  const publicId = published.publication.public_id;
  const publicQuestion = path.join(publicSite, "questions", publicId);
  assert.ok(fs.existsSync(path.join(publicQuestion, "content.md")));
  assert.ok(fs.existsSync(path.join(publicQuestion, "assets", "question-1.webp")));
  assert.ok(fs.existsSync(path.join(publicQuestion, "带答案错题.pdf")));
  const publicText = textFiles(publicSite)
    .map(file => fs.readFileSync(file, "utf8"))
    .join("\n")
    .toLowerCase();
  for (const forbidden of [
    entryId,
    "student-name-school-private-e2e.png",
    "teacher-solution",
    "教师版解析",
    "record.json",
    "delivery-manifest.json",
    library.toLowerCase(),
  ]) {
    assert.equal(publicText.includes(forbidden.toLowerCase()), false, `public tree leaked ${forbidden}`);
  }

  const catalog = await getJson("/api/public-site/catalog.json");
  assert.equal(catalog.questions.length, 1);
  assert.equal(catalog.questions[0].id, publicId);
  assert.equal(catalog.questions[0].simulation, null);
  const inspected = inspectDelivery(entryId);
  assert.equal(inspected.evaluation.status, "passed");
  assert.deepEqual(browserErrors, []);

  const summary = {
    status: "passed",
    entry_id: entryId,
    state: published.state,
    publication_status: published.publication.status,
    public_id: publicId,
    public_question_images: published.publication.public_question_images,
    public_pdf: catalog.questions[0].pdf,
    simulation: catalog.questions[0].simulation,
    git_status: published.publication.git_status,
    original_unchanged: sha256(originalPath) === originalDigest,
    browser_errors: browserErrors,
  };
  writeJson("publication-summary.json", summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  await recordFailure(page, entryId, browserErrors, error);
  throw error;
} finally {
  await browser.close();
}
