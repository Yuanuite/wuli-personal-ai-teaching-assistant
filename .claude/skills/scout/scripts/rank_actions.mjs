#!/usr/bin/env node

import fs from 'node:fs';

const VERSION = '2.0.0';
const CATEGORY_RANK = { blocker: 5, quality: 4, process: 3, feature: 2, knowledge: 1 };
const CATEGORY_ALIASES = {
  blocker: 'blocker', blocking: 'blocker', '阻断': 'blocker', '阻断性': 'blocker',
  quality: 'quality', debt: 'quality', '质量': 'quality', '技术债': 'quality',
  process: 'process', automation: 'process', '流程': 'process', '流程改进': 'process',
  feature: 'feature', content: 'feature', '功能': 'feature', '内容建设': 'feature',
  knowledge: 'knowledge', docs: 'knowledge', '知识': 'knowledge', '知识沉淀': 'knowledge'
};
const MODE_ALIASES = {
  derisk: 'derisk', risk: 'derisk', explore: 'derisk', '降风险': 'derisk',
  execute: 'execute', execution: 'execute', deliver: 'execute', '执行': 'execute'
};

function parseArgs(argv) {
  const args = { input: null, format: 'json', limit: 20, strict: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') args.help = true;
    else if (arg === '--version') args.version = true;
    else if (arg === '--input') args.input = argv[++i];
    else if (arg.startsWith('--input=')) args.input = arg.slice(8);
    else if (arg === '--format') args.format = argv[++i];
    else if (arg.startsWith('--format=')) args.format = arg.slice(9);
    else if (arg === '--limit') args.limit = Number(argv[++i]);
    else if (arg.startsWith('--limit=')) args.limit = Number(arg.slice(8));
    else if (arg === '--strict') args.strict = true;
    else throw new Error(`未知参数: ${arg}`);
  }
  if (!['json', 'markdown', 'tsv'].includes(args.format)) throw new Error('format 必须是 json、markdown 或 tsv');
  if (!Number.isInteger(args.limit) || args.limit < 1 || args.limit > 1000) throw new Error('limit 必须是 1–1000 的整数');
  return args;
}

function help() {
  return `Scout action ranker v${VERSION}\n\nUsage:\n  node rank_actions.mjs [--input file] [--format json|markdown|tsv] [--limit 20] [--strict]\n\nInput: JSON array/envelope, JSONL, or TSV. Invalid rows become warnings and do not crash the process.`;
}

function readInput(file) {
  if (file) return fs.readFileSync(file, 'utf8');
  return fs.readFileSync(0, 'utf8');
}

function parseTsv(text) {
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== '');
  if (!lines.length) return [];
  const first = lines[0].split('\t').map((item) => item.trim());
  const known = new Set(['name', 'category', 'mode', 'p', 'minutes', 'impact', 'confidence', 'compounding', 'reversibility', 'goalAlignment', 'evidence']);
  const hasHeader = first.some((item) => known.has(item));
  const headers = hasHeader ? first : ['name', 'p', 'minutes', 'category', 'mode', 'impact', 'confidence', 'compounding'];
  const dataLines = hasHeader ? lines.slice(1) : lines;
  return dataLines.map((line, index) => {
    const values = line.split('\t');
    const row = { __line: index + (hasHeader ? 2 : 1) };
    headers.forEach((header, i) => { row[header] = values[i]; });
    return row;
  });
}

function parseInput(text) {
  const trimmed = text.trim();
  if (!trimmed) return [];
  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) return parsed;
    if (parsed && Array.isArray(parsed.actions)) return parsed.actions;
    if (parsed && Array.isArray(parsed.candidates)) return parsed.candidates;
    return [parsed];
  } catch {
    // Continue with JSONL or TSV.
  }
  const lines = trimmed.split(/\r?\n/).filter(Boolean);
  if (lines.every((line) => line.trim().startsWith('{'))) {
    return lines.map((line, index) => {
      try { return { ...JSON.parse(line), __line: index + 1 }; }
      catch (error) { return { __parseError: error.message, __line: index + 1 }; }
    });
  }
  return parseTsv(trimmed);
}

function finiteNumber(value, fallback = null) {
  if (value === '' || value === null || value === undefined) return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function normalizeEvidence(value) {
  if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean).slice(0, 20);
  if (typeof value === 'string' && value.trim()) return value.split('|').map((item) => item.trim()).filter(Boolean).slice(0, 20);
  return [];
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function normalizeCandidate(raw, index, warnings, errors) {
  const location = raw && raw.__line ? `第 ${raw.__line} 行` : `第 ${index + 1} 项`;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    errors.push({ code: 'INVALID_ROW', location, message: '候选动作必须是对象' });
    return null;
  }
  if (raw.__parseError) {
    errors.push({ code: 'PARSE_ERROR', location, message: raw.__parseError });
    return null;
  }

  const name = String(raw.name ?? raw.action ?? '').trim();
  if (!name) {
    errors.push({ code: 'MISSING_NAME', location, message: '缺少 name' });
    return null;
  }

  const categoryInput = String(raw.category ?? 'quality').trim().toLowerCase();
  const category = CATEGORY_ALIASES[categoryInput] || CATEGORY_ALIASES[String(raw.category ?? '').trim()] || 'quality';
  if (!CATEGORY_ALIASES[categoryInput] && raw.category !== undefined) warnings.push({ code: 'UNKNOWN_CATEGORY', location, message: `${raw.category} 已降级为 quality` });

  let modeInput = String(raw.mode ?? '').trim().toLowerCase();
  let mode = MODE_ALIASES[modeInput] || MODE_ALIASES[String(raw.mode ?? '').trim()];
  if (!mode) {
    mode = raw.p !== undefined ? 'derisk' : 'execute';
    warnings.push({ code: 'MODE_INFERRED', location, message: `mode 未提供，已推断为 ${mode}` });
  }

  let minutes = finiteNumber(raw.minutes ?? raw.T ?? raw.timeMinutes, null);
  if (minutes === null || minutes <= 0) {
    errors.push({ code: 'INVALID_MINUTES', location, message: 'minutes 必须是大于 0 的有限数' });
    return null;
  }
  if (minutes < 1) {
    warnings.push({ code: 'MINUTES_CLAMPED', location, message: `${minutes} 分钟已钳制为 1 分钟` });
    minutes = 1;
  }

  let impact = finiteNumber(raw.impact, 3);
  if (impact < 1 || impact > 5) {
    warnings.push({ code: 'IMPACT_CLAMPED', location, message: `impact ${impact} 已钳制到 1–5` });
    impact = clamp(impact, 1, 5);
  }
  let confidence = finiteNumber(raw.confidence, 0.6);
  if (confidence < 0 || confidence > 1) {
    warnings.push({ code: 'CONFIDENCE_CLAMPED', location, message: `confidence ${confidence} 已钳制到 0–1` });
    confidence = clamp(confidence, 0, 1);
  }
  let compounding = finiteNumber(raw.compounding, 0);
  if (compounding < 0 || compounding > 2) {
    warnings.push({ code: 'COMPOUNDING_CLAMPED', location, message: `compounding ${compounding} 已钳制到 0–2` });
    compounding = clamp(compounding, 0, 2);
  }
  let reversibility = finiteNumber(raw.reversibility, 0.7);
  if (reversibility < 0 || reversibility > 1) {
    warnings.push({ code: 'REVERSIBILITY_CLAMPED', location, message: `reversibility ${reversibility} 已钳制到 0–1` });
    reversibility = clamp(reversibility, 0, 1);
  }
  let goalAlignment = finiteNumber(raw.goalAlignment, 0);
  if (goalAlignment < 0 || goalAlignment > 2) {
    warnings.push({ code: 'GOAL_ALIGNMENT_CLAMPED', location, message: `goalAlignment ${goalAlignment} 已钳制到 0–2` });
    goalAlignment = clamp(goalAlignment, 0, 2);
  }

  let p = null;
  let lambdaPerHour = null;
  let valueRate = null;
  let scoreWithinCategory;

  if (mode === 'derisk') {
    p = finiteNumber(raw.p ?? raw.probability, null);
    if (p === null) {
      errors.push({ code: 'MISSING_PROBABILITY', location, message: 'derisk 动作必须提供 p' });
      return null;
    }
    if (p <= 0 || p > 1) warnings.push({ code: 'PROBABILITY_OUT_OF_RANGE', location, message: `p=${p} 超出 (0,1]，计算时钳制到 0.05–0.99` });
    const pForCalc = clamp(p, 0.05, 0.99);
    lambdaPerHour = -Math.log(pForCalc) / (minutes / 60);
    const lambdaCapped = Math.min(lambdaPerHour, 20);
    scoreWithinCategory = lambdaCapped * impact * confidence + compounding * 2 + reversibility * 2 + goalAlignment * 4 + 10;
  } else {
    valueRate = impact * 60 / minutes;
    scoreWithinCategory = Math.min(valueRate, 20) * confidence + compounding * 3 + reversibility * 2 + goalAlignment * 4;
  }

  const categoryScore = CATEGORY_RANK[category] * 1000;
  const totalScore = categoryScore + scoreWithinCategory;
  return {
    name,
    category,
    mode,
    p,
    minutes,
    impact,
    confidence,
    compounding,
    reversibility,
    goalAlignment,
    evidence: normalizeEvidence(raw.evidence),
    successCondition: raw.successCondition ? String(raw.successCondition).trim() : null,
    stopCondition: raw.stopCondition ? String(raw.stopCondition).trim() : null,
    lambdaPerHour,
    valueRate,
    score: totalScore,
    __index: index
  };
}

function rank(rawActions, strict) {
  const warnings = [];
  const errors = [];
  const actions = rawActions.map((item, index) => normalizeCandidate(item, index, warnings, errors)).filter(Boolean);
  actions.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (a.minutes !== b.minutes) return a.minutes - b.minutes;
    return a.__index - b.__index;
  });
  actions.forEach((action, index) => {
    action.rank = index + 1;
    delete action.__index;
    action.score = Number(action.score.toFixed(4));
    if (action.lambdaPerHour !== null) action.lambdaPerHour = Number(action.lambdaPerHour.toFixed(4));
    if (action.valueRate !== null) action.valueRate = Number(action.valueRate.toFixed(4));
  });
  return {
    schemaVersion: 'scout.ranking.v1',
    version: VERSION,
    ok: actions.length > 0 && (!strict || errors.length === 0),
    actions,
    warnings,
    errors,
    summary: { input: rawActions.length, valid: actions.length, skipped: rawActions.length - actions.length }
  };
}

function fmt(number, digits = 2) {
  return Number.isFinite(number) ? number.toFixed(digits) : '—';
}

function toMarkdown(result, limit) {
  const lines = [
    '# Scout Ranking',
    '',
    `有效候选：${result.summary.valid}/${result.summary.input}；跳过：${result.summary.skipped}；警告：${result.warnings.length}；错误：${result.errors.length}`,
    '',
    '| # | 动作 | 类别 | 模式 | 估计 | 影响 | 置信度 |',
    '|---:|---|---|---|---|---:|---:|'
  ];
  for (const action of result.actions.slice(0, limit)) {
    const estimate = action.mode === 'derisk'
      ? `p=${(action.p * 100).toFixed(0)}%, ${fmt(action.lambdaPerHour)}/hour, ${fmt(action.minutes, 0)}min`
      : `${fmt(action.minutes, 0)}min, value=${fmt(action.valueRate)}`;
    lines.push(`| ${action.rank} | ${action.name.replace(/\|/g, '\\|')} | ${action.category} | ${action.mode} | ${estimate} | ${fmt(action.impact, 1)} | ${fmt(action.confidence, 2)} |`);
  }
  if (result.warnings.length) {
    lines.push('', '## Warnings');
    for (const warning of result.warnings.slice(0, 30)) lines.push(`- ${warning.location || ''} ${warning.code}: ${warning.message}`.trim());
  }
  if (result.errors.length) {
    lines.push('', '## Errors');
    for (const error of result.errors.slice(0, 30)) lines.push(`- ${error.location || ''} ${error.code}: ${error.message}`.trim());
  }
  return `${lines.join('\n')}\n`;
}

function toTsv(result, limit) {
  const rows = ['rank\tname\tcategory\tmode\tp\tminutes\tlambda_per_hour\tvalue_rate\timpact\tconfidence\tscore'];
  for (const action of result.actions.slice(0, limit)) {
    const cleanName = action.name.replace(/[\t\r\n]/g, ' ');
    rows.push([
      action.rank, cleanName, action.category, action.mode,
      action.p ?? '', action.minutes, action.lambdaPerHour ?? '', action.valueRate ?? '',
      action.impact, action.confidence, action.score
    ].join('\t'));
  }
  return `${rows.join('\n')}\n`;
}

try {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { console.log(help()); process.exit(0); }
  if (args.version) { console.log(VERSION); process.exit(0); }
  const text = readInput(args.input);
  const rawActions = parseInput(text);
  const result = rank(rawActions, args.strict);
  if (args.format === 'markdown') process.stdout.write(toMarkdown(result, args.limit));
  else if (args.format === 'tsv') process.stdout.write(toTsv(result, args.limit));
  else process.stdout.write(`${JSON.stringify({ ...result, actions: result.actions.slice(0, args.limit) }, null, 2)}\n`);
  process.exit(result.ok ? 0 : 2);
} catch (error) {
  const failure = {
    schemaVersion: 'scout.ranking.v1', version: VERSION, ok: false,
    actions: [], warnings: [], errors: [{ code: 'RANKER_FAILURE', message: error.message }],
    summary: { input: 0, valid: 0, skipped: 0 }
  };
  process.stdout.write(`${JSON.stringify(failure, null, 2)}\n`);
  process.exit(2);
}
