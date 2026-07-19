import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const script = path.join(here, '..', 'scripts', 'compute_lambda.py');
const python = process.platform === 'win32' ? 'python' : 'python3';

function available() {
  const result = spawnSync(python, ['--version'], { encoding: 'utf8' });
  return result.status === 0;
}

test('python calculator handles T=0 without traceback', { skip: !available() }, () => {
  const result = spawnSync(python, [script, '--format', 'json'], { input: 'bad\t0.5\t0\ngood\t0.4\t20\n', encoding: 'utf8' });
  assert.equal(result.status, 0);
  assert.doesNotMatch(result.stderr, /Traceback/);
  const json = JSON.parse(result.stdout);
  assert.equal(json.entries.length, 1);
  assert.equal(json.warnings[0].code, 'INVALID_MINUTES');
  assert.ok(Math.abs(json.entries[0].lambda_value - 2.748872) < 0.00001);
});

test('python calculator rejects NaN and invalid p', { skip: !available() }, () => {
  const result = spawnSync(python, [script, '--format', 'json'], { input: 'nan\tNaN\t10\nprob\t1.2\t10\n', encoding: 'utf8' });
  assert.equal(result.status, 2);
  const json = JSON.parse(result.stdout);
  assert.equal(json.ok, false);
  assert.equal(json.warnings.length, 2);
});
