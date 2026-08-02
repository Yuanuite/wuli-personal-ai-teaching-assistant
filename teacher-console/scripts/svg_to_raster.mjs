#!/usr/bin/env node
/* Privacy-free raster render for the diagram soft review (C4.4 input).
 * Usage: node svg_to_raster.mjs <input.svg> <output.png>
 */
import { chromium } from "playwright";

const [svgPath, pngPath] = process.argv.slice(2);
if (!svgPath || !pngPath) {
  console.error("usage: node svg_to_raster.mjs <input.svg> <output.png>");
  process.exit(2);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 920, height: 620 } });
  await page.goto(`file://${svgPath}`);
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.screenshot({ path: pngPath });
} finally {
  await browser.close();
}
