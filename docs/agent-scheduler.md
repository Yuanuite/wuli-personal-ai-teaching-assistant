# Agent Scheduler

Agent Scheduler 是悟理后台 Agent 作业的第一层调度策略。它不替代 Agent Gateway 的候选隔离、校验和提升；它只决定任务何时运行、同类任务最多并发多少、以及当前任务的默认优先级。

## 配置位置

默认配置文件：

```text
student-error-library/config/agent-scheduler.json
```

该文件是本地运行配置，服务启动时读取；不存在时教师端会写入默认配置。不要把它用于保存密钥，也不要发布到学生端。

默认值：

```json
{
  "schema_version": 1,
  "global_max_running": 6,
  "entry_max_running": 1,
  "kind_limits": {
    "source.clean": 4,
    "analysis.generate": 4,
    "answer.revise": 4,
    "visualization.model": 4
  },
  "kind_priorities": {
    "source.clean": 70,
    "analysis.generate": 60,
    "answer.revise": 80,
    "visualization.model": 50
  },
  "provider_limits": {}
}
```

## 调度规则

- 同一条目永远只能有一个 Agent 作业处于 `queued` 或 `running`；
- worker 只领取当前可运行的任务，不会让正在等待 kind/provider 限额的作业占住 worker；
- `kind_limits` 控制每类任务的并发上限；
- `kind_priorities` 控制可运行任务之间的默认优先级，数值越大越先运行；
- `provider_limits` 是 provider 资源池接口；只有作业 metadata 显式带 provider 时才会参与限流。当前大多数 provider 仍在 Gateway 内部自动选择，因此该字段先作为长期调度接口保留。

## 环境变量覆盖

这些变量优先级高于配置文件，适合临时压测：

```bash
export TEACHER_CONSOLE_AGENT_MAX_WORKERS=6
export TEACHER_CONSOLE_SOURCE_CLEAN_CONCURRENCY=4
export TEACHER_CONSOLE_ANALYSIS_CONCURRENCY=4
export TEACHER_CONSOLE_ANSWER_REVISE_CONCURRENCY=4
export TEACHER_CONSOLE_VISUALIZATION_MODEL_CONCURRENCY=4
export TEACHER_CONSOLE_SOURCE_CLEAN_INDEX_DEBOUNCE_SECONDS=2
```

## 批量基准

调度优化先用事实校准。可运行只读基准脚本：

```bash
python3 teacher-console/scripts/agent_batch_benchmark.py \
  --library student-error-library --kind source.clean --format markdown
```

脚本会读取 `.cache/agent-jobs/*.json`，输出每类任务的等待时间、运行时间、P50/P90、最大并发、provider/model 分布、失败类型和 token 用量。它不调用模型、不修改条目，也不重建索引。

新作业的失败类型由 Agent Gateway 或调度器在失败发生时写入 `failure_type`；脚本只对尚无该字段的旧作业使用文本启发式兼容。因此后续调度优化应按结构化失败码统计，不能把中文错误文案当作稳定接口。

需要把一次基准沉淀进本地记忆时显式加 `--record`：

```bash
python3 teacher-console/scripts/agent_batch_benchmark.py \
  --library student-error-library --kind source.clean --format markdown --record
```

这会追加一个全库级 `scheduler.benchmark` 事件到 `student-error-library/indexes/candidate-archive.jsonl`，并刷新 Knowledge Store 的 `scheduler_benchmark` 派生表。该事件不属于任何单题，不会写入条目目录，也不会进入学生端公开内容。

## 和 Evolve 闭环的关系

Phase 1 只做静态配置和优先级队列。`rag_effectiveness_report.py` 已开始把 Knowledge Store 证据状态、Candidate Archive、Evaluator 和教师复核结果汇总为只读观察报告；样本门槛未满足前不自动改调度策略。后续可在固定测试集和连续报告支持下调整：

- 每类任务的推荐并发；
- 每类任务的模型/供应商优先级；
- 失败后是否重试、降级或转人工；
- 批量任务与当前教师交互任务的优先级。

安全边界不变：调度器不能批准题干、批准答案、批准可视化、finish 或发布学生端。

检索后端增强与慢循环启用门槛见 [`evolve-roadmap.md`](evolve-roadmap.md)。
