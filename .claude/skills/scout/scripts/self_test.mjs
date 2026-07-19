#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const testDir = path.join(root, 'tests');
const tests = fs.readdirSync(testDir).filter((name) => name.endsWith('.test.mjs')).sort().map((name) => path.join(testDir, name));

const run = spawnSync(process.execPath, ['--test', ...tests], { cwd: root, stdio: 'inherit', timeout: 120000 });
if (run.error) {
  console.error(JSON.stringify({ ok: false, error: run.error.message }));
  process.exit(2);
}
if (run.status !== 0) process.exit(run.status ?? 2);

const probe = spawnSync(process.execPath, [path.join(here, 'scout_probe.mjs'), '--path', root, '--mode', 'quick', '--format', 'json'], { encoding: 'utf8', timeout: 20000 });
const ranking = spawnSync(process.execPath, [path.join(here, 'rank_actions.mjs'), '--input', path.join(root, 'examples', 'sample_candidates.json'), '--format', 'json'], { encoding: 'utf8', timeout: 20000 });
let probeJson;
let rankingJson;
try { probeJson = JSON.parse(probe.stdout); } catch { probeJson = null; }
try { rankingJson = JSON.parse(ranking.stdout); } catch { rankingJson = null; }
const ok = probe.status === 0 && probeJson?.ok === true && ranking.status === 0 && rankingJson?.ok === true && rankingJson.actions?.length === 3;
console.log(JSON.stringify({ schemaVersion: 'scout.self-test.v1', ok, tests: tests.length, probe: probeJson?.ok ?? false, ranking: rankingJson?.ok ?? false }, null, 2));
process.exit(ok ? 0 : 2);
