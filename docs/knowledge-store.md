# Wuli Knowledge Store

Knowledge Store 是悟理的本地派生检索层，用来把题库、Evaluator 和 Candidate Archive 聚合成可给 AI 使用的 evidence pack。它不是新的真源；删除数据库后可以从 `student-error-library/entries/`、`evaluation.json` 和 `candidate-archive.jsonl` 完整重建。

## 存储位置

默认数据库：

```text
student-error-library/indexes/wuli-memory.db
```

它属于私有题库索引，不应提交到公开仓库。当前实现只使用 Python 标准库 `sqlite3`，启用 SQLite WAL，并优先使用 FTS5 做文本检索；若当前 SQLite 不支持 FTS5，会降级为本地扫描。

## 数据来源

| 表 | 来源 | 用途 |
|---|---|---|
| `entry` | `record.json` | 条目标题、状态、科目、文件夹、知识点、错因 |
| `document` / `document_fts` | `record.json` 教学标签、`problem.md`、答案 Markdown、`source-review.md`、`physics-model.json` | 标签、题干、解析与模型的可引用文本证据 |
| `evaluation` | `evaluation.json` | 结构、复核、可视化、交付和安全检查摘要 |
| `candidate_event` | `candidate-archive.jsonl` | 教师反馈、Agent 候选、构建、发布和交付历史 |
| `teaching_memory` | `record.json` + `physics-model.json` | 知识点、错因、难度、二级结论和是否可视化 |
| `scheduler_benchmark` | 全库级 `scheduler.benchmark` Candidate Archive 事件 | Agent 调度、provider、耗时、失败类型和 token 用量基准 |
| `evolve_observation` | 全库级 `evolve.observation.*` Candidate Archive 事件 | RAG 效果观察和后续慢循环只读报告 |
| `evidence_unit` | 教师批准解析、批准物理模型中的结构化教学项、带条件技巧库 | Evidence Agent Phase-B shadow 投影；不参与当前生产 `query()` 排序 |

## 手动命令

重建：

```bash
python3 .claude/skills/manage-student-error-library/scripts/knowledge_store.py \
  --library student-error-library rebuild
```

查询并返回 evidence pack：

```bash
python3 .claude/skills/manage-student-error-library/scripts/knowledge_store.py \
  --library student-error-library query "动量守恒 非弹性碰撞" --mode teaching --top-k 5
```

`kb.py rebuild`、`validate`、`finalize` 和生命周期中触发的索引刷新会顺带刷新 Knowledge Store；SQLite 失败时只记录 `knowledge_store.status=skipped`，不阻断原有交付流程。

Schema 初始化/迁移只在显式重建路径执行。`query()` 使用 SQLite `mode=ro` 和
`query_only`，缺库、旧 schema 或不完整库返回 `unavailable`，不会创建或修改文件。
Candidate Archive 追加后写入 freshness dirty marker；查询会明确返回 `stale`，Agent evidence
拒绝使用陈旧索引，直到显式批量刷新完成。`candidate_event` 保存脱敏反馈类别、关联和结果摘要。
知识点、错因、难度与年级保留为 Agent 建议；为保证主流程流畅度，不设置独立强制确认门禁。

## Evidence pack 结构

查询返回 JSON，核心字段包括：

- `results[].matched_documents`：命中的题干/答案/模型片段，可作为引用证据；
- `query_expansions`：教师自然表述到题库规范词的确定性扩展，便于审计实际检索意图；
- `query_plan`：本次查询使用的原始表达、扩展表达、检索 token 和独立路由；
- `retrieval`：实际生效策略、词法后端、RRF 参数、各路候选数与影子策略前列条目；
- `results[].route_matches`：条目在标签、题干和解析路由中的名次、原始分和 RRF 贡献；
- `results[].evidence_audit`：候选的共享词、命中依据、共同适用条件、显式冲突条件、
  精度分和 `accepted/rejected-low-precision` 影子决策；
- `evidence_set`、`results[].evidence_coverage`：候选证据集合对标签、题干和方法槽位的覆盖、缺口、重复与来源可回溯计数；
- `results[].knowledge_points`、`error_types`：Agent 建议且可由教师修改的教学标签；
- `results[].evaluation`：当前条目的质量评分、失败项和教师复核要求；
- `results[].recent_events`：近期教师/Agent/构建/发布事件，用于避免重复犯同一类错误；
- `scheduler_benchmarks`：近期的全库 Agent 调度基准，用于判断自动模式、并发和 provider 策略；
- `evolve_observations`：近期的 RAG/策略效果观察报告，用于判断样本是否足够；
- `required_checks`：提示下游 AI 必须基于证据回答，不能把检索结果当成审批。

## Agent 证据注入

首次解析 `analysis.generate`、答案返修 `answer.revise` 和可视化建模 `visualization.model` 在任务构造时调用 `build_agent_evidence()`，把裁剪后的结果作为只读 `.agent-context/knowledge-evidence.json` 放入 Gateway 隔离候选区。当前条目经教师复核的题干、当前答案和教师本轮要求始终优先，历史证据只能用于召回可迁移的高中方法、易错点、适用条件和既往失败教训。首次解析仍须独立验算，不能复制历史答案。

证据包遵守以下边界：

- 排除当前条目，避免把旧版本答案当成外部佐证；
- 不包含内部 entry ID、文件夹名、数据库路径、事件 ID、原图或完整 Candidate Archive；
- 只保留相似题标题、知识点、错因、方法、二级结论、Evaluator 警告/失败摘要、匹配片段和近期教训；
- 经济模式最多 2 条、约 3500 字符，其他模式最多 4 条、约 9000 字符；
- evidence pack 写入 `context_budget`，记录请求上限、实际序列化字符数、候选/纳入/省略引用数和是否截断；引用带内容哈希，便于比较同一证据版本，但当前不提供模型自行取回原文的工具；
- 预算策略只作用于历史 evidence；当前题干、当前答案和教师本轮指令不在该 pack 内，不能被 evidence 裁剪逻辑修改；
- Knowledge Store 缺失或查询失败时返回 `status=unavailable`，不触发全库重建，也不阻塞 Agent 主任务。
- Knowledge Store freshness 为 `stale` 时同样返回不可用，不能把旧证据冒充当前证据。

作业结果只记录 `evidence_context.status/reference_count/task_type`，用于后续比较“有检索/无检索”的成功率和返修次数；具体证据内容不会进入作业公开结果。首次解析检查点还绑定 evidence pack 摘要，检索内容变化后不会误用旧生成结果。

观察报告由 `teacher-console/scripts/rag_effectiveness_report.py` 生成。它把作业记录中的耗时/用量与 Candidate Archive 中的 Evaluator、教师返修和最终批准关联起来；默认只读，显式 `--record` 才沉淀为 `evolve.observation.rag`。报告属于观察性证据，不能单独证明 RAG 导致结果变好。

确定性 evidence 预算先用只读预检验证，不直接上线语义压缩。教师按 [`evidence-budget-eval.example.jsonl`](../student-error-library/config/evidence-budget-eval.example.jsonl) 建立至少 20 条 `approved` 样本，为每条查询标注必须保留的历史事实，然后运行：

```bash
python3 teacher-console/scripts/evidence_budget_benchmark.py \
  --library student-error-library \
  --candidate-chars 8000 \
  --format markdown
```

预检比较 20,000 字符基线与候选预算，要求必须事实保留率 100%、候选全部不超预算且序列化字符中位节省至少 25%。通过只表示可以进入固定模型、固定 prompt 的成对答案评测；脚本不调用模型、不写 Candidate Archive，也不能证明教学质量没有下降。草稿样本即使结果良好也不能授权策略变化。

固定检索集由 `teacher-console/scripts/retrieval_benchmark.py` 管理，默认文件为 `student-error-library/evals/retrieval-cases.jsonl`。它复用同一个 `query()` 接口，因此可以在不改 Gateway 和页面的情况下比较后续 FTS、标签、混合排序或向量后端。机器生成的 `draft` 只可用于探索；至少 30 条教师核对并标记为 `approved` 的查询才允许把聚合结果记录为 `evolve.observation.retrieval`。持久事件不保存查询正文、相关条目 ID 或逐题结果，只保存聚合指标和漏召回 case ID。

评测必须固定同一批 approved case 做前后对照，并同时报告 Hit@k、Recall@k、MRR、空结果率和分类指标。Hit@5=1 仍可能漏掉一个查询对应的其他相关条目，因此不能替代 Recall@5；教师自然语言召回改善也应单独看 `teacher_phrase`。这些指标只评价检索，不评价最终解析内容；答案质量需另做固定模型、固定 prompt 的成对生成，并关联教师返修与批准结果。

### 多路召回的影子门禁

当前 `query()` 会并行计算三条可审计的词法路由：

1. `metadata`：知识点、错因、方法和二级结论；
2. `problem`：题干与原文复核；
3. `solution`：学生版、教师版、统一解析和物理模型。

三路在条目层使用 RRF 融合，但默认 `ranking_policy=baseline`，继续以已经通过固定集验证的单池 BM25 排序作为 Agent evidence 真正结果；多路排名只作为影子诊断返回。不得因为多路实现已经存在就自动激活。

可以用同一固定集显式比较：

```bash
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library run \
  --ranking-policy baseline --format markdown

python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library run \
  --ranking-policy multi-route --format markdown

python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library run \
  --ranking-policy intent-augmented --format markdown
```

截至 2026-07-25，基线 Recall@5/MRR 为 `0.8292/0.8633`，首版多路策略为
`0.7875/0.8561`。因此多路框架保留为实验能力，生产排序不变。只有固定集
Recall@5 至少达到 `0.85`、MRR 不低于当前基线且分类指标无明显退化后，才允许
经教师确认切换默认策略。

第二轮增加了确定性意图视图：移除“帮我找一道、相关题目”等任务套话，保留稳定
基线前三名，再从意图视图补入最多两条不重复候选；“反复进出”等经过固定集验证的
教师表达会扩展为多区域、周期轨迹、有界磁场和轨迹衔接。该
`intent-augmented` 策略在同一固定集上达到 Recall@5 `0.8653`、MRR `0.8650`，
`teacher_phrase` Recall@5 从 `0.6250` 提升到 `0.7321`，已达到离线指标门槛。
由于同一固定集参与了本轮规则校准，这组结果不是独立 holdout 证明；它仍保持
`experimental`，需新增教师确认查询批次不退化并经教师确认后，才能成为
`build_agent_evidence()` 默认排序。

`build_agent_evidence()` 的输出现在明确标记为 `candidate-evidence-set`，并记录
引用集合对 `concepts-and-labels`、`problem-context`、`solution-method` 三个槽位
的覆盖情况。这是诊断与后续集合选择的地基；当前不会为了填满槽位强行加入低相关证据。

W1 增加 `precision-gated-v1` 候选证据选择。它使用确定性物理条件词表审计领域、
场序列、边界几何和求解目标：每条引用都说明“为什么命中、哪些条件可迁移、哪些条件
冲突”。跨领域、显式边界几何或求解目标冲突会被候选策略剔除；若全部候选均不可靠，证据包返回
`degraded-empty-low-precision`，让模型独立求解，而不是用低精度历史题填满上下文。
该策略只供 `web-candidate` 离线成对评测，生产 `build_agent_evidence()` 仍默认
`selection_policy=baseline`。

W2 在精度门禁之后增加 `evidence-set-v2` 主动集合选择。选择器按“单条精度 +
新增槽位覆盖 + 检索路由多样性”计算确定性效用，逐条选择能共同回答问题的引用；
标题相同或正文 token Jaccard 至少 `0.88` 的近重复引用会被去除，领域、边界几何
或求解目标存在硬冲突的引用不会同时进入集合。输出会记录每一步的
`selected / rejected-near-duplicate / rejected-inter-reference-conflict /
omitted-context-budget` 决策，区分“策略选中”与“最终受上下文预算物化”，便于审计。
若精度门禁后无可靠候选，仍安全降级为空证据。

独立 holdout 使用 baseline 排名与 `evidence-set-v2` 选择时，Recall@5 为
`1.0000`、MRR 为 `0.7639`；教师标注相关证据在精度门禁和最终选择后的保留率均为
`1.0000`，选中集合的近重复对与硬冲突对均为 `0`，因此
`w2_evidence_set_ready=true`。该门禁只证明候选集合构建可进入答案影子评测，
不改变生产 `selection_policy=baseline`。

固定检索集从 W1 起区分 `evaluation_split=calibration|holdout`。旧 30 条记录在缺少
该字段时按 calibration 兼容读取；它们可用于调规则，但不能再单独授权策略切换。
独立 holdout 至少需要 12 条教师批准查询、覆盖四类查询，并带非空 `batch_id`。
holdout 查询不得与 calibration 查询重复。可从尚未参与 calibration 的条目追加草稿：

```bash
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library seed --append --limit 12 \
  --evaluation-split holdout --batch-id holdout-2026-08-a

python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library run --evaluation-split holdout \
  --ranking-policy intent-augmented --format markdown
```

只有 `calibration_ready=true`、`holdout_ready=true`、
`evidence_gate_ready=true` 和 `policy_gate_ready=true` 同时成立，独立批次指标
才可支持策略变更。`evidence_gate_ready` 要求教师标注的相关条目在条件审计后保留率
为 100%，防止检索指标正常但精度门禁误删有效证据。

W3 的 `build_blueprint_evidence()` 不再把整道复杂题压成一次查询。它读取拆题蓝图中
按目标与物理阶段聚类的 `retrieval_needs`，默认执行 3 路；第 4、5 路只有在带来新
目标覆盖或新候选时继续，连续两路无增益即停止。多路候选最后只调用一次
`evidence-set-v2`，因此查询数可以自适应增加，但证据字符预算、精度门禁、去重和
冲突处理不会放宽。该策略目前只用于 W3 影子报告。

### Evidence Unit shadow 投影

`wuli-evidence-unit-shadow-v1` 在显式 Knowledge Store rebuild 时生成独立
`evidence_unit` 表，当前不接入 `query()` 或 W3 生产路由。独立
`evidence_agent_shadow.py` 可通过只读连接把它送入 Agent Gateway 影子任务。投影只接受：

- 当前答案摘要仍有效的教师批准解析中的“最短主线”“带适用条件的二级结论”
  “易错点”和“教师审计”；
- 当前可视化批准仍有效的 `physics-model.json` 中带条件结构化教学项；
- 带 `conditions` 与 `forbidden` 的本地教师维护技巧库。

每条 Evidence Unit 固定保存：

```text
evidence_id
+ unit_kind
+ source_kind / authority_level
+ source_locator（库内相对路径、章节、行号）
+ text
+ physics_facets
+ applicability
+ exceptions
+ content_hash
```

适用性优先于权威等级；Routing Summary 不允许进入 Evidence Set。未批准答案、批准摘要
已经失效的答案和当前条目均可在读取投影时排除。投影读取使用只读连接；索引 stale、
表缺失或投影版本不一致时返回 `unavailable`。

该投影是 fail-soft 的派生能力：单条来源不合格只增加
`evidence_unit_projection_errors`，不得阻断原有 Knowledge Store 重建。2026-07-30
首次正式派生索引重建生成 257 条 shadow Evidence Unit，投影错误为 0；生产 baseline
检索指标保持不变。

## 和 RAG / Evolve 的关系

当前层解决“可靠取证”和“低成本检索”，不负责自动生成优化方案。后续接入顺序建议：

1. Agent Gateway 已在首次解析、答案返修和可视化建模前接收裁剪后的 evidence pack；
2. Evaluator 继续给每次产物打确定性分；
3. Candidate Archive 记录教师反馈与候选结果；
4. Evolve 循环只在以上证据齐全时比较候选，不直接修改 canonical 文件或审批状态。

这样可以先获得 RAG 的稳定收益，再逐步叠加候选生成、评分和回滚机制。
