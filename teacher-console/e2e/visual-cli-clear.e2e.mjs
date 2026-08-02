#!/usr/bin/env node
// C5.3 visual-cli-clear: run the same synthetic clear image through the CLI
// chain -- ingest via the official skill CLI (human source review, no visual
// extraction), then run entry_visual_extract.py as a subprocess -- and assert
// the staged facts share the key fields of the web scenario (schema,
// model_identity.model_id, source_review.method=registry-visual-extract,
// state stays needs-source-review).

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  baseUrl,
  entryDirFor,
  getJson,
  ingestViaCli,
  library,
  readJson,
  recordFailure,
  runThinCli,
  visualFixture,
  waitForState,
  writeSummary,
} from "./visual-common.mjs";

const summary = { status: "failed", scenario: "visual-cli-clear", fixture: path.basename(visualFixture) };

try {
  assert.ok(baseUrl, "missing E2E_BASE_URL");
  const { entryId, cliLibrary } = ingestViaCli(visualFixture);
  summary.entry_id = entryId;

  // The CLI entry lives in the dedicated CLI library; read artifacts there
  // (the web server serves only the web library).
  const cliEntryDir = entryDirFor(entryId, cliLibrary);
  const beforeDir = cliEntryDir;
  assert.ok(!fs.existsSync(path.join(beforeDir, "visual-facts.json")), "no facts before the thin CLI run");
  const beforeReview = readJson(path.join(beforeDir, "source-review.json"));
  assert.equal(beforeReview.status, "needs-review");

  const extract = runThinCli(entryId, cliLibrary);
  assert.equal(extract.status, 0, `thin CLI failed: ${extract.stderr || extract.stdout}`);
  const cliSummary = JSON.parse(extract.stdout.trim().split("\n").at(-1));
  assert.equal(cliSummary.status, "completed");
  assert.equal(cliSummary.source_review_status, "needs-review");
  assert.equal(cliSummary.model_id, "e2e-mock-vision");
  assert.equal(cliSummary.upstream_model, "mock-vision");

  const entryDir = entryDirFor(entryId, cliLibrary);
  const facts = readJson(path.join(entryDir, "visual-facts.json"));
  assert.equal(facts.schema, "wuli.visual-facts.v1");
  assert.equal(facts.model_identity.model_id, "e2e-mock-vision");
  assert.equal(facts.model_identity.provider, "openai-compatible");
  assert.deepEqual(facts.uncertainties, [], "clear image must not carry uncertainties");

  const ledger = readJson(path.join(entryDir, "visual-extract-request.json"));
  assert.equal(ledger.status, "completed");
  assert.equal(ledger.model_id, "e2e-mock-vision");

  const review = readJson(path.join(entryDir, "source-review.json"));
  assert.equal(review.method, "registry-visual-extract");
  assert.equal(review.status, "needs-review");

  const record = readJson(path.join(entryDir, "record.json"));
  assert.equal(record.ocr?.review_required, true);
  assert.equal(record.source_review?.status, "needs-review");
  assert.equal(record.source_review?.method, "registry-visual-extract");

  summary.status = "passed";
  summary.cli_exit = extract.status;
  summary.mock_url = process.env.E2E_VISION_MOCK_URL || "";
  writeSummary(summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  recordFailure(summary, error);
  throw error;
}
