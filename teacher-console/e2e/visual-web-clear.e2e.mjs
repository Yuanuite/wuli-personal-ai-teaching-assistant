#!/usr/bin/env node
// C5.2 visual-web-clear: upload the synthetic clear-question.png through the
// web path (POST /api/upload + POST /api/run-upload) and assert the
// registry-routed visual extraction stages facts without auto-approving the
// source. When no vision route is available the run must fail closed with a
// durable ledger reason and the entry must remain human-reviewable; either
// way "no auto approval" is mandatory.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  baseUrl,
  entryDirFor,
  getJson,
  library,
  readJson,
  recordFailure,
  uploadEntry,
  waitForState,
  writeSummary,
} from "./visual-common.mjs";

const fixtureName = "clear-question.png";
const summary = { status: "failed", scenario: "visual-web-clear", fixture: fixtureName };

try {
  assert.ok(baseUrl, "missing E2E_BASE_URL");
  const { entryId } = await uploadEntry(fixtureName);
  summary.entry_id = entryId;

  const detail = await waitForState(entryId, "needs-source-review");
  assert.equal(detail.state, "needs-source-review");

  const entryDir = entryDirFor(entryId);
  const record = readJson(path.join(entryDir, "record.json"));
  const ledger = readJson(path.join(entryDir, "visual-extract-request.json"));
  const review = readJson(path.join(entryDir, "source-review.json"));

  if (ledger.status === "completed") {
    // MiMo success path: facts staged, gate may pass but the teacher gate
    // must stay open.
    const facts = readJson(path.join(entryDir, "visual-facts.json"));
    assert.equal(facts.schema, "wuli.visual-facts.v1");
    assert.equal(facts.model_identity.model_id, "e2e-mock-vision");
    assert.equal(facts.model_identity.provider, "openai-compatible");
    assert.deepEqual(facts.uncertainties, [], "clear image must not carry uncertainties");
    assert.equal(ledger.model_id, "e2e-mock-vision");
    assert.equal(ledger.status, "completed");
    assert.ok(fs.existsSync(path.join(entryDir, "visual-facts-gate.json")));
    summary.extraction = "completed";
  } else {
    // Fail-closed path (no vision route): the ledger records a durable
    // reason and no facts may be staged.
    assert.ok(ledger.failure_type && ledger.failure_type.length > 0, "ledger must record failure_type");
    assert.ok(ledger.message && ledger.message.length > 0, "ledger must record a failure message");
    assert.ok(
      !fs.existsSync(path.join(entryDir, "visual-facts.json")),
      "no facts may be staged on a failed extraction",
    );
    summary.extraction = "failed-closed";
  }

  // Mandatory in both branches: the source must never be auto-approved.
  assert.equal(record.ocr?.review_required, true, "OCR review must remain required");
  assert.equal(record.source_review?.status, "needs-review", "source must never be auto-approved");
  assert.equal(review.status, "needs-review", "source-review report must stay needs-review");
  assert.equal(detail.source_review?.status, "needs-review", "entry detail must stay needs-review");

  summary.status = "passed";
  summary.mock_url = process.env.E2E_VISION_MOCK_URL || "";
  writeSummary(summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  recordFailure(summary, error);
  throw error;
}
