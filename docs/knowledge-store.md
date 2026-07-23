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
| `document` / `document_fts` | `problem.md`、答案 Markdown、`source-review.md`、`physics-model.json` | 题干/解析/模型的可引用文本证据 |
| `evaluation` | `evaluation.json` | 结构、复核、可视化、交付和安全检查摘要 |
| `candidate_event` | `candidate-archive.jsonl` | 教师反馈、Agent 候选、构建、发布和交付历史 |
| `teaching_memory` | `record.json` + `physics-model.json` | 知识点、错因、难度、二级结论和是否可视化 |
| `scheduler_benchmark` | 全库级 `scheduler.benchmark` Candidate Archive 事件 | Agent 调度、provider、耗时、失败类型和 token 用量基准 |
| `evolve_observation` | 全库级 `evolve.observation.*` Candidate Archive 事件 | RAG 效果观察和后续慢循环只读报告 |

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

查询会先执行增量 schema 检查，只补充新版本缺少的表和索引，不删除、不重建已有数据；因此旧版派生数据库升级后也能立即检索。完整数据刷新仍由 `kb.py rebuild` 负责。

## Evidence pack 结构

查询返回 JSON，核心字段包括：

- `results[].matched_documents`：命中的题干/答案/模型片段，可作为引用证据；
- `results[].knowledge_points`、`error_types`：教学分析的稳定标签；
- `results[].evaluation`：当前条目的质量评分、失败项和教师复核要求；
- `results[].recent_events`：最近教师/Agent/构建/发布事件，用于避免重复犯同一类错误；
- `scheduler_benchmarks`：最近的全库 Agent 调度基准，用于判断自动模式、并发和 provider 策略；
- `evolve_observations`：最近的 RAG/策略效果观察报告，用于判断样本是否足够；
- `required_checks`：提示下游 AI 必须基于证据回答，不能把检索结果当成审批。

## Agent 证据注入

答案返修 `answer.revise` 和可视化建模 `visualization.model` 在任务构造时调用 `build_agent_evidence()`，把裁剪后的结果作为只读 `.agent-context/knowledge-evidence.json` 放入 Gateway 隔离候选区。当前条目经教师复核的题干、答案和教师本轮要求始终优先，历史证据只能用于核对方法、易错点、适用条件和既往失败教训。

证据包遵守以下边界：

- 排除当前条目，避免把旧版本答案当成外部佐证；
- 不包含内部 entry ID、文件夹名、数据库路径、事件 ID、原图或完整 Candidate Archive；
- 只保留相似题标题、知识点、错因、方法、二级结论、Evaluator 警告/失败摘要、匹配片段和近期教训；
- 经济模式最多 2 条、约 3500 字符，其他模式最多 4 条、约 9000 字符；
- Knowledge Store 缺失或查询失败时返回 `status=unavailable`，不触发全库重建，也不阻塞 Agent 主任务。

作业结果只记录 `evidence_context.status/reference_count/task_type`，用于后续比较“有检索/无检索”的成功率和返修次数；具体证据内容不会进入作业公开结果。首个版本只注入返修和可视化任务，待 Evaluator 数据证明收益后再决定是否扩展到首次解析。

观察报告由 `teacher-console/scripts/rag_effectiveness_report.py` 生成。它把作业记录中的耗时/用量与 Candidate Archive 中的 Evaluator、教师返修和最终批准关联起来；默认只读，显式 `--record` 才沉淀为 `evolve.observation.rag`。报告属于观察性证据，不能单独证明 RAG 导致结果变好。

固定检索集由 `teacher-console/scripts/retrieval_benchmark.py` 管理，默认文件为 `student-error-library/evals/retrieval-cases.jsonl`。它复用同一个 `query()` 接口，因此可以在不改 Gateway 和页面的情况下比较后续 FTS、标签、混合排序或向量后端。机器生成的 `draft` 只可用于探索；至少 30 条教师核对并标记为 `approved` 的查询才允许把聚合结果记录为 `evolve.observation.retrieval`。持久事件不保存查询正文、相关条目 ID 或逐题结果，只保存聚合指标和漏召回 case ID。

## 和 RAG / Evolve 的关系

当前层解决“可靠取证”和“低成本检索”，不负责自动生成优化方案。后续接入顺序建议：

1. Agent Gateway 已在答案返修和可视化建模前接收裁剪后的 evidence pack；首次解析和审计建议是否接入由后续 Evaluator 数据决定；
2. Evaluator 继续给每次产物打确定性分；
3. Candidate Archive 记录教师反馈与候选结果；
4. Evolve 循环只在以上证据齐全时比较候选，不直接修改 canonical 文件或审批状态。

这样可以先获得 RAG 的稳定收益，再逐步叠加候选生成、评分和回滚机制。
