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
  "schema_version": 2,
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
  "provider_limits": {},
  "adaptive_concurrency": {
    "enabled": true,
    "initial": 1,
    "first_success_limit": 2,
    "max_limit": 4,
    "successes_to_max": 2
  }
}
```

## 调度规则

- 同一条目永远只能有一个 Agent 作业处于 `queued` 或 `running`；
- worker 只领取当前可运行的任务，不会让正在等待 kind/provider 限额的作业占住 worker；
- `kind_limits` 控制每类任务的并发上限；
- `kind_priorities` 控制可运行任务之间的默认优先级，数值越大越先运行；
- `provider_limits` 是 provider 资源池接口；只有作业 metadata 显式带 provider 时才会参与限流。当前大多数 provider 仍在 Gateway 内部自动选择，因此该字段先作为长期调度接口保留。

## 自适应并发

每个 `concurrency_group` 独立执行 `1 → 2 → 4` 的探测与恢复策略；未显式指定时按任务 kind 分组：

- 首道金丝雀成功后升到 2；
- 并发度 2 下连续成功 2 道后升到 4；
- provider 超时、限流、预算耗尽、不可用、协议/执行异常等结构性失败会立即停止派发该组新任务并降到串行；
- 已经在途的任务允许结束，但其结果不会冒充降级后的串行探针；
- 降级后的下一道新任务成功，恢复并发度 2，随后重新按连续成功门槛升到 4；
- 内容校验失败只隔离单题，不降低整组并发。

每次升降速都会写入终态作业的 `scheduler_event`，包括失效类型、调整前后并发度和累计降级次数。状态只驻留于当前服务进程；重启后从金丝雀重新开始，避免凭旧状态直接扩大并发。

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

统计优先读取 Gateway 产出的统一 `outcome`；旧作业缺少该字段时才兼容解析历史结果，避免调度器、候选档案和评测脚本各自推断失败语义。

## 和 Evolve 闭环的关系

当前层只做确定性的优先级队列和 `1 → 2 → 4` 故障反馈，不根据教学质量样本自动修改长期策略。`rag_effectiveness_report.py` 已开始把 Knowledge Store 证据状态、Candidate Archive、Evaluator 和教师复核结果汇总为只读观察报告；样本门槛未满足前不自动改变模型路由或长期调度参数。后续可在固定测试集和连续报告支持下调整：

- 每类任务的推荐并发；
- 每类任务的模型/供应商优先级；
- 失败后是否重试、降级或转人工；
- 批量任务与当前教师交互任务的优先级。

安全边界不变：调度器不能批准题干、批准答案、批准可视化、finish 或发布学生端。

检索后端增强与慢循环启用门槛见 [`evolve-roadmap.md`](evolve-roadmap.md)。
