# 悟理 Evolve 分阶段路线

目标不是让系统频繁自动改自己，而是建立“证据足够才更新、任何策略都可回滚”的教学慢循环。

W3 从影子状态走到生产完成的历史顺序和一票否决项统一见
[`rag-completion-work-tree.md`](rag-completion-work-tree.md)。本文保留检索与慢循环的
原理和历史指标，不再充当当期执行清单。

## 当前阶段：观测地基

已具备 Evaluator、Candidate Archive、Knowledge Store、Agent Scheduler/Benchmark、RAG 证据注入和 RAG Effectiveness Report。观察报告按 `retrieved / empty / unavailable / legacy-no-rag` 分组，比较成功率、耗时、Evaluator、返修和批准结果。

当前报告是观察性分组，不是因果 A/B。线上不随机关闭 RAG；真正的有/无 RAG 对照应使用固定、教师已复核的测试题集。

答案质量使用 [`answer-quality-benchmark.md`](answer-quality-benchmark.md) 的三层成对评测：
同一模型直出只作 baseline，网页工作流产物是被测对象，教师最终复核答案才是真值；
结果必须按难度分层，不能用简单题平均分掩盖高难题退化。

上下文轻量化同样遵守两段门禁：先用 `evidence_budget_benchmark.py` 在至少 20 条教师批准样本上验证历史 evidence 的字符节省和必须事实保留；只有预检通过，才进入固定模型、固定 prompt 的成对答案评测。预检不会自动改变 evidence 预算，可逆语义压缩和按需取回协议在成对答案质量得到验证前不实现。

## 优先补齐：教师行为反馈地基

在调整检索算法或自动策略前，先把教师真实操作变成可靠监督信号：

1. 保存打回、手动编辑、重生成和最终批准之间的候选关联；
2. 将答案 diff 从行数升级为语义修改类别；
3. 自动推断“重做 / 大幅修改后采用 / 小幅修改后采用 / 原样采用”，保留依据和置信度，不要求教师重复打分；
4. 知识点、错因、难度等分类元数据保留为可编辑的 Agent 建议，不新增独立强制门禁；只有教师实际修改或最终批准形成的信号才进入稳定教学观测；
5. RAG/Evolve 使用最终复核版本的评价，不使用候选刚生成时的待审核流程分；
6. 教师原始 note 留在私有审计记录，跨题 evidence 只接收脱敏、结构化的 lesson。

具体工程缺口和验收项统一维护在 [`github-issues.md#零统一执行视图`](github-issues.md#零统一执行视图)。慢循环仍只能生成观察报告，不能把当前批准率或流程合规分当作解析内容质量。

## 当前进展与下一阶段：检索后端增强

不是立刻换向量库。先建立至少 30 条代表性检索查询，覆盖主要知识点、题型、错因和教师常用表达，并由教师标注相关条目。记录 FTS/标签基线后，仅在满足任一条件时升级：

- Recall@5 低于 85%；
- 同义表达或跨题型检索漏召回超过 15%；
- `empty/unavailable` 不是数据缺失，而是词面匹配失败；
- 教师连续反馈“明明有相似题但没检索到”。

截至 2026-07-25，本地固定集已有 30 条教师确认查询。加入教学标签独立文档和可审计查询扩展后，同一固定集的 Hit@5 从 0.8667 升至 1.0000、Recall@5 从 0.7208 升至 0.8292、MRR 从 0.7217 升至 0.8633；`teacher_phrase` Recall@5 从 0.4107 升至 0.6250。它证明本轮词面检索改动提高了召回，但总 Recall@5 仍低于 85% 门槛，因此继续按字段加权、规范词扩展和去重顺序改进，不据此宣称答案质量已经提升，也不直接跳到向量库。

三路 BM25（标签/题干/解析）、RRF 和查询计划诊断已经以影子策略接入统一
`query()` 接口。首版在同一固定集上的 Recall@5/MRR 为 `0.7875/0.8561`，低于
`baseline` 的 `0.8292/0.8633`，因此默认排序没有切换。下一轮先处理教师表达中的
任务噪声、路由候选过宽和跨路泛化匹配，再通过 `--ranking-policy multi-route`
复测；不得用实现完成替代指标门禁。

确定性意图视图的第二轮影子策略已完成：保留基线头部结果，用去除任务套话后的查询
补充尾部候选；在相同30条教师确认查询上 Recall@5/MRR 为 `0.8653/0.8650`，
`teacher_phrase` Recall@5 为 `0.7321`。该结果达到离线检索门槛，但尚未改变默认
`baseline`；同一固定集参与了规则校准，因此切换还需要新增教师确认查询批次不退化
并由教师显式确认。候选结果同时开始报告标签、题干、方法三个证据
槽位的覆盖与缺口，下一阶段再验证覆盖约束是否改善最终答案，而不是仅继续调检索分数。

W1 已把这一要求固化为评测契约：原 30 条固定集是 `calibration`；新增
`holdout` 至少 12 条、覆盖四类查询且具有独立批次标识。缺少 holdout 时
`policy_gate_ready=false`，即使 calibration 指标达标也不能切换策略。
Knowledge Store 同时输出物理条件相容性审计，候选 `precision-gated-v1`
会剔除跨领域、显式几何或求解目标冲突证据，并在没有可靠证据时降级为空证据。该策略仍只进入
`web-candidate` 离线答案评测，生产 baseline 不变。

首批独立 holdout 经教师批准后，baseline 与 intent-augmented 的 Recall@5/MRR
均为 `1.0000/0.7639`；条件审计对教师标注相关条目的保留率为 `1.0000`，
`policy_gate_ready=true`。multi-route Recall@5 为 `0.9167`，漏掉
`holdout-009`，继续保持实验状态。holdout 通过只允许进入下一阶段评测，
不自动切换任何生产策略。

W2 已完成主动证据集构建：`evidence-set-v2` 在 W1 精度门禁之后，按新增证据槽位、
路由多样性和单条精度选择引用，同时去除近重复并阻止领域、几何或求解目标相互冲突
的引用共存。独立 holdout 上最终相关证据保留率为 `1.0000`，近重复对和冲突对均为
`0`，`w2_evidence_set_ready=true`。中等、较难、挑战三档真实网页候选的关键结论
均与教师复核稿一致；其中较难题因无可靠历史证据安全降级为空集合。该结论只验收 W2
候选能力，生产 baseline 仍不切换。下一阶段应扩大各难度档答案样本，而不是立即引入
向量数据库或自动上线候选策略。

增强顺序：JSON 标签过滤和字段加权 → FTS 查询扩展与知识点归一化 → 混合排序与去重 → 本地向量检索。Neo4j/图检索只在跨题知识链、错因演化或多跳分析出现明确查询需求后引入。

W3 已完成生产验收：确定性复杂度初筛决定是否生成 `problem.decompose` 双层蓝图，
蓝图把物理过程和推理过程分开，并生成默认 3 路、最多 5 路定向召回需求；W2
证据集选择器统一融合后，Solver A 结构化求解，目标级风险审计器按预期收益决定
是否调用独立验证器，极高风险或冲突再调用盲解 Solver B，并按证据和可复算关系
仲裁。教师端只显示最多两张核对卡。`wuli-analysis-adaptive-v1` 现默认只把复杂题
送入 W3，低风险题和 W3 失败回退继续使用 W2；答案仍必须由教师批准。完整契约见
[`w3-reasoning-pipeline.md`](w3-reasoning-pipeline.md)。

2026-07-26 的冻结验收中，5 道校准题 16/16。首轮文本一致性评分把 5 道 holdout
记为 15/16；随后教师确认唯一分歧是原参考答案遗漏的整周期同相位合法分支，修订后
数学正确率为 16/16，并将该目标标为 `valid-supplement`。由于参考答案修订由本轮
影子输出触发，该 holdout 不再满足独立性：`accuracy_non_regression=true`，但
`independent_holdout_intact=false`、`fresh_holdout_required=true`，所以
`production_eligible=false`。下一版本必须建立新的未见真值集，不能用修订后的本轮
holdout 证明泛化。

W4-1 已把新鲜批次改为“真值先冻结、影子后运行”：所有曾进入旧 W3 manifest 的题
一律排除；新 manifest 生成后，教师必须先批准至少 5 题、12 个目标的结构化真值，
再生成不可覆盖的 `truth-lock.json`。答案、目标真值或锁摘要发生变化都会失败关闭。
这段“当前 14 道、尚缺 5 道”的记录是 W4-1 当时的历史停点；随后已经建立并冻结
5 道新鲜题、15 个目标，完成独立 holdout、生产灰度和 W2 回滚验收。

教师已允许选择旧题作回放控制。首批 5 题、15 个目标的 `replay` 结果为 W3
`15/15`，平均内部调用 4.4、平均教师核对卡 0.4；旧 W3 报告中的 baseline 是教师
复核答案，不再冒充 W2 生成成绩。真实 W2/W3 成对候选由 W4-2 单独生成。回放结果
不计入独立 holdout，生产资格仍为 false。

W4-2 的首个真实网页配对固定为 `expert` 路由、`codex-visualization` 模型和 candidate
evidence。初次诊断暴露的 compact LaTeX、隐藏控制字符、决定性关系长度上限和失败
重跑覆盖问题均已修复。全部 5 道 replay、15 个冻结目标完成真实配对后，W2 为
14/15（93.33%），W3 为 15/15（100%），W2 可交付性通过 3/5。唯一准确率差异是
三维电磁运动题中曾由旧 W3 推动教师补充的同相位分支，因此不能视作独立泛化增益；
另一道双区域磁场题虽结论正确，但首次性证明未完整展示两个中间时段的位置式。
W4-2 已完成回放诊断，下一硬门槛仍是新的未见 holdout。

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

教师确认必须绑定当前最新的已记录且确实包含策略建议的周报，旧确认不会授权后续新报告：

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
