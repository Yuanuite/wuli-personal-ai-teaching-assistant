#!/usr/bin/env node
// Shared helpers for the visual-consistency E2E scenarios (C5.2/C5.3/C5.4).
// These scenarios are pure HTTP/CLI checks; no browser is launched.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

export const baseUrl = process.env.E2E_BASE_URL;
export const library = process.env.E2E_LIBRARY;
export const projectRoot = process.env.E2E_PROJECT_ROOT;
export const python = process.env.E2E_PYTHON || "python3";
export const artifactDir =
  process.env.E2E_ARTIFACT_DIR || path.join(projectRoot, "test-results", "e2e");
export const visualFixture = process.env.E2E_VISUAL_FIXTURE;
export const visualMode = process.env.E2E_VISUAL_MODE;
export const visionMockUrl = process.env.E2E_VISION_MOCK_URL || "";

for (const [name, value] of Object.entries({ baseUrl, library, projectRoot })) {
  if (!value) throw new Error(`missing required E2E environment: ${name}`);
}
fs.mkdirSync(artifactDir, { recursive: true });

export function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

export async function getJson(relative) {
  const response = await fetch(new URL(relative, baseUrl));
  assert.equal(response.status, 200, `GET ${relative} returned ${response.status}`);
  return response.json();
}

export async function waitForState(entryId, expected, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  let detail;
  while (Date.now() < deadline) {
    detail = await getJson(`/api/entries/${encodeURIComponent(entryId)}`);
    if (detail.state === expected) return detail;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`entry ${entryId} did not reach ${expected}; last state=${detail?.state}`);
}

// Upload a fixture through the two-step web upload path used by the teacher
// UI: POST /api/upload?filename=... (raw image bytes) then POST
// /api/run-upload (JSON {filename, ocr, subject}). run-upload runs the
// registry-routed visual extraction synchronously inside the request.
export async function uploadEntry(fixtureName, { ocr = "none", subject = "高中物理" } = {}) {
  const uploadResponse = await fetch(
    new URL(`/api/upload?filename=${encodeURIComponent(fixtureName)}`, baseUrl),
    {
      method: "POST",
      body: fs.readFileSync(visualFixture),
      headers: { "Content-Type": "image/png", "X-Teacher-Console": "1" },
    },
  );
  assert.equal(uploadResponse.status, 200, `upload ${fixtureName} returned ${uploadResponse.status}`);
  const uploaded = await uploadResponse.json();
  assert.equal(uploaded.status, "uploaded");
  const runResponse = await fetch(new URL("/api/run-upload", baseUrl), {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Teacher-Console": "1" },
    body: JSON.stringify({ filename: uploaded.filename, ocr, subject }),
  });
  assert.equal(runResponse.status, 200, `run-upload returned ${runResponse.status}`);
  const report = await runResponse.json();
  const ingested = (report.results || []).find(item => item.status === "ingested");
  assert.ok(ingested, "run-upload should have ingested the image");
  return { entryId: ingested.entry_id, report, item: ingested };
}

// Replicate model_registry._model_probe_digest() in JS so a hand-written CLI
// registry entry carries the exact config_digest the registry validator
// computes (provider/base_url/model/api_key_env/api_key_digest/remote, sorted
// keys, ", "/": " separators). A mismatch would silently make the vision model
// "not passed connection test" and the CLI extraction would fail closed.
function modelProbeDigest(baseUrl) {
  const payload = {
    provider: "openai-compatible",
    base_url: baseUrl,
    model: "mock-vision",
    api_key_env: "TEACHER_CONSOLE_AGENT_API_KEY",
    api_key_digest: createHash("sha256").update("e2e-mock-key").digest("hex"),
    remote: false,
  };
  const keys = Object.keys(payload).sort();
  const serialized = `{${keys.map(key => `"${key}": ${JSON.stringify(payload[key])}`).join(", ")}}`;
  return createHash("sha256").update(serialized).digest("hex");
}

// Ingest a fixture through the official skill CLI with human source review
// (no visual extraction), so the thin CLI can be exercised separately.
export function ingestViaCli(fixturePath) {
  const processUploads = path.join(
    projectRoot,
    ".claude",
    "skills",
    "manage-student-error-library",
    "scripts",
    "process_uploads.py",
  );
  // Use a dedicated CLI library so the same fixture creates a fresh entry
  // instead of deduping onto the web library's entry by content hash.
  const cliLibrary = path.join(path.dirname(library), "student-error-library-cli");
  fs.mkdirSync(path.join(cliLibrary, "config"), { recursive: true });
  const visionMockUrl = process.env.E2E_VISION_MOCK_URL || "";
  if (visionMockUrl) {
    // Register the controlled mock vision route through model_registry so
    // probe digests match the production computation (mirrors run_e2e.py).
    fs.writeFileSync(
      path.join(cliLibrary, "config.json"),
      `${JSON.stringify({ schema_version: 1, privacy: { allow_remote_visual_review: true }, source_review: { mode: "registry" } }, null, 2)}\n`,
    );
    const register = spawnSync(
      python,
      [
        path.join(projectRoot, "teacher-console", "e2e", "register_cli_models.py"),
        cliLibrary,
        visionMockUrl,
      ],
      { encoding: "utf8" },
    );
    assert.equal(register.status, 0, `register_cli_models failed: ${register.stderr || register.stdout}`);
  }
  const result = spawnSync(
    python,
    [
      processUploads,
      "--library",
      cliLibrary,
      "start",
      "--input",
      fixturePath,
      "--ocr",
      "none",
      "--source-review-mode",
      "human",
    ],
    { encoding: "utf8" },
  );
  assert.equal(result.status, 0, `process_uploads start failed: ${result.stderr || result.stdout}`);
  const report = JSON.parse(result.stdout.trim());
  const entryId = report.work_orders?.[0]?.entry_id;
  assert.ok(entryId, "process_uploads start should produce an entry id");
  return { entryId, report, cliLibrary };
}

// Run the thin visual-extract CLI against the isolated E2E CLI library.
// Returns the spawnSync result; callers decide whether a non-zero exit is a
// valid fail-closed outcome (blurred scenario) or a hard failure.
export function runThinCli(entryId, cliLibrary) {
  const thin = path.join(projectRoot, "teacher-console", "e2e", "entry_visual_extract_isolated.py");
  return spawnSync(python, [thin, cliLibrary || library, entryId], { encoding: "utf8" });
}

export function entryDirFor(entryId, lib) {
  return path.join(lib || library, "entries", entryId);
}

export function writeJson(name, value) {
  fs.writeFileSync(path.join(artifactDir, name), `${JSON.stringify(value, null, 2)}\n`);
}

export function writeSummary(summary) {
  writeJson(`${summary.scenario}-summary.json`, summary);
}

export function recordFailure(summary, error) {
  writeJson("failure.json", {
    status: "failed",
    ...summary,
    error: String(error.stack || error),
  });
}
