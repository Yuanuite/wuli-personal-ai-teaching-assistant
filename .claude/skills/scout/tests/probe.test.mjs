import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const script = path.join(here, '..', 'scripts', 'scout_probe.mjs');

function runProbe(projectPath, extra = []) {
  return spawnSync(process.execPath, [script, '--path', projectPath, '--format', 'json', ...extra], { encoding: 'utf8', timeout: 10000 });
}

function tempDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'scout-probe-'));
}

test('empty non-git directory returns structured result', () => {
  const dir = tempDir();
  const result = runProbe(dir);
  assert.equal(result.status, 0, result.stderr);
  const json = JSON.parse(result.stdout);
  assert.equal(json.ok, true);
  assert.equal(json.git.isRepository, false);
  assert.equal(json.summary.filesScanned, 0);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('secret-like file contents are not read', () => {
  const dir = tempDir();
  fs.writeFileSync(path.join(dir, '.env'), 'TODO FIXME SUPER_SECRET=abc');
  fs.writeFileSync(path.join(dir, 'app.js'), '// TODO safe marker\n');
  const result = runProbe(dir, ['--mode', 'deep']);
  const json = JSON.parse(result.stdout);
  assert.equal(json.summary.sensitiveFilesDetected, 1);
  assert.equal(json.summary.todoCount, 1);
  assert.equal(json.summary.fixmeCount, 0);
  assert.equal(json.security.contentsRead, false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('package scripts are detected but never executed', () => {
  const dir = tempDir();
  const marker = path.join(dir, 'SHOULD_NOT_EXIST');
  const pkg = {
    name: 'unsafe-fixture',
    scripts: { test: `node -e "require('fs').writeFileSync('${marker.replaceAll('\\', '\\\\')}','x')"` }
  };
  fs.writeFileSync(path.join(dir, 'package.json'), JSON.stringify(pkg));
  const result = runProbe(dir);
  const json = JSON.parse(result.stdout);
  assert.equal(json.ok, true);
  assert.deepEqual(json.project.packageJson[0].scriptNames, ['test']);
  assert.equal(fs.existsSync(marker), false);
  assert.equal(json.security.projectScriptsExecuted, false);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('invalid path returns JSON error without traceback', () => {
  const result = runProbe(path.join(os.tmpdir(), 'definitely-missing-scout-path'));
  assert.equal(result.status, 2);
  assert.doesNotMatch(result.stderr, /Error:|at /);
  const json = JSON.parse(result.stdout);
  assert.equal(json.ok, false);
  assert.equal(json.error.code, 'PATH_NOT_ACCESSIBLE');
});

test('file limit produces truncation warning', () => {
  const dir = tempDir();
  for (let i = 0; i < 5; i += 1) fs.writeFileSync(path.join(dir, `f${i}.js`), 'export default 1;');
  const result = runProbe(dir, ['--max-files', '2']);
  const json = JSON.parse(result.stdout);
  assert.equal(json.summary.truncated, true);
  assert.ok(json.warnings.some((w) => w.code === 'SCAN_LIMIT_REACHED'));
  fs.rmSync(dir, { recursive: true, force: true });
});
