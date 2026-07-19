#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');

const input = process.argv[2];
if (!input) throw new Error('usage: test_guided_simulator.mjs <html> [screenshot]');
const screenshot = process.argv[3];
const mobileScreenshot = process.argv[4];
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 820 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.goto(pathToFileURL(path.resolve(input)).href);
await page.locator('[data-case-id="case-b-over-12"]').click();
await page.locator('[data-layer="force"]').click();
await page.locator('#next-event-guided').click();
const firstEvent = await page.locator('#event-guided').textContent();
const result = await page.locator('#result-guided').textContent();
await page.locator('#progress-guided').evaluate(element => {
  element.value = '1000';
  element.dispatchEvent(new Event('input', { bubbles: true }));
});
const stopEvent = await page.locator('#event-guided').textContent();
const activeLayer = await page.locator('[data-layer="force"]').evaluate(element => element.classList.contains('active'));
if (!result.includes('B/12') || !result.includes('第二次进入 III 区之前')) errors.push(`wrong B/12 timing: ${result}`);
if (!firstEvent || firstEvent.includes('从 P 出发')) errors.push(`next-event did not advance: ${firstEvent}`);
if (!stopEvent.includes('第三次进入 III 区')) errors.push(`wrong stop event: ${stopEvent}`);
if (!activeLayer) errors.push('force layer did not activate');
if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });
await page.setViewportSize({ width: 390, height: 844 });
await page.reload();
await page.waitForTimeout(150);
const layout = await page.evaluate(() => {
  const rect = selector => {
    const value = document.querySelector(selector)?.getBoundingClientRect();
    return value ? { top: value.top, bottom: value.bottom, height: value.height } : null;
  };
  return {
    canvas: rect('#canvas-guided'),
    play: rect('#play-guided'),
    progress: rect('#progress-guided'),
    detailsOpen: document.querySelector('.more-controls')?.open,
    scrollY: window.scrollY,
    viewport: window.innerHeight,
    documentHeight: document.documentElement.scrollHeight,
  };
});
if (!layout.canvas || layout.canvas.bottom > layout.viewport) errors.push(`mobile canvas is not initially visible: ${JSON.stringify(layout.canvas)}`);
if (!layout.play || layout.play.bottom > layout.viewport) errors.push(`mobile play control is not initially visible: ${JSON.stringify(layout.play)}`);
if (!layout.progress || layout.progress.bottom > layout.viewport) errors.push(`mobile scrubber is not initially visible: ${JSON.stringify(layout.progress)}`);
if (layout.detailsOpen) errors.push('mobile secondary controls should start collapsed');
if (mobileScreenshot) await page.screenshot({ path: mobileScreenshot, fullPage: false });
console.log(JSON.stringify({ errors, firstEvent, stopEvent, result, activeLayer, screenshot, mobileScreenshot, layout }, null, 2));
await browser.close();
if (errors.length) process.exit(1);
