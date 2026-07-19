# Scout

Scout 是一个面向代码项目的“下一步决策 + 质量保障”Skill。它不会盲目列 TODO，而是通过只读项目侦查、证据分层、双轨排序和可验证的 Decision Cards，帮助开发者优先处理阻断、质量盲区与最危险的技术假设。

## 核心价值

- **先证据，后建议**：Git、项目清单、测试/CI 结构、文档和风险信号均由只读探针采集。
- **双轨决策**：降风险任务使用失败发现率，执行任务使用影响/成本与复利价值，避免用一个公式套所有工作。
- **可审计输出**：每条建议包含证据、最小行动、成功条件、停止条件和置信度。
- **安全默认**：不读取秘密内容，不自动安装依赖，不自动执行仓库脚本，不联网，不写项目。
- **稳定降级**：非 Git、空项目、缺工具、权限错误、异常输入均返回结构化警告而非崩溃。
- **可持续校准**：经用户授权可记录预测与实际结果，改善后续概率和耗时估计。

## 目录结构

```text
scout/
├── SKILL.md
├── README.md
├── CHANGELOG.md
├── LICENSE
├── VERSION
├── scripts/
│   ├── scout_probe.mjs
│   ├── rank_actions.mjs
│   ├── outcome_log.mjs
│   ├── calibrate.mjs
│   ├── compute_lambda.py
│   └── self_test.mjs
├── references/
│   ├── decision-model.md
│   ├── security.md
│   ├── tool-orchestration.md
│   ├── examples.md
│   └── evaluation.md
├── schema/
│   ├── candidate.schema.json
│   └── outcome.schema.json
├── tests/
├── evals/
└── examples/
```

## 环境要求

Skill 本体无强制运行时依赖，可在没有脚本执行权限时使用文件/Git 工具降级运行。可选辅助能力：

- Node.js 18+：运行只读探针、排序器、结果日志与评测脚本；
- Python 3.9+：仅用于兼容版 `compute_lambda.py`；
- Git：可选；没有 Git 时自动降级。

## AstronClaw / OpenClaw 安装

Skill 根目录必须直接包含 `SKILL.md`。解压后将整个 `scout` 目录安装到工作区 Skill 目录，或使用本地目录安装：

```bash
openclaw skills install ./scout --as scout
openclaw skills list --eligible
openclaw skills info scout
```

新会话中调用：

```text
/scout
/scout quick
/scout deep
/scout goal="提升发布前质量"
```

Skill 内部资源统一通过 `{baseDir}` 引用，不依赖 `.claude/skills` 等硬编码路径。

## 本地自测

```bash
node scripts/self_test.mjs
```

单独测试探针：

```bash
node scripts/scout_probe.mjs --path . --mode deep --format json
```

单独测试排序器：

```bash
node scripts/rank_actions.mjs --input examples/sample_candidates.json --format markdown
```

兼容版 λ 计算器：

```bash
printf '验证方案\t0.4\t20\n' | python3 scripts/compute_lambda.py --unit hour
```

## 安全说明

Scout 默认不执行项目测试、构建、安装或部署命令，因为这些命令可能包含任意代码。探针只读取受限的项目元数据和文本信号，并跳过 `.env`、密钥、私钥、依赖目录、构建产物、超大文件和符号链接目标。

详见 `references/security.md`。

## 评测

`evals/cases.jsonl` 提供覆盖非 Git、空项目、脏工作区、测试盲区、异常候选输入、安全边界等场景的评测集。推荐指标：

- 稳定调用率
- 输入校验通过率
- 有证据建议占比
- 专家 Top-1 一致率
- 未授权副作用率
- 估计校准误差

运行静态评测：

```bash
node evals/run_evals.mjs
```

## 设计边界

失败发现率不是严格的信息熵，也不适用于所有任务。Scout 只把它用于同一严重度下的最小风险验证；普通交付任务使用影响/成本与复利价值排序。任何概率和耗时均为估计，必须与测量事实分开呈现。

## 许可证

MIT
