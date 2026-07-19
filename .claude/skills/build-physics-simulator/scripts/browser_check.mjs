#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';

const args = process.argv.slice(2);
if (!args.length) {
  console.error('usage: node browser_check.mjs <html> [--screenshot <png>]');
  process.exit(2);
}
const htmlPath = path.resolve(args[0]);
const screenshotIndex = args.indexOf('--screenshot');
const screenshot = screenshotIndex >= 0 ? path.resolve(args[screenshotIndex + 1]) : null;

let chromium;
try {
  const require = createRequire(import.meta.url);
  ({ chromium } = require('playwright'));
} catch (error) {
  console.log(JSON.stringify({ status: 'skipped', reason: 'Playwright is not available.', errors: [] }, null, 2));
  process.exit(2);
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (error) {
  console.log(JSON.stringify({ status: 'skipped', reason: `Chromium could not start: ${error.message}`, errors: [] }, null, 2));
  process.exit(2);
}
const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') errors.push(message.text());
});
await page.goto(pathToFileURL(htmlPath).href);
await page.waitForTimeout(500);

const exercised = { buttons: 0, ranges: 0 };
for (const button of await page.locator('button:visible:enabled').all()) {
  try {
    await button.click({ timeout: 1000 });
    await page.waitForTimeout(30);
    exercised.buttons += 1;
  } catch (error) {
    errors.push(`button interaction failed: ${error.message}`);
  }
}
for (const range of await page.locator('input[type="range"]:visible:enabled').all()) {
  try {
    await range.evaluate(element => {
      const min = Number(element.min || 0);
      const max = Number(element.max || 100);
      element.value = String(min + (max - min) * 0.63);
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await page.waitForTimeout(30);
    exercised.ranges += 1;
  } catch (error) {
    errors.push(`range interaction failed: ${error.message}`);
  }
}
if (screenshot) await page.screenshot({ path: screenshot, fullPage: true });
const report = {
  status: errors.length ? 'failed' : 'passed',
  title: await page.title(),
  canvas: await page.locator('canvas').count(),
  svg: await page.locator('svg').count(),
  buttons: await page.locator('button').count(),
  ranges: await page.locator('input[type="range"]').count(),
  errors,
  exercised,
  screenshot,
};
console.log(JSON.stringify(report, null, 2));
await browser.close();
process.exit(errors.length ? 1 : 0);
