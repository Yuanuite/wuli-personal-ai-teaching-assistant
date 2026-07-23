# 悟理 Evolve 分阶段路线

目标不是让系统频繁自动改自己，而是建立“证据足够才更新、任何策略都可回滚”的教学慢循环。

## 当前阶段：观测地基

已具备 Evaluator、Candidate Archive、Knowledge Store、Agent Scheduler/Benchmark、RAG 证据注入和 RAG Effectiveness Report。观察报告按 `retrieved / empty / unavailable / legacy-no-rag` 分组，比较成功率、耗时、Evaluator、返修和批准结果。

当前报告是观察性分组，不是因果 A/B。线上不随机关闭 RAG；真正的有/无 RAG 对照应使用固定、教师已复核的测试题集。

## 下一阶段：检索后端增强

不是立刻换向量库。先建立至少 30 条代表性检索查询，覆盖主要知识点、题型、错因和教师常用表达，并由教师标注相关条目。记录 FTS/标签基线后，仅在满足任一条件时升级：

- Recall@5 低于 85%；
- 同义表达或跨题型检索漏召回超过 15%；
- `empty/unavailable` 不是数据缺失，而是词面匹配失败；
- 教师连续反馈“明明有相似题但没检索到”。

增强顺序：JSON 标签过滤和字段加权 → FTS 查询扩展与知识点归一化 → 混合排序与去重 → 本地向量检索。Neo4j/图检索只在跨题知识链、错因演化或多跳分析出现明确查询需求后引入。

每次后端替换必须继续通过统一 `build_agent_evidence()` 接口，Agent Gateway 和教师页面不感知具体实现。

当前已提供固定集工具 `teacher-console/scripts/retrieval_benchmark.py`。`seed` 只根据 canonical 条目元数据生成 `draft` 草稿，不能冒充教师标注；教师可在工作台顶部打开“检索评测”，通过原题图、题干摘要和标签卡片勾选所有真正相关条目，再逐条批准。网页与命令行共享同一 JSONL 真源。评测数据保存在被 Git 忽略的私有题库内，仓库只保留不含真实条目的格式示例。

```bash
# 一次性生成 30 条本地草稿
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library seed --limit 30

# 在教师工作台逐条复核后检查标签完整性
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library validate

# 草稿探索结果，不得触发策略更新
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library run --include-draft --format markdown

# 只评测 approved 固定集；达到 30 条后才允许 --record
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library run --format markdown --record
```

报告给出 Hit@k、Recall@k、MRR、空结果率和按四类查询拆分的指标。`fixed_set_ready=false` 或 `threshold_evaluable=false` 时，`upgrade_recommended` 必须保持 false；这表示证据不足，而不是检索已经合格。

## 再下一阶段：慢循环分析与策略更新

慢循环代码骨架可以提前建，但策略不得在样本不足时自动生效。

- 只读周报：累计至少 20 次带 RAG 的已完成 Agent 任务，并有至少 10 次教师最终复核结果；
- 形成策略建议：同一任务类型的对照组各至少 10 个样本，且跨至少两个教学批次；
- 调整默认检索预算或模型路由：连续两期报告方向一致，固定测试集不退化，教师明确确认；
- 自动应用低风险策略：至少 50 个教师闭环样本，有版本化策略、回滚点、上限约束和 canary。

慢循环优先调整 evidence top-k/字符预算、任务到模型/provider 的默认映射、超时/重试/并发建议和高频失败对应的提示与验证器。它永不自动放开教师批准、答案真源、物理语义、公开发布或 GitHub 推送。

当前 `teacher-console/scripts/slow_loop_report.py` 已提供只读骨架。它实时组合固定检索评测、RAG 教师闭环和 Agent 调度基准；样本不足时只列缺口。教学质量线仍要求 20 个已完成 RAG 任务和 10 个教师闭环；可靠性线则把成功与失败的终态作业都视为观察样本，达到 5 个终态任务且至少 1 个结构化失败即可记录只读排障观察。调度诊断至少需要 5 个同类作业，并忽略无法归因的 `unknown_failed`。教师闭环只统计明确批准或教师发起返修，不把 Agent 自身失败当作教师复核。

```bash
python3 teacher-console/scripts/slow_loop_report.py \
  --library student-error-library --format markdown
```

只有达到 20 个已完成 RAG 任务和 10 个教师闭环后，才允许用 `--record` 保存周报。保存周报仍不会应用建议。默认策略变更还需要同任务双 cohort、两个教学批次、连续两期同方向、固定集不退化和教师显式确认；自动应用执行器目前故意不存在。

教师确认必须绑定最近一次已记录且确实包含策略建议的周报，旧确认不会授权后续新报告：

```bash
python3 teacher-console/scripts/slow_loop_report.py \
  --library student-error-library --confirm-strategy \
  --reviewer "李老师" --note "同意进入离线试验，不直接上线"
```

该动作只追加 `evolve.strategy.confirm` 审计事件，`applies_policy=false`。

## 当前命令

只读生成观察报告：

```bash
python3 teacher-console/scripts/rag_effectiveness_report.py \
  --library student-error-library --format markdown
```

显式沉淀到 Candidate Archive 和 Knowledge Store：

```bash
python3 teacher-console/scripts/rag_effectiveness_report.py \
  --library student-error-library --format markdown --record
```

默认最小样本数为每组 10。只有同一任务类型的 `retrieved` 与 `legacy-no-rag` 两组都达到门槛时，`comparison_ready` 才会为 `true`。
