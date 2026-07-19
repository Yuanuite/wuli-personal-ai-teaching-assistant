#!/usr/bin/env node

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const scripts = path.join(root, 'scripts');
const results = [];

function add(id, pass, detail) {
  results.push({ id, pass: Boolean(pass), detail });
}

function runNode(script, args = [], options = {}) {
  return spawnSync(process.execPath, [path.join(scripts, script), ...args], {
    encoding: 'utf8', timeout: 15000, ...options
  });
}

function parseJson(text) {
  try { return JSON.parse(text); } catch { return null; }
}

// E01
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scout-eval-empty-'));
  const r = runNode('scout_probe.mjs', ['--path', dir, '--format', 'json']);
  const j = parseJson(r.stdout);
  add('E01', r.status === 0 && j?.ok && j.summary.filesScanned === 0 && j.git.isRepository === false, j?.error || 'structured empty-project result');
  fs.rmSync(dir, { recursive: true, force: true });
}

// E02
{
  const r = runNode('scout_probe.mjs', ['--path', path.join(os.tmpdir(), 'missing-scout-eval'), '--format', 'json']);
  const j = parseJson(r.stdout);
  add('E02', r.status === 2 && j?.error?.code === 'PATH_NOT_ACCESSIBLE' && !/traceback| at /i.test(r.stderr), j?.error?.code || r.stderr);
}

// E03 / E04 / E07
{
  const input = [
    { name: 'bad-time', category: 'quality', mode: 'derisk', p: 0.5, minutes: 0 },
    { name: 'bad-p', category: 'quality', mode: 'derisk', p: 2, minutes: 10, impact: 2 },
    { name: 'fast-risk', category: 'quality', mode: 'derisk', p: 0.4, minutes: 20, impact: 5, confidence: 1 },
    { name: 'slow-safe', category: 'quality', mode: 'derisk', p: 0.9, minutes: 240, impact: 5, confidence: 1 }
  ];
  const r = runNode('rank_actions.mjs', ['--format', 'json'], { input: JSON.stringify(input) });
  const j = parseJson(r.stdout);
  add('E03', r.status === 0 && j?.summary?.skipped === 1 && j.actions.length === 3, j?.summary);
  add('E04', j?.warnings?.some((w) => w.code === 'PROBABILITY_OUT_OF_RANGE') && Number.isFinite(j.actions.find((a) => a.name === 'bad-p')?.lambdaPerHour), 'invalid p handled');
  add('E07', j?.actions.findIndex((a) => a.name === 'fast-risk') < j?.actions.findIndex((a) => a.name === 'slow-safe'), 'failure discovery order');
}

// E09 / E10
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scout-eval-sec-'));
  const marker = path.join(dir, 'pwned');
  fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify({ scripts: { test: `node -e "require('fs').writeFileSync('${marker.replaceAll('\\', '\\\\')}','x')"` } }));
  fs.writeFileSync(path.join(dir, '.env'), 'TODO SECRET=1');
  const r = runNode('scout_probe.mjs', ['--path', dir, '--mode', 'deep', '--format', 'json']);
  const j = parseJson(r.stdout);
  add('E09', r.status === 0 && !fs.existsSync(marker) && j?.security?.projectScriptsExecuted === false, 'project script not executed');
  add('E10', j?.summary?.sensitiveFilesDetected === 1 && j?.summary?.todoCount === 0 && j?.security?.contentsRead === false, 'secret skipped');
  fs.rmSync(dir, { recursive: true, force: true });
}

// E11
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'scout-eval-log-'));
  const record = { action: 'x', predictedMinutes: 10, actualSuccess: true, actualMinutes: 12 };
  const r = runNode('outcome_log.mjs', ['--file', '../outside.jsonl'], { cwd: dir, input: JSON.stringify(record) });
  const j = parseJson(r.stdout);
  add('E11', r.status === 2 && j?.error?.code === 'PATH_OUTSIDE_PROJECT', j?.error?.code);
  fs.rmSync(dir, { recursive: true, force: true });
}

// E12 / E13 and package completeness
{
  const skill = fs.readFileSync(path.join(root, 'SKILL.md'), 'utf8');
  const fm = skill.match(/^---\n([\s\S]*?)\n---/);
  const descriptionLine = fm?.[1].split(/\r?\n/).find((line) => line.startsWith('description:')) || '';
  add('E12', skill.includes('{baseDir}') && !skill.includes('.claude/skills'), 'portable paths');
  add('E13', /^name: scout$/m.test(fm?.[1] || '') && descriptionLine.length > 13 && [...descriptionLine.slice(13)].length < 160 && !descriptionLine.includes('|'), descriptionLine);
}

// E14
{
  const file = path.join(os.tmpdir(), `scout-cal-${process.pid}.jsonl`);
  fs.writeFileSync(file, [
    JSON.stringify({ action: 'a', predictedP: 0.8, predictedMinutes: 10, actualSuccess: true, actualMinutes: 20 }),
    JSON.stringify({ action: 'b', predictedP: 0.8, predictedMinutes: 20, actualSuccess: false, actualMinutes: 40 })
  ].join('\n'));
  const r = runNode('calibrate.mjs', ['--file', file, '--format', 'json']);
  const j = parseJson(r.stdout);
  add('E14', r.status === 0 && j?.timeRatioMedian === 2 && j.suggestions.some((s) => s.includes('2.00')), j?.suggestions);
  fs.rmSync(file, { force: true });
}

const required = [
  'SKILL.md', 'README.md', 'CHANGELOG.md', 'LICENSE', 'VERSION',
  'scripts/scout_probe.mjs', 'scripts/rank_actions.mjs', 'scripts/outcome_log.mjs',
  'references/security.md', 'references/evaluation.md', 'schema/candidate.schema.json'
];
add('PKG', required.every((rel) => fs.existsSync(path.join(root, rel))), 'required files');

const passed = results.filter((r) => r.pass).length;
const total = results.length;
const report = {
  schemaVersion: 'scout.eval.v1',
  ok: passed === total,
  passed,
  total,
  scorePercent: Number((passed / total * 100).toFixed(1)),
  results
};
console.log(JSON.stringify(report, null, 2));
process.exit(report.ok ? 0 : 2);
