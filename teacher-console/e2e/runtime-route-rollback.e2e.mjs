#!/usr/bin/env node
// C5.6 runtime-route-rollback: prove stale runtime is visible without a
// restart, a dead vision endpoint fails closed into the call ledger, and the
// source stays unapproved after a failed extraction.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  baseUrl,
  getJson,
  library,
  readJson,
  recordFailure,
  writeSummary,
} from "./visual-common.mjs";

const summary = { status: "failed", scenario: "runtime-route-rollback" };

async function postJson(relative, body) {
  const response = await fetch(new URL(relative, baseUrl), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Teacher-Console": "1" },
    body: JSON.stringify(body || {}),
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = JSON.parse(text);
  } catch {
    payload = { raw: text.slice(0, 300) };
  }
  return { status: response.status, payload };
}

try {
  const healthBefore = await getJson("/api/health");
  assert.equal(healthBefore.runtime_stale, false, "fresh server must not be stale");

  // 1) Config moves after server start -> runtime_stale flips without restart.
  const registryPath = path.join(library, "config", "model-registry.json");
  const originalRegistry = fs.readFileSync(registryPath, "utf8");
  const registry = JSON.parse(originalRegistry);
  registry.defaults = { ...(registry.defaults || {}), vision: "e2e-mock-vision" };
  registry.models = (registry.models || []).map(m =>
    m.id === "e2e-mock-vision" ? { ...m, model: "mock-vision-changed" } : m,
  );
  fs.writeFileSync(registryPath, `${JSON.stringify(registry, null, 2)}\n`);
  const healthAfter = await getJson("/api/health");
  assert.equal(healthAfter.runtime_stale, true, "config change must mark runtime stale");
  assert.notEqual(
    healthAfter.runtime_identity.model_registry_digest,
    healthBefore.runtime_identity.model_registry_digest,
    "registry digest must change",
  );
  fs.writeFileSync(registryPath, originalRegistry);
  const healthRestored = await getJson("/api/health");
  assert.equal(healthRestored.runtime_stale, false, "restoring config must un-stale the runtime");
  summary.stale_visibility = "passed";

  // 2) A dead vision endpoint fails closed into the call ledger.
  const { spawnSync } = await import("node:child_process");
  const python = process.env.E2E_PYTHON || "python3";
  const register = spawnSync(
    python,
    [
      path.join(process.env.E2E_PROJECT_ROOT, "teacher-console", "e2e", "register_dead_vision.py"),
      library,
    ],
    { encoding: "utf8" },
  );
  assert.equal(register.status, 0, `register_dead_vision failed: ${register.stderr || register.stdout}`);

  const fixture = path.join(
    process.env.E2E_PROJECT_ROOT,
    "teacher-console",
    "tests",
    "fixtures",
    "visual-routing",
    "clear-question.png",
  );
  const uploadResponse = await fetch(
    new URL(`/api/upload?filename=${encodeURIComponent("dead-vision-test.png")}`, baseUrl),
    {
      method: "POST",
      body: fs.readFileSync(fixture),
      headers: { "Content-Type": "image/png", "X-Teacher-Console": "1" },
    },
  );
  assert.equal(uploadResponse.status, 200);
  const uploaded = await uploadResponse.json();
  const runResponse = await fetch(new URL("/api/run-upload", baseUrl), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Teacher-Console": "1" },
    body: JSON.stringify({ filename: uploaded.filename, ocr: "none", subject: "高中物理" }),
  });
  const runPayload = await runResponse.json();
  const item = (runPayload.results || []).find(i => i.status === "ingested");
  assert.ok(item, "run-upload should ingest the image");
  const entryId = item.entry_id;
  const entryDir = path.join(library, "entries", entryId);
  const ledgerPath = path.join(entryDir, "visual-extract-request.json");
  assert.ok(fs.existsSync(ledgerPath), "visual-extract-request.json must exist after the failed extraction");
  const ledger = readJson(ledgerPath);
  assert.equal(ledger.status, "failed");
  assert.ok(ledger.failure_type, "ledger must record a failure_type");
  assert.ok(!fs.existsSync(path.join(entryDir, "visual-facts.json")), "no facts may be staged on failure");
  const record = readJson(path.join(entryDir, "record.json"));
  assert.equal(record.source_review?.status, "needs-review", "dead endpoint must keep the source unapproved");
  summary.dead_endpoint_fail_closed = "passed";

  summary.status = "passed";
  writeSummary(summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  recordFailure(summary, error);
  throw error;
}
