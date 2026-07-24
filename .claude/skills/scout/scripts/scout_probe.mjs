#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const VERSION = '2.0.0';
const IGNORE_DIRS = new Set([
  '.git', '.hg', '.svn', 'node_modules', 'vendor', '.venv', 'venv', '__pycache__',
  'dist', 'build', 'target', 'coverage', '.next', '.nuxt', '.cache', '.pytest_cache',
  '.mypy_cache', '.ruff_cache', '.idea', '.vscode', '.gradle', 'Pods'
]);
const TEXT_EXTENSIONS = new Set([
  '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.py', '.rs', '.go', '.java', '.kt',
  '.c', '.cc', '.cpp', '.h', '.hpp', '.cs', '.rb', '.php', '.swift', '.scala', '.sh',
  '.bash', '.zsh', '.md', '.mdx', '.txt', '.toml', '.yaml', '.yml', '.json', '.jsonl',
  '.ini', '.cfg', '.conf', '.xml', '.sql', '.graphql', '.vue', '.svelte'
]);
const SOURCE_EXTENSIONS = new Set([
  '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.py', '.rs', '.go', '.java', '.kt',
  '.c', '.cc', '.cpp', '.h', '.hpp', '.cs', '.rb', '.php', '.swift', '.scala', '.vue', '.svelte'
]);
const SENSITIVE_PATTERNS = [
  /^\.env(?:\..*)?$/i, /(?:^|[-_.])secrets?(?:[-_.]|$)/i, /credentials?/i,
  /id_rsa/i, /id_ed25519/i, /\.pem$/i, /\.key$/i, /keystore/i, /token/i
];
const DOC_NAMES = new Set([
  'readme.md', 'agents.md', 'claude.md', 'contributing.md', 'architecture.md',
  'security.md', 'runbook.md', 'operations.md', 'deploy.md', 'deployment.md'
]);
const QUALITY_NAMES = new Set([
  'eslint.config.js', 'eslint.config.mjs', '.eslintrc', '.eslintrc.json', '.eslintrc.js',
  'biome.json', 'biome.jsonc', 'ruff.toml', '.ruff.toml', 'mypy.ini', '.flake8',
  'pytest.ini', 'vitest.config.ts', 'vitest.config.js', 'jest.config.js', 'jest.config.ts',
  'playwright.config.ts', 'playwright.config.js', 'cypress.config.ts', 'cypress.config.js',
  'tsconfig.json', 'pyrightconfig.json', 'sonar-project.properties', '.golangci.yml'
]);
const MANIFESTS = new Set([
  'package.json', 'pyproject.toml', 'requirements.txt', 'poetry.lock', 'uv.lock',
  'cargo.toml', 'go.mod', 'pom.xml', 'build.gradle', 'build.gradle.kts',
  'composer.json', 'gemfile', 'makefile', 'justfile', 'dockerfile', 'docker-compose.yml',
  'docker-compose.yaml'
]);

function parseArgs(argv) {
  const args = { path: '.', mode: 'standard', format: 'json', maxFiles: null, maxFileBytes: 262144 };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') args.help = true;
    else if (arg === '--version') args.version = true;
    else if (arg === '--path') args.path = argv[++i];
    else if (arg.startsWith('--path=')) args.path = arg.slice(7);
    else if (arg === '--mode') args.mode = argv[++i];
    else if (arg.startsWith('--mode=')) args.mode = arg.slice(7);
    else if (arg === '--format') args.format = argv[++i];
    else if (arg.startsWith('--format=')) args.format = arg.slice(9);
    else if (arg === '--max-files') args.maxFiles = Number(argv[++i]);
    else if (arg.startsWith('--max-files=')) args.maxFiles = Number(arg.slice(12));
    else if (arg === '--max-file-bytes') args.maxFileBytes = Number(argv[++i]);
    else if (arg.startsWith('--max-file-bytes=')) args.maxFileBytes = Number(arg.slice(17));
    else throw new Error(`未知参数: ${arg}`);
  }
  if (!['quick', 'standard', 'deep'].includes(args.mode)) throw new Error(`mode 必须是 quick、standard 或 deep，收到: ${args.mode}`);
  if (!['json', 'markdown'].includes(args.format)) throw new Error(`format 必须是 json 或 markdown，收到: ${args.format}`);
  if (args.maxFiles === null) args.maxFiles = args.mode === 'quick' ? 1500 : args.mode === 'deep' ? 10000 : 5000;
  if (!Number.isFinite(args.maxFiles) || args.maxFiles < 1 || args.maxFiles > 100000) throw new Error('max-files 必须在 1–100000 之间');
  if (!Number.isFinite(args.maxFileBytes) || args.maxFileBytes < 1024 || args.maxFileBytes > 10 * 1024 * 1024) throw new Error('max-file-bytes 必须在 1024–10485760 之间');
  return args;
}

function help() {
  return `Scout read-only project probe v${VERSION}\n\nUsage:\n  node scout_probe.mjs [--path .] [--mode quick|standard|deep] [--format json|markdown]\n\nThe probe never executes project scripts, follows no symlinks, and skips secret-like files.`;
}

function isSensitiveName(name) {
  return SENSITIVE_PATTERNS.some((pattern) => pattern.test(name));
}

function safeRelative(root, absolute) {
  const rel = path.relative(root, absolute) || '.';
  return rel.split(path.sep).join('/');
}

function runGit(root, args, timeout = 3000) {
  const result = spawnSync('git', args, {
    cwd: root,
    encoding: 'utf8',
    timeout,
    windowsHide: true,
    maxBuffer: 1024 * 1024
  });
  return {
    ok: result.status === 0 && !result.error,
    stdout: result.stdout || '',
    stderr: result.stderr || '',
    error: result.error ? result.error.message : null,
    status: result.status
  };
}

function detectProjectTypes(fileNamesLower) {
  const types = [];
  if (fileNamesLower.has('package.json')) types.push('node');
  if (fileNamesLower.has('pyproject.toml') || fileNamesLower.has('requirements.txt')) types.push('python');
  if (fileNamesLower.has('cargo.toml')) types.push('rust');
  if (fileNamesLower.has('go.mod')) types.push('go');
  if (fileNamesLower.has('pom.xml') || fileNamesLower.has('build.gradle') || fileNamesLower.has('build.gradle.kts')) types.push('java');
  if (fileNamesLower.has('composer.json')) types.push('php');
  if (fileNamesLower.has('gemfile')) types.push('ruby');
  if (fileNamesLower.has('dockerfile') || fileNamesLower.has('docker-compose.yml') || fileNamesLower.has('docker-compose.yaml')) types.push('container');
  return types;
}

function inspectPackageJson(root, rel, warnings) {
  try {
    const absolute = path.join(root, rel);
    const stat = fs.statSync(absolute);
    if (stat.size > 1024 * 1024) {
      warnings.push({ code: 'MANIFEST_TOO_LARGE', message: `${rel} 超过 1MB，未解析` });
      return null;
    }
    const parsed = JSON.parse(fs.readFileSync(absolute, 'utf8'));
    const scriptNames = parsed && typeof parsed.scripts === 'object' && parsed.scripts ? Object.keys(parsed.scripts).sort() : [];
    return {
      path: rel,
      name: typeof parsed.name === 'string' ? parsed.name.slice(0, 120) : null,
      private: parsed.private === true,
      scriptNames,
      dependencyCount: Object.keys(parsed.dependencies || {}).length,
      devDependencyCount: Object.keys(parsed.devDependencies || {}).length
    };
  } catch (error) {
    warnings.push({ code: 'PACKAGE_JSON_PARSE_FAILED', message: `${rel}: ${error.message}` });
    return null;
  }
}

function probeProject(options) {
  const warnings = [];
  const inputPath = options.path || '.';
  let root;
  try {
    root = fs.realpathSync(path.resolve(inputPath));
  } catch (error) {
    return {
      schemaVersion: 'scout.probe.v1',
      version: VERSION,
      ok: false,
      error: { code: 'PATH_NOT_ACCESSIBLE', message: error.message },
      warnings,
      checkedAt: new Date().toISOString()
    };
  }

  let rootStat;
  try {
    rootStat = fs.statSync(root);
  } catch (error) {
    return { schemaVersion: 'scout.probe.v1', version: VERSION, ok: false, error: { code: 'STAT_FAILED', message: error.message }, warnings, checkedAt: new Date().toISOString() };
  }
  if (!rootStat.isDirectory()) {
    return { schemaVersion: 'scout.probe.v1', version: VERSION, ok: false, error: { code: 'NOT_A_DIRECTORY', message: 'path 必须指向目录' }, warnings, checkedAt: new Date().toISOString() };
  }

  const summary = {
    filesScanned: 0,
    directoriesScanned: 0,
    sourceFiles: 0,
    testFiles: 0,
    integrationTestFiles: 0,
    e2eTestFiles: 0,
    todoCount: 0,
    fixmeCount: 0,
    largeFilesSkipped: 0,
    symlinksSkipped: 0,
    permissionErrors: 0,
    sensitiveFilesDetected: 0,
    truncated: false
  };
  const relativeFiles = [];
  const fileNamesLower = new Set();
  const manifests = [];
  const docs = [];
  const qualityConfigs = [];
  const ciConfigs = [];
  const sensitiveFileExamples = [];
  const stack = [root];

  while (stack.length > 0 && summary.filesScanned < options.maxFiles) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
      summary.directoriesScanned += 1;
    } catch (error) {
      summary.permissionErrors += 1;
      warnings.push({ code: 'DIRECTORY_READ_FAILED', message: `${safeRelative(root, current)}: ${error.code || error.message}` });
      continue;
    }

    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      if (summary.filesScanned >= options.maxFiles) break;
      const absolute = path.join(current, entry.name);
      const rel = safeRelative(root, absolute);
      const lowerName = entry.name.toLowerCase();

      if (entry.isSymbolicLink()) {
        summary.symlinksSkipped += 1;
        continue;
      }
      if (entry.isDirectory()) {
        if (!IGNORE_DIRS.has(entry.name) && !IGNORE_DIRS.has(lowerName)) stack.push(absolute);
        continue;
      }
      if (!entry.isFile()) continue;

      summary.filesScanned += 1;
      relativeFiles.push(rel);
      fileNamesLower.add(lowerName);

      if (isSensitiveName(entry.name)) {
        summary.sensitiveFilesDetected += 1;
        if (sensitiveFileExamples.length < 5) sensitiveFileExamples.push(rel);
        continue;
      }

      let stat;
      try {
        stat = fs.statSync(absolute);
      } catch (error) {
        summary.permissionErrors += 1;
        warnings.push({ code: 'FILE_STAT_FAILED', message: `${rel}: ${error.code || error.message}` });
        continue;
      }

      if (stat.size > options.maxFileBytes) {
        summary.largeFilesSkipped += 1;
        continue;
      }

      const ext = path.extname(lowerName);
      if (SOURCE_EXTENSIONS.has(ext)) summary.sourceFiles += 1;
      const runnableTestName = /^(test[_-].+|.+\.(?:test|spec|e2e))\.[^/]+$/i.test(entry.name);
      const inTestDirectory = /(^|\/)(test|tests|__tests__)(\/|$)/i.test(rel);
      const isTestFile = runnableTestName || inTestDirectory;
      if (isTestFile) summary.testFiles += 1;
      if (runnableTestName && /(^|\/)integration(\/|$)|integration/i.test(rel)) {
        summary.integrationTestFiles += 1;
      }
      if (
        runnableTestName
        && (/(^|\/)(e2e|end-to-end)(\/|$)/i.test(rel) || /playwright|cypress|\.e2e\./i.test(rel))
      ) {
        summary.e2eTestFiles += 1;
      }

      if (MANIFESTS.has(lowerName)) manifests.push(rel);
      if (DOC_NAMES.has(lowerName)) docs.push(rel);
      if (QUALITY_NAMES.has(lowerName)) qualityConfigs.push(rel);
      if (/^\.github\/workflows\/.+\.ya?ml$/i.test(rel) || /^\.gitlab-ci\.ya?ml$/i.test(rel) || /^azure-pipelines\.ya?ml$/i.test(rel) || /^jenkinsfile$/i.test(rel)) ciConfigs.push(rel);

      if (options.mode !== 'quick' && TEXT_EXTENSIONS.has(ext)) {
        try {
          const text = fs.readFileSync(absolute, 'utf8');
          summary.todoCount += (text.match(/\bTODO\b/g) || []).length;
          summary.fixmeCount += (text.match(/\bFIXME\b/g) || []).length;
        } catch (error) {
          warnings.push({ code: 'TEXT_READ_FAILED', message: `${rel}: ${error.code || error.message}` });
        }
      }
    }
  }

  if (stack.length > 0 || summary.filesScanned >= options.maxFiles) {
    summary.truncated = true;
    warnings.push({ code: 'SCAN_LIMIT_REACHED', message: `达到 ${options.maxFiles} 个文件上限，结果为受限扫描` });
  }

  const lowerFiles = new Set(relativeFiles.map((item) => item.toLowerCase()));
  const projectTypes = detectProjectTypes(new Set([...fileNamesLower, ...lowerFiles]));
  const packageJsonFiles = manifests.filter((item) => path.basename(item).toLowerCase() === 'package.json').slice(0, 20);
  const packageJson = packageJsonFiles.map((rel) => inspectPackageJson(root, rel, warnings)).filter(Boolean);

  const git = {
    checked: true,
    available: false,
    isRepository: false,
    modified: 0,
    added: 0,
    deleted: 0,
    renamed: 0,
    untracked: 0,
    conflicted: 0,
    totalChanged: 0,
    recentCommits: []
  };
  const gitCheck = runGit(root, ['rev-parse', '--is-inside-work-tree']);
  if (gitCheck.error && /ENOENT/.test(gitCheck.error)) {
    git.checked = false;
    warnings.push({ code: 'GIT_NOT_AVAILABLE', message: '未找到 git，可继续使用文件证据' });
  } else if (gitCheck.ok && gitCheck.stdout.trim() === 'true') {
    git.available = true;
    git.isRepository = true;
    const status = runGit(root, ['status', '--porcelain=v1', '-uno'], 5000);
    if (status.ok) {
      const lines = status.stdout.split(/\r?\n/).filter(Boolean);
      for (const line of lines) {
        const code = line.slice(0, 2);
        if (code.includes('U') || ['AA', 'DD'].includes(code)) git.conflicted += 1;
        else if (code.includes('R')) git.renamed += 1;
        else if (code.includes('D')) git.deleted += 1;
        else if (code.includes('A')) git.added += 1;
        else git.modified += 1;
      }
      git.totalChanged = lines.length;
    } else {
      warnings.push({ code: 'GIT_STATUS_FAILED', message: status.error || status.stderr.trim() || 'git status 失败' });
    }
    const untracked = runGit(root, ['ls-files', '--others', '--exclude-standard'], 5000);
    if (untracked.ok) git.untracked = untracked.stdout.split(/\r?\n/).filter(Boolean).length;
    else warnings.push({ code: 'GIT_UNTRACKED_FAILED', message: untracked.error || untracked.stderr.trim() || '无法统计未跟踪文件' });

    const log = runGit(root, ['log', '-5', '--pretty=format:%h%x09%s'], 5000);
    if (log.ok) {
      git.recentCommits = log.stdout.split(/\r?\n/).filter(Boolean).map((line) => {
        const [hash, ...subjectParts] = line.split('\t');
        return { hash, subject: subjectParts.join('\t').slice(0, 200) };
      });
    } else if (log.status !== 128) {
      warnings.push({ code: 'GIT_LOG_FAILED', message: log.error || log.stderr.trim() || 'git log 失败' });
    }
  }

  const result = {
    schemaVersion: 'scout.probe.v1',
    version: VERSION,
    ok: true,
    mode: options.mode,
    checkedAt: new Date().toISOString(),
    root: '.',
    limits: { maxFiles: options.maxFiles, maxFileBytes: options.maxFileBytes },
    summary,
    git,
    project: {
      types: projectTypes,
      manifests: manifests.sort().slice(0, 100),
      packageJson,
      docs: docs.sort().slice(0, 100),
      qualityConfigs: qualityConfigs.sort().slice(0, 100),
      ciConfigs: ciConfigs.sort().slice(0, 100),
      hasReadme: docs.some((item) => path.basename(item).toLowerCase() === 'readme.md'),
      hasAgentGuide: docs.some((item) => ['agents.md', 'claude.md'].includes(path.basename(item).toLowerCase())),
      hasCI: ciConfigs.length > 0,
      hasQualityConfig: qualityConfigs.length > 0,
      testSignals: {
        files: summary.testFiles,
        integrationFiles: summary.integrationTestFiles,
        e2eFiles: summary.e2eTestFiles,
        detectedOnly: true,
        executed: false
      }
    },
    security: {
      sensitiveFilesDetected: summary.sensitiveFilesDetected,
      examples: sensitiveFileExamples,
      contentsRead: false,
      symlinksFollowed: false,
      projectScriptsExecuted: false,
      networkUsed: false
    },
    warnings
  };
  return result;
}

function toMarkdown(result) {
  if (!result.ok) return `# Scout Probe\n\n- 状态：失败\n- 错误：${result.error.code} — ${result.error.message}\n`;
  const s = result.summary;
  const g = result.git;
  const p = result.project;
  const lines = [
    '# Scout Probe',
    '',
    `- 模式：${result.mode}`,
    `- 扫描：${s.filesScanned} 个文件，${s.directoriesScanned} 个目录${s.truncated ? '（达到上限）' : ''}`,
    `- 项目类型：${p.types.length ? p.types.join(', ') : '未识别'}`,
    `- Git：${g.checked ? (g.isRepository ? `${g.totalChanged} 个已跟踪变更，${g.untracked} 个未跟踪文件` : '非 Git 项目') : '未检查（git 不可用）'}`,
    `- 测试结构：${p.testSignals.files} 个测试文件；集成 ${p.testSignals.integrationFiles}；E2E ${p.testSignals.e2eFiles}（未执行）`,
    `- CI / 质量配置：${p.ciConfigs.length} / ${p.qualityConfigs.length}`,
    `- TODO / FIXME：${result.mode === 'quick' ? '未扫描' : `${s.todoCount} / ${s.fixmeCount}`}`,
    `- 敏感文件名：${s.sensitiveFilesDetected}（内容未读取）`,
    `- 警告：${result.warnings.length}`
  ];
  if (result.warnings.length) {
    lines.push('', '## Warnings');
    for (const warning of result.warnings.slice(0, 20)) lines.push(`- ${warning.code}: ${warning.message}`);
  }
  return `${lines.join('\n')}\n`;
}

let options;
try {
  options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log(help());
    process.exit(0);
  }
  if (options.version) {
    console.log(VERSION);
    process.exit(0);
  }
  const result = probeProject(options);
  if (options.format === 'markdown') process.stdout.write(toMarkdown(result));
  else process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exit(result.ok ? 0 : 2);
} catch (error) {
  const failure = {
    schemaVersion: 'scout.probe.v1',
    version: VERSION,
    ok: false,
    error: { code: 'INVALID_ARGUMENT', message: error.message },
    warnings: [],
    checkedAt: new Date().toISOString()
  };
  process.stdout.write(`${JSON.stringify(failure, null, 2)}\n`);
  process.exit(2);
}
