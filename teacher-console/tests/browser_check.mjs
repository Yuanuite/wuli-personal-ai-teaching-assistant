#!/usr/bin/env node
import { createRequire } from "node:module";
import process from "node:process";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const baseUrl = process.argv[2] || "http://127.0.0.1:8787/";
const screenshot = process.argv[3] || "/private/tmp/teacher-console-browser-check.png";
const visualizationScreenshot = screenshot.replace(/(\.[^.]+)?$/, "-visualization$1");
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

const dynamicEntry = page.locator(".entry-card", { hasText: "带电粒子在同心圆复合场中的运动" });
await dynamicEntry.click();
await page.waitForFunction(() => document.querySelector("#entry-title")?.textContent?.includes("带电粒子在同心圆复合场中的运动"));
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
const visualizationTabHidden = await page.locator('[data-tab="visualization"]').evaluate(element => element.classList.contains("hidden"));
const staticGalleryElements = await page.locator("#visualization-static-gallery").count();
await page.locator('[data-tab="delivery"]').click();
const prerequisiteToast = await page.locator("#toast").textContent();
const activeTabAfterBlockedClick = await page.locator(".tab.active").getAttribute("data-tab");
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
  status: errors.length || folders < 2 || entryCards < 2 || sandbox !== "allow-scripts" || simulator.canvas !== 1 || visualizationTabHidden || staticGalleryElements || internalDownloads.length || !prerequisiteToast?.includes("解析复核") || activeTabAfterBlockedClick !== "answer" || !shortToastHidden || !optionalVisualizationVisible || !optionalVisualizationTitle?.includes("尚未生成") || !generationButtonText?.includes("调用 Skill") || !scrollLocked || viewport.scrollWidth > viewport.innerWidth + 1 || viewport.shellBottom > viewport.innerHeight + 1 ? "failed" : "passed",
  folders,
  entryCards,
  sandbox,
  simulator,
  visualizationTabHidden,
  staticGalleryElements,
  prerequisiteToast,
  activeTabAfterBlockedClick,
  shortToastHidden,
  optionalVisualizationVisible,
  optionalVisualizationTitle,
  generationButtonText,
  downloads,
  internalDownloads,
  viewport,
  scrollLocked,
  errors,
  screenshot,
  visualizationScreenshot,
};
console.log(JSON.stringify(report, null, 2));
if (report.status !== "passed") process.exitCode = 1;
