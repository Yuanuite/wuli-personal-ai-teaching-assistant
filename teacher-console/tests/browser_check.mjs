#!/usr/bin/env node
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseUrl = process.argv[2] || "http://127.0.0.1:8787/";
const screenshot = process.argv[3] || "/private/tmp/teacher-console-browser-check.png";
const visualizationScreenshot = screenshot.replace(/(\.[^.]+)?$/, "-visualization$1");
const difficultyScreenshot = screenshot.replace(/(\.[^.]+)?$/, "-difficulty$1");
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1 });
const errors = [];
page.on("pageerror", error => errors.push(error.message));
page.on("console", message => {
  if (message.type() === "error") errors.push(message.text());
});

await page.goto(baseUrl, { waitUntil: "networkidle" });
await page.locator(".folder-group").first().waitFor();
const folders = await page.locator(".folder-group").count();
const entryCards = await page.locator(".entry-card").count();
const agentHealthDetail = await page.locator("#agent-health-detail").textContent();

const dynamicEntry = page.locator(".entry-card", { hasText: "带电粒子在同心圆复合场中的运动" });
await dynamicEntry.click();
await page.waitForFunction(() => document.querySelector("#entry-title")?.textContent?.includes("带电粒子在同心圆复合场中的运动"));
await page.locator('[data-tab="answer"]').click();
const difficultyToggle = page.locator("#difficulty-assessment-toggle");
const difficultyPlacement = await page.evaluate(() => {
  const teacher = document.querySelector('[data-solution="teacher"]')?.getBoundingClientRect();
  const difficulty = document.querySelector("#difficulty-assessment-toggle")?.getBoundingClientRect();
  return {
    visible: Boolean(difficulty?.width && difficulty?.height),
    besideTeacher: Boolean(teacher && difficulty && Math.abs(teacher.top - difficulty.top) < 4 && difficulty.left >= teacher.right),
  };
});
await difficultyToggle.click();
const difficultyPanelVisible = await page.locator("#difficulty-assessment").isVisible();
const difficultyExpanded = await difficultyToggle.getAttribute("aria-expanded");
const difficultyDimensionCount = await page.locator(".difficulty-dimension").count();
await page.screenshot({ path: difficultyScreenshot, fullPage: false });
await page.locator('[data-tab="visualization"]').click();
const frameElement = page.locator("#visualization-frame");
await frameElement.waitFor({ state: "visible" });
await page.waitForFunction(() => document.querySelector("#visualization-frame")?.getAttribute("src")?.includes("/api/visualization/"));
const sandbox = await frameElement.getAttribute("sandbox");
await page.waitForTimeout(500);
const frameHandle = await frameElement.elementHandle();
const frame = await frameHandle.contentFrame();
if (!frame) throw new Error("visualization iframe did not load");
await frame.locator("canvas").waitFor();
const simulator = {
  title: await frame.title(),
  canvas: await frame.locator("canvas").count(),
  buttons: await frame.locator("button").count(),
  ranges: await frame.locator('input[type="range"]').count(),
};
await page.screenshot({ path: visualizationScreenshot, fullPage: false });

const staticEntry = page.locator(".entry-card", { hasText: "航拍直升机电池容量与能量转化" });
await staticEntry.click();
await page.waitForFunction(() => document.querySelector("#entry-title")?.textContent?.includes("航拍直升机电池容量与能量转化"));
const staticState = await page.locator("#state-badge").textContent();
const visualizationTabHidden = await page.locator('[data-tab="visualization"]').evaluate(element => element.classList.contains("hidden"));
const staticGalleryElements = await page.locator("#visualization-static-gallery").count();
await page.locator('[data-tab="delivery"]').click();
const prerequisiteToast = await page.locator("#toast").textContent();
const activeTabAfterBlockedClick = await page.locator(".tab.active").getAttribute("data-tab");
const deliveryWasReady = ["可生成交付", "已交付"].includes(String(staticState || "").trim());
const deliveryNavigationOk = deliveryWasReady
  ? activeTabAfterBlockedClick === "delivery"
  : prerequisiteToast?.includes("复核") && activeTabAfterBlockedClick !== "delivery";
await page.waitForTimeout(1800);
const shortToastHidden = await page.locator("#toast").evaluate(element => element.classList.contains("hidden"));
const downloads = await page.locator(".download-card strong").allTextContents();
const internalDownloads = downloads.filter(name => /json|screenshot|manifest/i.test(name));

const optionalEntry = page.locator(".entry-card", { hasText: "正方形线框匀速穿越宽磁场" });
await optionalEntry.click();
await page.waitForFunction(() => document.querySelector("#entry-title")?.textContent?.includes("正方形线框匀速穿越宽磁场"));
await page.locator('[data-tab="visualization"]').click();
const optionalVisualizationVisible = await page.locator("#tab-visualization").isVisible();
const optionalVisualizationTitle = await page.locator("#visualization-empty strong").textContent();
const generationButtonText = await page.locator("#build-visualization").textContent();

const w3Entry = page.locator(".entry-card", { hasText: "方波电场与匀强磁场中的带电粒子运动" });
await w3Entry.click();
await page.waitForFunction(() => document.querySelector("#entry-title")?.textContent?.includes("方波电场与匀强磁场中的带电粒子运动"));
await page.locator('[data-tab="answer"]').click();
const w3ReviewFocusVisible = await page.locator("#w3-review-focus").isVisible();
const w3ReviewFocusCount = await page.locator(".w3-focus-card").count();

const w3FlaggedEntry = page.locator(".entry-card", { hasText: "三维复合场中电子的类平抛、螺旋运动与共圆心条件" });
await w3FlaggedEntry.click();
await page.waitForFunction(() => document.querySelector("#entry-title")?.textContent?.includes("三维复合场中电子的类平抛、螺旋运动与共圆心条件"));
await page.locator('[data-tab="answer"]').click();
const w3FlaggedFocusVisible = await page.locator("#w3-review-focus").isVisible();
const w3FlaggedFocusCount = await page.locator(".w3-focus-card").count();
const w3FlaggedFocusText = await page.locator("#w3-review-focus").textContent();
const w3FlaggedFocusCollapsed = !(await page.locator("#w3-review-focus").getAttribute("open"));
await page.locator("#w3-review-focus summary").click();
const w3FlaggedFocusExpanded = await page.locator("#w3-review-focus").getAttribute("open") !== null;
const w3FlaggedVisibleCardCount = await page.locator(".w3-focus-card:visible").count();
await page.locator(".w3-focus-card summary").first().click();
const w3AuditBlockCount = await page.locator(".w3-focus-card").first().locator(".w3-audit-block").count();
const w3AuditCopyCount = await page.locator(".w3-focus-card").first().locator(".w3-audit-copy").count();
const w3AuditUse = page.locator(".w3-focus-card").first().locator(".w3-audit-use").first();
const w3AuditUseVisible = await w3AuditUse.isVisible();
if (w3AuditUseVisible) await w3AuditUse.click();
const w3RevisionFilled = (await page.locator("#answer-note").inputValue()).includes("请据此核对并修改解析");

const viewport = await page.evaluate(() => ({
  innerHeight: window.innerHeight,
  scrollHeight: document.documentElement.scrollHeight,
  innerWidth: window.innerWidth,
  scrollWidth: document.documentElement.scrollWidth,
  bodyOverflow: getComputedStyle(document.body).overflow,
  shellBottom: Math.round(document.querySelector(".shell")?.getBoundingClientRect().bottom || 0),
}));
await page.evaluate(() => window.scrollTo(0, 0));
await page.mouse.wheel(0, 500);
await page.waitForTimeout(50);
const scrollLocked = await page.evaluate(() => window.scrollY === 0);
await page.screenshot({ path: screenshot, fullPage: false });
await browser.close();

const report = {
  status: errors.length || folders < 2 || entryCards < 2 || !agentHealthDetail?.includes("Agent") || !difficultyPlacement.visible || !difficultyPlacement.besideTeacher || !difficultyPanelVisible || difficultyExpanded !== "true" || difficultyDimensionCount !== 6 || sandbox !== "allow-scripts" || simulator.canvas !== 1 || visualizationTabHidden || staticGalleryElements || internalDownloads.length || !deliveryNavigationOk || !shortToastHidden || !optionalVisualizationVisible || !optionalVisualizationTitle?.includes("尚未生成") || !generationButtonText?.includes("调用 Skill") || w3ReviewFocusVisible || w3ReviewFocusCount > 2 || !w3FlaggedFocusVisible || w3FlaggedFocusCount !== 2 || !w3FlaggedFocusCollapsed || !w3FlaggedFocusExpanded || w3FlaggedVisibleCardCount !== 2 || w3AuditBlockCount < 2 || w3AuditCopyCount < 2 || !w3AuditUseVisible || !w3RevisionFilled || /unresolved|solver|verifier|adjudicator/i.test(w3FlaggedFocusText || "") || !scrollLocked || viewport.scrollWidth > viewport.innerWidth + 1 || viewport.shellBottom > viewport.innerHeight + 1 ? "failed" : "passed",
  folders,
  entryCards,
  agentHealthDetail,
  difficultyPlacement,
  difficultyPanelVisible,
  difficultyExpanded,
  difficultyDimensionCount,
  sandbox,
  simulator,
  visualizationTabHidden,
  staticGalleryElements,
  staticState,
  deliveryWasReady,
  deliveryNavigationOk,
  prerequisiteToast,
  activeTabAfterBlockedClick,
  shortToastHidden,
  optionalVisualizationVisible,
  optionalVisualizationTitle,
  generationButtonText,
  w3ReviewFocusVisible,
  w3ReviewFocusCount,
  w3FlaggedFocusVisible,
  w3FlaggedFocusCount,
  w3FlaggedFocusCollapsed,
  w3FlaggedFocusExpanded,
  w3FlaggedVisibleCardCount,
  w3AuditBlockCount,
  w3AuditCopyCount,
  w3AuditUseVisible,
  w3RevisionFilled,
  downloads,
  internalDownloads,
  viewport,
  scrollLocked,
  errors,
  screenshot,
  visualizationScreenshot,
  difficultyScreenshot,
};
console.log(JSON.stringify(report, null, 2));
if (report.status !== "passed") process.exitCode = 1;
