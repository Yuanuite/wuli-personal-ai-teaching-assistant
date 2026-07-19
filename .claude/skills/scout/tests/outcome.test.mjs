import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const script = path.join(here, '..', 'scripts', 'outcome_log.mjs');

function validRecord() {
  return { action: '验证方案', predictedP: 0.4, predictedMinutes: 20, actualSuccess: false, actualMinutes: 16, reason: '失败' };
}

test('writes a validated JSONL record inside project', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scout-outcome-'));
  const result = spawnSync(process.execPath, [script, '--file', '.scout/outcomes.jsonl'], { cwd: dir, input: JSON.stringify(validRecord()), encoding: 'utf8' });
  assert.equal(result.status, 0, result.stdout);
  const file = path.join(dir, '.scout', 'outcomes.jsonl');
  assert.equal(fs.existsSync(file), true);
  const saved = JSON.parse(fs.readFileSync(file, 'utf8').trim());
  assert.equal(saved.action, '验证方案');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('refuses paths outside current project', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scout-outcome-'));
  const result = spawnSync(process.execPath, [script, '--file', '../outside.jsonl'], { cwd: dir, input: JSON.stringify(validRecord()), encoding: 'utf8' });
  assert.equal(result.status, 2);
  const json = JSON.parse(result.stdout);
  assert.equal(json.error.code, 'PATH_OUTSIDE_PROJECT');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('refuses unknown fields to prevent accidental code dumps', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scout-outcome-'));
  const record = { ...validRecord(), sourceCode: 'secret' };
  const result = spawnSync(process.execPath, [script, '--file', '.scout/outcomes.jsonl'], { cwd: dir, input: JSON.stringify(record), encoding: 'utf8' });
  assert.equal(result.status, 2);
  const json = JSON.parse(result.stdout);
  assert.equal(json.error.code, 'UNKNOWN_FIELDS');
  fs.rmSync(dir, { recursive: true, force: true });
});
