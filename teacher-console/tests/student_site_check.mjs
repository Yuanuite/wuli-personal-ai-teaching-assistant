#!/usr/bin/env node
import { createRequire } from "node:module";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const siteRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../student-site");
let baseUrl = process.argv[2] || "";
const screenshot = process.argv[3] || "/private/tmp/student-site-sort-desktop.png";
const mobileScreenshot = screenshot.replace(/(\.[^.]+)?$/, "-mobile$1");
let server;
if (!baseUrl) {
  server = http.createServer((request, response) => {
    const requested = decodeURIComponent(new URL(request.url || "/", "http://127.0.0.1").pathname);
    const relative = requested === "/" ? "index.html" : requested.replace(/^\/+/, "");
    const target = path.resolve(siteRoot, relative);
    if (target !== siteRoot && !target.startsWith(`${siteRoot}${path.sep}`)) {
      response.writeHead(403).end("Forbidden");
      return;
    }
    fs.readFile(target, (error, content) => {
      if (error) {
        response.writeHead(404).end("Not found");
        return;
      }
      const type = target.endsWith(".js")
        ? "text/javascript"
        : target.endsWith(".css")
          ? "text/css"
          : target.endsWith(".json")
            ? "application/json"
            : "text/html";
      response.writeHead(200, { "Content-Type": `${type}; charset=utf-8` }).end(content);
    });
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}/`;
}

const catalogFixture = {
  schema_version: 1,
  questions: [
    {
      id: "question-early-upload",
      title: "较早上传、较晚发布",
      subject: "高中物理",
      knowledge_points: ["早期题目"],
      content: "questions/question-early-upload/content.md",
      uploaded_at: "2026-07-19T09:00:00+08:00",
      published_at: "2026-07-24T09:00:00+08:00",
      difficulty: { score: 42, level: "中等", summary: "中等：需要图像迁移。", dimensions: [{label:"知识点深度",score:2,core_judgment:"理解应用。"},{label:"多知识点交叉",score:2,core_judgment:"两点联动。"},{label:"题型距离与表征转换",score:1,core_judgment:"常规母题。"},{label:"题目长度与结构",score:1,core_judgment:"结构直接。"},{label:"运算与规范表达",score:1,core_judgment:"计算简短。"},{label:"干扰信息与条件辨析",score:2,core_judgment:"需辨析条件。"}] },
    },
    {
      id: "question-late-upload",
      title: "较晚上传、较早发布",
      subject: "高中物理",
      knowledge_points: ["近期题目"],
      content: "questions/question-late-upload/content.md",
      uploaded_at: "2026-07-24T09:00:00+08:00",
      published_at: "2026-07-20T09:00:00+08:00",
      difficulty: { score: 73, level: "较难", summary: "较难：需要多过程建模。", dimensions: [{label:"知识点深度",score:3,core_judgment:"综合分析。"},{label:"多知识点交叉",score:3,core_judgment:"多点联动。"},{label:"题型距离与表征转换",score:3,core_judgment:"需要转换。"},{label:"题目长度与结构",score:2,core_judgment:"多问。"},{label:"运算与规范表达",score:2,core_judgment:"多步计算。"},{label:"干扰信息与条件辨析",score:2,core_judgment:"需辨析条件。"}] },
    },
    {
      id: "question-legacy",
      title: "旧目录兼容题",
      subject: "高中物理",
      knowledge_points: ["兼容回退"],
      content: "questions/question-legacy/content.md",
      published_at: "2026-07-18T09:00:00+08:00",
    },
  ],
};
const browser = await chromium.launch({ headless: true });
const errors = [];

async function inspectPage(page) {
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.route("**/catalog.json", route =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(catalogFixture) }),
  );
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator(".question-card").first().waitFor();
}

async function cardIds(page) {
  return page.locator(".question-card").evaluateAll(elements =>
    elements.map(element => new URL(element.href).searchParams.get("id")),
  );
}

const desktop = await browser.newPage({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 1 });
await inspectPage(desktop);
if (await desktop.locator("html").getAttribute("data-theme") !== "dark") await desktop.locator("#theme-toggle").click();
const darkTheme = await desktop.locator("html").getAttribute("data-theme");
const themeToggleLabel = await desktop.locator("#theme-toggle").getAttribute("aria-label");
await desktop.reload({ waitUntil: "networkidle" });
const persistedTheme = await desktop.locator("html").getAttribute("data-theme");
const sort = desktop.locator("#question-sort");
const defaultValue = await sort.inputValue();
const descIds = await cardIds(desktop);
const timeLabels = await desktop.locator(".question-card time").allTextContents();
const catalogBar = await desktop.evaluate(() => {
  const summary = document.querySelector("#catalog-summary")?.getBoundingClientRect();
  const sort = document.querySelector("#question-sort")?.getBoundingClientRect();
  return {
    sameRow: Boolean(summary && sort && Math.abs(summary.top - sort.top) < 8),
    sortWidth: Math.round(sort?.width || 0),
  };
});
await sort.selectOption("uploaded-asc");
const ascIds = await cardIds(desktop);
const ascSummary = await desktop.locator("#catalog-summary").textContent();
await sort.selectOption("difficulty-desc");
const difficultyIds = await cardIds(desktop);
const difficultyDot = await desktop.locator(".difficulty-dot").first().getAttribute("aria-label");
const difficultyDotVisual = {
  glyphs: await desktop.locator(".difficulty-dot").first().locator(".difficulty-glyph").count(),
  numericLabels: await desktop.locator(".difficulty-dot").first().locator(".difficulty-value").count(),
};
const difficultyDotPalette = await desktop.locator(".difficulty-dot").first().evaluate(dot => ({
  background: getComputedStyle(dot).backgroundColor,
  glyphStroke: getComputedStyle(dot.querySelector(".difficulty-glyph-frame")).stroke,
}));
await desktop.locator(".difficulty-dot").first().hover();
const difficultyPopoverVisible = await desktop.locator(".difficulty-popover").first().isVisible();
const difficultyPopoverTopLayer = await desktop.locator(".difficulty-popover").first().evaluate(panel => ({
  parent: panel.parentElement?.tagName,
  open: panel.matches(":popover-open") || panel.classList.contains("is-open"),
  position: getComputedStyle(panel).position,
}));
const radarLabelCount = await desktop.locator(".difficulty-popover").first().locator(".radar-label").count();
const popoverHasDimensionCopy = await desktop.locator(".difficulty-popover").first().locator(".difficulty-copy").count();
const popoverHasConclusion = await desktop.locator(".difficulty-popover").first().locator(".difficulty-conclusion").count();
await desktop.screenshot({ path: screenshot, fullPage: true });

const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
await inspectPage(mobile);
if (await mobile.locator("html").getAttribute("data-theme") !== "dark") await mobile.locator("#theme-toggle").click();
const mobileViewport = await mobile.evaluate(() => ({
  innerWidth: window.innerWidth,
  scrollWidth: document.documentElement.scrollWidth,
  sortVisible: Boolean(document.querySelector("#question-sort")?.getClientRects().length),
}));
await mobile.locator(".difficulty-dot").first().click();
const mobileDifficultyPopoverVisible = await mobile.locator(".difficulty-popover").first().isVisible();
await mobile.screenshot({ path: mobileScreenshot, fullPage: true });
await browser.close();
if (server) await new Promise(resolve => server.close(resolve));

const report = {
  status:
    errors.length ||
    defaultValue !== "uploaded-desc" ||
    JSON.stringify(descIds) !==
      JSON.stringify(["question-late-upload", "question-early-upload", "question-legacy"]) ||
    JSON.stringify(ascIds) !==
      JSON.stringify(["question-legacy", "question-early-upload", "question-late-upload"]) ||
    JSON.stringify(difficultyIds) !==
      JSON.stringify(["question-late-upload", "question-early-upload", "question-legacy"]) ||
    !difficultyDot?.includes("73/100") ||
    difficultyDotVisual.glyphs !== 1 ||
    difficultyDotVisual.numericLabels !== 0 ||
    difficultyDotPalette.background !== "rgb(251, 146, 60)" ||
    difficultyDotPalette.glyphStroke !== "rgb(255, 255, 255)" ||
    !difficultyPopoverVisible ||
    difficultyPopoverTopLayer.parent !== "BODY" ||
    !difficultyPopoverTopLayer.open ||
    difficultyPopoverTopLayer.position !== "fixed" ||
    darkTheme !== "dark" ||
    persistedTheme !== "dark" ||
    !themeToggleLabel?.includes("亮色") ||
    radarLabelCount !== 6 ||
    popoverHasDimensionCopy !== 0 ||
    popoverHasConclusion !== 1 ||
    timeLabels.length !== catalogFixture.questions.length ||
    !timeLabels.some(label => label.startsWith("发布 ")) ||
    ascSummary !== "共 3 道已复核题目" ||
    !catalogBar.sameRow ||
    catalogBar.sortWidth > 140 ||
    !mobileViewport.sortVisible ||
    !mobileDifficultyPopoverVisible ||
    mobileViewport.scrollWidth > mobileViewport.innerWidth + 1
      ? "failed"
      : "passed",
  defaultValue,
  descIds,
  ascIds,
  timeLabels,
  ascSummary,
  difficultyIds,
  difficultyDot,
  difficultyDotVisual,
  difficultyDotPalette,
  difficultyPopoverVisible,
  difficultyPopoverTopLayer,
  darkTheme,
  persistedTheme,
  themeToggleLabel,
  radarLabelCount,
  popoverHasDimensionCopy,
  popoverHasConclusion,
  catalogBar,
  mobileViewport,
  mobileDifficultyPopoverVisible,
  errors,
  screenshot,
  mobileScreenshot,
};

console.log(JSON.stringify(report, null, 2));
if (report.status !== "passed") process.exitCode = 1;
