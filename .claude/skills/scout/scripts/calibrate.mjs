#!/usr/bin/env node

import fs from 'node:fs';

const VERSION = '2.0.0';

function parseArgs(argv) {
  const args = { file: null, format: 'json' };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--file') args.file = argv[++i];
    else if (arg.startsWith('--file=')) args.file = arg.slice(7);
    else if (arg === '--format') args.format = argv[++i];
    else if (arg.startsWith('--format=')) args.format = arg.slice(9);
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`未知参数: ${arg}`);
  }
  if (!args.file) throw new Error('必须提供 --file');
  if (!['json', 'markdown'].includes(args.format)) throw new Error('format 必须是 json 或 markdown');
  return args;
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function parseRecords(text) {
  const warnings = [];
  const records = [];
  text.split(/\r?\n/).forEach((line, index) => {
    if (!line.trim()) return;
    try {
      const item = JSON.parse(line);
      const predictedMinutes = Number(item.predictedMinutes);
      const actualMinutes = Number(item.actualMinutes);
      if (!Number.isFinite(predictedMinutes) || predictedMinutes <= 0 || !Number.isFinite(actualMinutes) || actualMinutes <= 0 || typeof item.actualSuccess !== 'boolean') {
        warnings.push({ line: index + 1, code: 'INVALID_RECORD', message: '缺少有效时间或 actualSuccess' });
        return;
      }
      let predictedP = item.predictedP;
      if (predictedP !== null && predictedP !== undefined) {
        predictedP = Number(predictedP);
        if (!Number.isFinite(predictedP) || predictedP < 0 || predictedP > 1) {
          warnings.push({ line: index + 1, code: 'INVALID_PROBABILITY', message: 'predictedP 无效，已忽略概率校准' });
          predictedP = null;
        }
      } else predictedP = null;
      records.push({ ...item, predictedMinutes, actualMinutes, predictedP });
    } catch (error) {
      warnings.push({ line: index + 1, code: 'INVALID_JSON', message: error.message });
    }
  });
  return { records, warnings };
}

function buildReport(records, warnings) {
  const ratios = records.map((r) => r.actualMinutes / r.predictedMinutes);
  const withProbability = records.filter((r) => r.predictedP !== null);
  const brier = withProbability.length
    ? withProbability.reduce((sum, r) => sum + (r.predictedP - (r.actualSuccess ? 1 : 0)) ** 2, 0) / withProbability.length
    : null;
  const actualSuccessRate = records.length ? records.filter((r) => r.actualSuccess).length / records.length : null;
  const predictedSuccessMean = withProbability.length ? withProbability.reduce((sum, r) => sum + r.predictedP, 0) / withProbability.length : null;
  const timeRatioMedian = median(ratios);
  const timeRatioMean = ratios.length ? ratios.reduce((a, b) => a + b, 0) / ratios.length : null;
  const suggestions = [];
  if (timeRatioMedian !== null) {
    if (timeRatioMedian > 1.25) suggestions.push(`历史实际耗时中位数是预测的 ${timeRatioMedian.toFixed(2)} 倍，后续 T 建议乘以该系数。`);
    else if (timeRatioMedian < 0.8) suggestions.push(`历史实际耗时中位数是预测的 ${timeRatioMedian.toFixed(2)} 倍，可适度下调 T。`);
    else suggestions.push('历史耗时预测基本校准，无需大幅调整。');
  }
  if (predictedSuccessMean !== null && actualSuccessRate !== null) {
    const gap = predictedSuccessMean - actualSuccessRate;
    if (gap > 0.15) suggestions.push(`成功概率平均高估 ${(gap * 100).toFixed(0)} 个百分点，建议下调 p 档位。`);
    else if (gap < -0.15) suggestions.push(`成功概率平均低估 ${(-gap * 100).toFixed(0)} 个百分点，建议上调 p 档位。`);
    else suggestions.push('成功概率均值与实际成功率接近。');
  }
  return {
    schemaVersion: 'scout.calibration.v1', version: VERSION, ok: records.length > 0,
    sampleSize: records.length,
    probabilitySampleSize: withProbability.length,
    actualSuccessRate,
    predictedSuccessMean,
    brierScore: brier,
    timeRatioMedian,
    timeRatioMean,
    suggestions,
    warnings
  };
}

function toMarkdown(report) {
  const pct = (v) => v === null ? '—' : `${(v * 100).toFixed(1)}%`;
  const num = (v) => v === null ? '—' : v.toFixed(3);
  const lines = [
    '# Scout Calibration', '',
    `- 样本数：${report.sampleSize}`,
    `- 有概率预测样本：${report.probabilitySampleSize}`,
    `- 实际成功率：${pct(report.actualSuccessRate)}`,
    `- 平均预测成功率：${pct(report.predictedSuccessMean)}`,
    `- Brier Score：${num(report.brierScore)}`,
    `- 实际/预测耗时中位数：${num(report.timeRatioMedian)}`,
    '', '## 建议'
  ];
  if (report.suggestions.length) report.suggestions.forEach((s) => lines.push(`- ${s}`));
  else lines.push('- 样本不足，暂不调整。');
  if (report.warnings.length) {
    lines.push('', '## Warnings');
    report.warnings.slice(0, 30).forEach((w) => lines.push(`- 第 ${w.line} 行 ${w.code}: ${w.message}`));
  }
  return `${lines.join('\n')}\n`;
}

try {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { console.log('Usage: node calibrate.mjs --file .scout/outcomes.jsonl [--format json|markdown]'); process.exit(0); }
  const text = fs.readFileSync(args.file, 'utf8');
  const { records, warnings } = parseRecords(text);
  const report = buildReport(records, warnings);
  if (args.format === 'markdown') process.stdout.write(toMarkdown(report));
  else process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  process.exit(report.ok ? 0 : 2);
} catch (error) {
  process.stdout.write(`${JSON.stringify({ schemaVersion: 'scout.calibration.v1', version: VERSION, ok: false, error: { code: 'CALIBRATION_FAILED', message: error.message }, warnings: [] }, null, 2)}\n`);
  process.exit(2);
}
