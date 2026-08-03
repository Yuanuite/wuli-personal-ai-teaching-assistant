#!/usr/bin/env node
// Work-tree core-failure-attribution-repair D4: July golden-set acceptance.
//
// Replays the five July teacher-reviewed complex problems (difficulty.score
// >= 60, read-only reference from student-site/) through the D1-D3 chain
// (rich five-section solve + auto diagram + isolated claim verification) and
// asserts the six-dimension quality rubric recorded in
// tests/fixtures/quality-alignment.json. The public student-site is never
// modified.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

import { baseUrl, entryDirFor, readJson, recordFailure, waitForState, writeSummary } from "./visual-common.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const alignment = JSON.parse(
  fs.readFileSync(path.join(scriptDir, "..", "tests", "fixtures", "quality-alignment.json"), "utf-8"),
);
const studentSite = path.join(scriptDir, "..", "..", "student-site");

const summary = { status: "failed", scenario: "analysis-core-quality-alignment", questions: [] };

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

async function postAction(entryId, action, body) {
  return postJson(`/api/entries/${encodeURIComponent(entryId)}/${action}`, body);
}

async function waitForJob(jobUrl, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  let job;
  while (Date.now() < deadline) {
    const response = await fetch(new URL(jobUrl, baseUrl));
    job = await response.json();
    if (job.status === "completed" || job.status === "failed") return job;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`job ${jobUrl} did not finish; last=${job?.status}`);
}

// --- deterministic unique PNGs (source-hash dedup needs distinct bytes) ----

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

function uniquePng(seed) {
  const width = 8;
  const height = 8;
  const raw = Buffer.alloc(height * (1 + width * 3));
  for (let y = 0; y < height; y += 1) {
    const row = y * (1 + width * 3);
    raw[row] = 0;
    for (let x = 0; x < width; x += 1) {
      raw[row + 1 + x * 3] = (seed * 37 + x * 11) & 0xff;
      raw[row + 2 + x * 3] = (seed * 53 + y * 7) & 0xff;
      raw[row + 3 + x * 3] = (seed * 91 + x + y) & 0xff;
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8; // bit depth
  header[9] = 2; // truecolor RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk("IHDR", header),
    pngChunk("IDAT", zlib.deflateSync(raw)),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

// --- golden problem extraction (read-only July samples) ---------------------

function extractProblem(questionId) {
  const content = fs.readFileSync(path.join(studentSite, "questions", questionId, "content.md"), "utf-8");
  let match = content.match(/^## 题干\n([\s\S]*?)\n## /m);
  if (!match) {
    match = content.match(/^# [^\n]*\n([\s\S]*?)\n---/m);
  }
  assert.ok(match, `${questionId}: cannot locate problem statement in July sample`);
  // July samples end the problem statement with a ``---`` separator before
  // the published solution; cut there so no answer text leaks into the replay.
  const lines = [];
  for (const line of match[1].split("\n")) {
    if (line.trim() === "---" || /^# /.test(line)) break;
    if (line.trim().startsWith("![")) continue;
    lines.push(line);
  }
  // The replayed entry carries no published figure assets; strip any inline
  // image reference left behind (validator rejects markdown pointing at
  // missing files).
  return lines
    .join("\n")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/assets\/asset-[0-9]+\.svg/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

async function runGoldenQuestion(index, item) {
  const problem = extractProblem(item.id);
  const filename = `d4-golden-${index}-${Date.now()}.png`;
  const uploadResponse = await fetch(new URL(`/api/upload?filename=${encodeURIComponent(filename)}`, baseUrl), {
    method: "POST",
    headers: { "Content-Type": "image/png", "X-Teacher-Console": "1" },
    body: uniquePng(index + 1),
  });
  assert.equal(uploadResponse.status, 200, `upload ${filename} returned ${uploadResponse.status}`);
  const uploaded = await uploadResponse.json();
  const report = await postJson("/api/run-upload", { filename: uploaded.filename, ocr: "none", subject: "高中物理" });
  const ingested = (report.payload.results || []).find(entry => entry.status === "ingested");
  assert.ok(ingested, `${item.id}: run-upload should ingest the golden fixture`);
  const entryId = ingested.entry_id;
  await waitForState(entryId, "needs-source-review");
  const cleanJob = ingested.source_clean && ingested.source_clean.job;
  if (cleanJob && cleanJob.id) {
    await waitForJob(`/api/jobs/${encodeURIComponent(cleanJob.id)}`);
  }
  const approveSource = await postAction(entryId, "approve-source", {
    problem,
    reviewer: "teacher",
    note: "D4 golden set",
  });
  assert.equal(approveSource.status, 200, `${item.id}: approve-source ${approveSource.status}`);
  const analyze = await postAction(entryId, "analyze", { routing_tier: "economy" });
  assert.equal(analyze.payload.status, "queued", `${item.id}: ${JSON.stringify(analyze.payload).slice(0, 200)}`);
  const job = await waitForJob(analyze.payload.job.url);
  assert.equal(job.status, "completed", `${item.id}: analysis job failed: ${job.error || job.result?.message}`);
  return entryId;
}

// --- six-dimension rubric checks --------------------------------------------

function checkSixDimensions(item, entryId) {
  const dir = entryDirFor(entryId);
  const request = readJson(path.join(dir, "analysis-request.json"));
  assert.equal(request.complexity?.decision, "decompose", `${item.id}: golden question must screen as complex`);
  assert.equal(request.complexity?.contract, "wuli.core-rich.v2");

  const core = readJson(path.join(dir, "core-solution.json"));
  const claims = core?.result?.claims || [];
  assert.ok(claims.length >= 1, `${item.id}: rich solve must carry claims`);
  const solution = fs.readFileSync(path.join(dir, "student-solution.md"), "utf-8");
  const result = { id: item.id, entry_id: entryId, dimensions: {} };

  // ① 五段齐全且内容针对本题
  for (const section of ["答案速览", "一眼识别", "详细解答", "易错点", "30 秒自测"]) {
    assert.ok(solution.includes(`## ${section}`), `${item.id}: missing section ${section}`);
  }
  for (const claim of claims) {
    assert.ok(solution.includes(claim.final_answer), `${item.id}: final_answer ${claim.id} missing`);
    for (const relation of claim.key_relations) {
      assert.ok(solution.includes(relation), `${item.id}: key_relation of ${claim.id} missing`);
    }
  }
  result.dimensions["five-sections"] = "pass";

  // ② 跨小问引用正确：每个 claim 的结论必须出现在详细解答段内且按序编号
  const detailMatch = solution.match(/## 详细解答\n([\s\S]*?)\n## 易错点/);
  assert.ok(detailMatch, `${item.id}: 详细解答 section not bounded`);
  const detail = detailMatch[1];
  const stepCount = (detail.match(/^### 第\s*[0-9一二三四五]+\s*步/gm) || []).length;
  assert.ok(stepCount >= 1 && stepCount <= 5, `${item.id}: numbered steps out of range (${stepCount})`);
  for (const claim of claims) {
    assert.ok(detail.includes(claim.final_answer), `${item.id}: ${claim.id} conclusion missing in 详细解答`);
  }
  result.dimensions["cross-target-references"] = "pass";

  // ③ 易错点「错误表现+纠正策略」成对
  const pitfallMatch = solution.match(/## 易错点\n([\s\S]*?)\n## 30 秒自测/);
  assert.ok(pitfallMatch, `${item.id}: 易错点 section not bounded`);
  const pitfalls = pitfallMatch[1]
    .split("\n")
    .filter(line => line.trim().startsWith("-"));
  assert.ok(pitfalls.length >= claims.length, `${item.id}: pitfalls must cover every claim`);
  for (const pitfall of pitfalls) {
    assert.ok(pitfall.includes("错误表现") && pitfall.includes("纠正策略"), `${item.id}: unpaired pitfall`);
  }
  result.dimensions["paired-pitfalls"] = "pass";

  // ④ 二级结论带适用条件
  const glanceMatch = solution.match(/## 一眼识别\n([\s\S]*?)\n## 详细解答/);
  assert.ok(glanceMatch && glanceMatch[1].includes("二级结论"), `${item.id}: missing secondary conclusion`);
  assert.ok(glanceMatch[1].includes("适用条件"), `${item.id}: secondary conclusion lacks applicability`);
  result.dimensions["conditioned-conclusions"] = "pass";

  // ⑤ 静态 SVG 图示存在（复杂题自动排队 diagram）
  assert.equal(request.diagram_task?.status, "completed", `${item.id}: auto diagram must complete`);
  assert.ok(fs.existsSync(path.join(dir, "assets", "explanatory.svg")), `${item.id}: explanatory.svg missing`);
  result.dimensions["static-svg"] = "pass";

  // ⑥ 量纲/适用条件核对项列出
  const selfTestMatch = solution.match(/## 30 秒自测\n([\s\S]*)$/);
  assert.ok(selfTestMatch, `${item.id}: 30 秒自测 section missing`);
  assert.ok(selfTestMatch[1].includes("量纲"), `${item.id}: self-test lacks dimension checks`);
  for (const claim of claims) {
    assert.ok(selfTestMatch[1].includes(claim.id), `${item.id}: self-test lacks ${claim.id} check item`);
  }
  result.dimensions["dimension-checks"] = "pass";

  // D3 链：独立 claim-verifier 必须 VERIFIED 才 canonical
  const verification = request.claim_verification || {};
  assert.equal(verification.status, "completed", `${item.id}: claim verification must run`);
  assert.equal(verification.answer_status, "canonical", `${item.id}: golden answer must verify`);
  assert.notEqual(verification.verifier_model_id, "", `${item.id}: verifier identity missing`);
  result.claim_verification = "canonical";
  result.agent_call_count = request.agent_call_count;
  return result;
}

try {
  assert.equal(alignment.golden_set.length, 5, "golden set must keep the five July samples");
  for (const [index, item] of alignment.golden_set.entries()) {
    const entryId = await runGoldenQuestion(index, item);
    summary.questions.push(checkSixDimensions(item, entryId));
  }
  summary.rubric = alignment.rubric.map(entry => entry.id);
  summary.status = "passed";
  writeSummary(summary);
  console.log(JSON.stringify(summary, null, 2));
} catch (error) {
  recordFailure(summary, error);
  throw error;
}
