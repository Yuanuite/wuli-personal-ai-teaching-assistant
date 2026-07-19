import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const script = path.join(here, '..', 'scripts', 'rank_actions.mjs');

function run(input, args = []) {
  return spawnSync(process.execPath, [script, '--format', 'json', ...args], { input: typeof input === 'string' ? input : JSON.stringify(input), encoding: 'utf8', timeout: 10000 });
}

test('lambda is calculated per hour with correct units', () => {
  const result = run([{ name: '验证方案', category: 'quality', mode: 'derisk', p: 0.4, minutes: 20, impact: 5, confidence: 1 }]);
  assert.equal(result.status, 0, result.stderr);
  const json = JSON.parse(result.stdout);
  assert.ok(Math.abs(json.actions[0].lambdaPerHour - 2.7489) < 0.0002);
});

test('zero minutes is skipped instead of crashing', () => {
  const result = run([
    { name: '坏输入', category: 'quality', mode: 'derisk', p: 0.5, minutes: 0 },
    { name: '有效输入', category: 'process', mode: 'execute', minutes: 10, impact: 3 }
  ]);
  assert.equal(result.status, 0);
  const json = JSON.parse(result.stdout);
  assert.equal(json.actions.length, 1);
  assert.equal(json.errors[0].code, 'INVALID_MINUTES');
});

test('blocker outranks other categories', () => {
  const input = [
    { name: '快速文档', category: 'knowledge', mode: 'execute', minutes: 1, impact: 5, confidence: 1, compounding: 2 },
    { name: '恢复构建', category: 'blocker', mode: 'execute', minutes: 120, impact: 3, confidence: 0.5 }
  ];
  const json = JSON.parse(run(input).stdout);
  assert.equal(json.actions[0].name, '恢复构建');
});

test('same input has deterministic order', () => {
  const input = [
    { name: '甲', category: 'quality', mode: 'execute', minutes: 10, impact: 3 },
    { name: '乙', category: 'quality', mode: 'execute', minutes: 10, impact: 3 }
  ];
  const a = run(input).stdout;
  const b = run(input).stdout;
  assert.deepEqual(JSON.parse(a).actions.map((x) => x.name), JSON.parse(b).actions.map((x) => x.name));
});

test('invalid probability is clamped and warned', () => {
  const json = JSON.parse(run([{ name: '异常概率', category: 'quality', mode: 'derisk', p: 2, minutes: 10 }]).stdout);
  assert.equal(json.actions.length, 1);
  assert.ok(json.warnings.some((w) => w.code === 'PROBABILITY_OUT_OF_RANGE'));
  assert.ok(Number.isFinite(json.actions[0].lambdaPerHour));
});

test('unicode names render in markdown without fixed-width corruption', () => {
  const result = spawnSync(process.execPath, [script, '--format', 'markdown'], {
    input: JSON.stringify([{ name: '验证中文检索能力', category: 'quality', mode: 'derisk', p: 0.4, minutes: 20 }]),
    encoding: 'utf8'
  });
  assert.match(result.stdout, /验证中文检索能力/);
  assert.match(result.stdout, /\/hour/);
});
