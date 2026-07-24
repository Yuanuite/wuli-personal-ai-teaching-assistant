# Agent 失败排障层

失败排障层位于 Agent Gateway 与具体教学任务之间。它不替代 provider、Evaluator、领域验证器或教师复核，只负责把一次失败变成稳定分类、脱敏证据和受边界约束的下一步动作。

## 工作流

1. Gateway 在隔离候选区运行原任务，并返回稳定 `failure_type`。
2. `failure_intelligence.py` 读取当前校验错误，并从全库 Candidate Archive 检索“同任务类型 + 同失败类型”的近期模式。
3. 证据包删除条目 ID、绝对路径、完整 prompt、学生内容和密钥，只写入新候选区的 `.agent-context/failure-evidence.json`。
4. 先在原隔离候选区运行确定性零 Token 修复。只有仍属于内容形态可纠正、且首轮没有报告 token 消耗或达到成本阈值的失败，才在全新隔离目录中自动重试一次；第二次仍须经过原来的允许路径、denied paths、领域校验和 canonical 摘要检查。
5. `failure_repair.status`、初末失败类型、重试次数和历史引用数写入作业结果与 Candidate Archive；Benchmark 汇总 `repair_outcomes`。

对 `analysis.generate`，有效结构化响应会先保存为与当前输入摘要绑定的本地检查点。provider 之后的确定性落盘或校验失败不触发重复推理；下一次提交先零 Token 重放检查点。题干、记录、教师指令、模型或档位变化会使检查点失效，成功提升后检查点立即删除。

## 自动动作边界

| 失败类型 | 自动动作 |
|---|---|
| `candidate_validation_failed` | 先尝试恢复保护字段、同步答案副本等零 Token 修复；仅在首轮成本很低时重新生成一次完整候选 |
| `output_truncated` | 首轮成本很低时要求缩短输出并闭合必需结构，重试一次 |
| `candidate_no_change` | 首轮成本很低时提醒实际修改允许文件，重试一次 |
| `unauthorized_change`、`canonical_changed` | 永不自动重试；等待教师刷新或修正范围 |
| provider 超时、限流、超过费用预算、不可用、协议/执行失败 | Gateway 已做安全 provider 降级；记录后延迟重提或修复配置，避免立即重复计费；费用超限不得通过自动提高预算解决 |
| `simulation_build_failed`、`worker_interrupted`、`task_exception` | 保留证据，交给确定性构建排查或重新提交，不猜测成功 |

`max_retries=1` 是硬边界，不是必须重试的承诺。首轮报告任意正 token 用量，或单次 provider 运行达到 30 秒时，`failure_repair.status=not-retried-budget-protected`，避免用第二次完整推理修复已有候选。失败证据不能放宽路径、批准答案、交付或发布，也不能覆盖当前教师意见。

## 两条 Evolve 观察线

- 教学质量慢循环仍需 20 个已完成 RAG 任务和 10 个教师闭环，才可比较答案质量和检索策略。
- 可靠性观察把成功与失败的终态作业都计入样本；达到 5 个终态作业且至少有 1 个结构化失败，即可记录只读排障报告。它只能形成建议，不能自动更改路由、验证器或安全策略。

测试入口：

```bash
python3 -m unittest teacher-console/tests/test_failure_intelligence.py
python3 -m unittest teacher-console/tests/test_agent_batch_benchmark.py teacher-console/tests/test_slow_loop_report.py
```
