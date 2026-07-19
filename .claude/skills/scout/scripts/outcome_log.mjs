#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';

const VERSION = '2.0.0';
const ALLOWED_KEYS = new Set([
  'action', 'category', 'mode', 'predictedP', 'predictedMinutes',
  'actualSuccess', 'actualMinutes', 'reason', 'lesson', 'timestamp'
]);

function fail(code, message, details = null) {
  const output = { schemaVersion: 'scout.outcome-write.v1', version: VERSION, ok: false, error: { code, message, details } };
  process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
  process.exit(2);
}

function parseArgs(argv) {
  let file = null;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--file') file = argv[++i];
    else if (argv[i].startsWith('--file=')) file = argv[i].slice(7);
    else if (argv[i] === '--help' || argv[i] === '-h') return { help: true };
    else throw new Error(`未知参数: ${argv[i]}`);
  }
  if (!file) throw new Error('必须显式提供 --file，例如 .scout/outcomes.jsonl');
  return { file };
}

function isInside(base, target) {
  const rel = path.relative(base, target);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

function finite(value, name, min, max, required = true) {
  if (value === undefined || value === null || value === '') {
    if (required) throw new Error(`${name} 缺失`);
    return null;
  }
  const n = Number(value);
  if (!Number.isFinite(n) || n < min || n > max) throw new Error(`${name} 必须在 ${min}–${max} 之间`);
  return n;
}

function cleanText(value, name, maxLength, required = false) {
  if (value === undefined || value === null) {
    if (required) throw new Error(`${name} 缺失`);
    return null;
  }
  const text = String(value).trim();
  if (required && !text) throw new Error(`${name} 不能为空`);
  if (text.length > maxLength) throw new Error(`${name} 超过 ${maxLength} 字符`);
  return text || null;
}

try {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('Usage: node outcome_log.mjs --file .scout/outcomes.jsonl < outcome.json');
    process.exit(0);
  }
  const rawText = fs.readFileSync(0, 'utf8').trim();
  if (!rawText) fail('EMPTY_INPUT', 'stdin 中没有 JSON');
  let raw;
  try { raw = JSON.parse(rawText); }
  catch (error) { fail('INVALID_JSON', error.message); }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) fail('INVALID_OBJECT', '输入必须是 JSON 对象');

  const unknownKeys = Object.keys(raw).filter((key) => !ALLOWED_KEYS.has(key));
  if (unknownKeys.length) fail('UNKNOWN_FIELDS', '为防止意外写入代码或敏感内容，存在未允许字段', unknownKeys);

  const record = {
    schemaVersion: 'scout.outcome.v1',
    action: cleanText(raw.action, 'action', 240, true),
    category: cleanText(raw.category, 'category', 32, false),
    mode: cleanText(raw.mode, 'mode', 32, false),
    predictedP: finite(raw.predictedP, 'predictedP', 0, 1, false),
    predictedMinutes: finite(raw.predictedMinutes, 'predictedMinutes', 0.1, 100000, true),
    actualSuccess: raw.actualSuccess,
    actualMinutes: finite(raw.actualMinutes, 'actualMinutes', 0.1, 100000, true),
    reason: cleanText(raw.reason, 'reason', 1000, false),
    lesson: cleanText(raw.lesson, 'lesson', 1000, false),
    timestamp: raw.timestamp ? new Date(raw.timestamp).toISOString() : new Date().toISOString()
  };
  if (typeof record.actualSuccess !== 'boolean') fail('INVALID_SUCCESS', 'actualSuccess 必须是 true 或 false');

  const cwd = fs.realpathSync(process.cwd());
  const target = path.resolve(cwd, args.file);
  if (!isInside(cwd, target)) fail('PATH_OUTSIDE_PROJECT', '日志必须位于当前项目目录内');

  const parent = path.dirname(target);
  fs.mkdirSync(parent, { recursive: true, mode: 0o700 });
  const parentReal = fs.realpathSync(parent);
  if (!isInside(cwd, parentReal)) fail('SYMLINK_ESCAPE', '日志目录解析到项目外部');
  if (fs.existsSync(target)) {
    const stat = fs.lstatSync(target);
    if (stat.isSymbolicLink()) fail('SYMLINK_REFUSED', '拒绝写入符号链接');
    if (!stat.isFile()) fail('NOT_A_FILE', '目标不是普通文件');
  }

  fs.appendFileSync(target, `${JSON.stringify(record)}\n`, { encoding: 'utf8', mode: 0o600, flag: 'a' });
  try { fs.chmodSync(target, 0o600); } catch { /* best effort on Windows */ }
  process.stdout.write(`${JSON.stringify({ schemaVersion: 'scout.outcome-write.v1', version: VERSION, ok: true, file: path.relative(cwd, target).split(path.sep).join('/'), record }, null, 2)}\n`);
} catch (error) {
  fail('OUTCOME_WRITE_FAILED', error.message);
}
