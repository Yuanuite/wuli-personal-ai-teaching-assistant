#!/usr/bin/env node
// C5.4 visual-blurred-fail-closed: upload blurred-question.png once through
// the web path and once through the CLI chain. On both sides the extraction
// must either stage facts with non-empty uncertainties or fail closed; the
// entry must stay at needs-source-review; analysis must never start (no
// student-solution.md / analysis job) and the source must never be approved.

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
  uploadEntry,
  visualFixture,
  waitForState,
  writeSummary,
} from "./visual-common.mjs";

const fixtureName = "blurred-question.png";
const summary = { status: "failed", scenario: "visual-blurred-fail-closed", fixture: fixtureName };

// Assert the shared fail-closed invariants for one entry produced by either
// entry path (web or CLI).
function assertFailClosedInvariants(entryId, label, lib) {
  const entryDir = entryDirFor(entryId, lib);
  const record = readJson(path.join(entryDir, "record.json"));
  const review = readJson(path.join(entryDir, "source-review.json"));
  const ledger = readJson(path.join(entryDir, "visual-extract-request.json"));

  if (ledger.status === "completed") {
    const facts = readJson(path.join(entryDir, "visual-facts.json"));
    assert.ok(
      Array.isArray(facts.uncertainties) && facts.uncertainties.length > 0,
      `${label}: blurred image must yield non-empty uncertainties`,
    );
    assert.equal(facts.schema, "wuli.visual-facts.v1");
    assert.equal(review.method, "registry-visual-extract");
    summary[`${label}_outcome`] = "completed-with-uncertainties";
  } else {
    assert.ok(ledger.failure_type && ledger.failure_type.length > 0, `${label}: ledger must record failure_type`);
    assert.ok(ledger.message && ledger.message.length > 0, `${label}: ledger must record a failure message`);
    assert.ok(
      !fs.existsSync(path.join(entryDir, "visual-facts.json")),
      `${label}: no facts may be staged on a failed extraction`,
    );
    summary[`${label}_outcome`] = "failed-closed";
  }

  // Never auto-approve the source.
  assert.equal(record.ocr?.review_required, true, `${label}: OCR review must remain required`);
  assert.equal(record.source_review?.status, "needs-review", `${label}: source must never be approved`);
  assert.equal(review.status, "needs-review", `${label}: review packet must stay needs-review`);

  // Analysis must never start.
  for (const artifact of [
    "student-solution.md",
    "teacher-solution.md",
    "analysis-request.json",
    "answer-review.json",
  ]) {
    assert.ok(
      !fs.existsSync(path.join(entryDir, artifact)),
      `${label}: ${artifact} must not exist (analysis must never start)`,
    );
  }
  return entryDir;
}

try {
  assert.ok(baseUrl, "missing E2E_BASE_URL");

  // Web entry in the web library.
  const web = await uploadEntry(fixtureName);
  summary.web_entry_id = web.entryId;
  await waitForState(web.entryId, "needs-source-review");
  assertFailClosedInvariants(web.entryId, "web", library);
  const webDetail = await getJson(`/api/entries/${encodeURIComponent(web.entryId)}`);
  assert.equal(webDetail.state, "needs-source-review", "web entry must stay needs-source-review");

  // CLI entry in a dedicated CLI library (fresh entry, no dedupe with web).
  const cli = ingestViaCli(visualFixture);
  summary.cli_entry_id = cli.entryId;
  const beforeDir = entryDirFor(cli.entryId, cli.cliLibrary);
  assert.ok(
    !fs.existsSync(path.join(beforeDir, "visual-facts.json")),
    "no facts before the thin CLI run",
  );
  const extract = runThinCli(cli.entryId, cli.cliLibrary);
  if (extract.status !== 0) {
    // Fail-closed is an accepted outcome when the vision route is unavailable.
    const cliOutcome = JSON.parse(extract.stdout.trim().split("\n").at(-1) || '{"status":"failed"}');
    assert.equal(cliOutcome.status, "failed");
    summary.cli_exit = extract.status;
  } else {
    const cliOutcome = JSON.parse(extract.stdout.trim().split("\n").at(-1));
    assert.equal(cliOutcome.status, "completed");
    summary.cli_exit = extract.status;
  }
  assertFailClosedInvariants(cli.entryId, "cli", cli.cliLibrary);
  const cliRecord = readJson(path.join(beforeDir, "record.json"));
  assert.equal(cliRecord.ocr?.review_required, true, "cli: OCR review must remain required");
  assert.equal(cliRecord.source_review?.status, "needs-review", "cli: source must never be approved");

  summary.status = "passed";
  summary.mock_url = process.env.E2E_VISION_MOCK_URL || "";
  writeSummary(summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  recordFailure(summary, error);
  throw error;
}
