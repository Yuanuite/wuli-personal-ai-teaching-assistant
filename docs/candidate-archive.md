# 悟理 Candidate Archive

Candidate Archive 是悟理“教学进化系统”的第二块轻基建。Evaluator 回答“当前结果好不好”，Candidate Archive 记录“它是怎么变成这样的”。

它采用追加式 JSONL，不引入数据库：

```text
student-error-library/entries/<entry-id>/candidate-archive.jsonl
student-error-library/indexes/candidate-archive.jsonl
```

## 当前记录范围

第一版记录这些事件：

| 来源 | 事件 |
|---|---|
| 教师 | 来源批准、答案保存、答案批准、答案返修请求、可视化批准、公开题图/发布确认 |
| 系统 | 可视化确定性构建、最终交付、公开预览 |
| Agent | 题干整理、解析生成、答案返修、可视化模型生成/修复的完成或失败结果 |
| 调度器 | 全库级 `scheduler.benchmark` 基准事件，记录批量作业耗时、并发、provider/model 分布和失败类型 |
| Evolve 观察器 | 全库级 `evolve.observation.rag`、`evolve.observation.retrieval` 与 `evolve.observation.slow-loop`，分别记录 Agent 检索 cohort、固定检索集聚合指标和证据门禁后的只读策略周报 |

Agent 完成或失败事件同时保存统一的 `outcome` 摘要，包括执行阶段、provider、尝试次数、错误分类、回退状态及可用的 token/时延指标。归档只保留隐私最小化后的错误摘要，不保存完整 prompt、证据正文、密钥、环境变量或系统临时候选路径。

事件 ID 使用 UUID；`links` 把 Agent 候选、教师反馈/编辑与最终批准串联。答案保存会写入
`answer.save`，语义差异覆盖新增、删除和二进制解释图，并归类物理纠错、推理、教学、
分类、可视化与格式变化。采用等级由修改比例、打回次数和关键纠错自动推断，附依据与置信度。

每条事件包含：

```json
{
  "event_id": "...",
  "entry_id": "...",
  "task_type": "answer.revise",
  "actor": "agent",
  "event_type": "agent-result",
  "status": "failed",
  "raw_status": "failed",
  "changed_files": [],
  "failure_reasons": [],
  "request": {},
  "result": {},
  "evaluation": {},
  "feedback": {"categories": ["physics-correction"]},
  "links": {"candidate_event_id": "..."}
}
```

## 隐私与边界

- 不保存 API Key、Authorization、token、password、secret 等敏感字段；这些值会写成 `[redacted]`。
- 不复制原始题图、PDF、HTML 或完整候选文件内容。
- 长文本会截断，只保留摘要、状态、变更文件、失败原因和 Evaluator 摘要。
- 教师原始 note 只保留在单题私有 Archive；全库 Archive 删除正文，只保留结构化类别和私有定位。
- Archive 不批准、不发布、不改变 canonical 条目；它只是事件记录。
- 全库级事件使用 `entry_id="__library__"`，只写入 `indexes/candidate-archive.jsonl`，不写入任何单题目录。
- 固定检索集事件不保存查询正文、相关条目 ID 或逐题检索结果，只保存聚合指标和漏召回 case ID。

## 与 Evaluator / RAG / Evolve 的关系

```text
Evaluator = 体检报告
Candidate Archive = 病历本 / 成长档案
```

后续题库 RAG 和 AI 审计 RAG 可以检索：

- 哪类题目经常返修；
- 哪个模型在哪类任务上失败多；
- 哪些可视化类型最容易构建失败；
- 教师最常修改答案的哪些部分；
- 哪些 failure_reason 应该进入下次 Agent 的避坑上下文。

这让悟理的 evolve 闭环从“当前体检”推进到“历史复盘”：

```text
生成候选 → 自动评价 → 教师复核 → 记录成败 → 检索历史 → 下次改进
```
