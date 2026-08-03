# 教学正确性证据基线 v1

> 记录日期：2026-07-29  
> 对应任务：CE-001  
> 代码基线：`09794868abb67d784c56b895dc51c74289a976d9`

## 1. 结论

当前项目已经具备稳定的结构校验、方法门禁、W3 分解—求解—复核流程和生产启用门禁，但尚不具备“逐条物理断言均由可追溯证书支撑”的证明链。

已有历史 replay 显示 W3 在 5 道题、15 个评分目标上达到 `15/15`，优于同批 W2 的 `14/15`；这批样本来自既有题目，且 fresh holdout 仍为 `0/5`，因此该结果只能作为诊断证据，不能作为生产正确性证明。

## 2. 基线边界

- 本报告记录开始实施 Claim Ledger 前的运行时代码 `HEAD`。
- 记录时工作树仅有执行计划及其文档入口变更，没有运行时代码变更。
- CE-001 没有调用模型、重跑正式题目、修改知识库或写入 canonical 条目。
- 后续正确性能力默认保持影子模式，不改变当前 W2 生产输出。

## 3. 确定性测试

### 3.1 W3 专项测试

命令：

```bash
python3 -B -m unittest \
  teacher-console/tests/test_solution_verification.py \
  teacher-console/tests/test_solution_reasoning.py \
  teacher-console/tests/test_w3_pipeline.py \
  teacher-console/tests/test_analysis_artifacts.py
```

结果：

```text
Ran 28 tests in 0.010s
OK
```

### 3.2 全量单元测试

可复现命令：

```bash
/Users/qingyuan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -B -m unittest discover -s teacher-console/tests -p 'test_*.py' -v
```

结果：

```text
Ran 273 tests in 2.919s
OK (skipped=2)
```

跳过的两个测试均为当前沙箱禁止创建 loopback socket 的 HTTP 集成测试。

系统 `python3` 的首次运行还记录到一个环境问题：该解释器缺少 Pillow，8 个测试模块在导入 `PIL` 时失败；已使用 Codex 工作区依赖运行时复跑并全部通过。这个失败不属于代码断言回归，但说明全量测试命令必须绑定含项目依赖的 Python 环境，不能把任意系统 Python 当作可复现基线。

## 4. W3 历史 replay

证据文件：

- `student-error-library/evals/w3-shadow-w4-replay-1/result.json`
- `student-error-library/evals/w3-shadow-w4-replay-1/paired-result.json`

| 指标 | 结果 |
|---|---:|
| replay 题目数 | 5 |
| 评分目标数 | 15 |
| W2 目标正确数 | 14/15 |
| W2 目标正确率 | 0.9333 |
| W3 目标正确数 | 15/15 |
| W3 目标正确率 | 1.0000 |
| W3 相对提升 | +0.0667 |
| W2 交付质量通过 | 3/5 |
| W3 平均 Agent 调用数 | 4.4 |
| 平均教师聚焦数 | 0.4 |
| 可用于生产证据 | 否 |

已知差异包括：

- 一题 W2 漏掉完整解集分支，W3 补全；
- 一题 W2 虽结论正确，但首次相遇的中间区间排除链未完整展开；
- replay 中存在 1 个参考答案修订样本和 1 个经确认的有效补充目标。

这些现象直接支持 Claim Ledger 的必要性：答案级“正确/错误”分数无法区分漏分支、推理链未闭合和参考答案本身需要修订。

## 5. Fresh holdout 状态

命令：

```bash
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-fresh-1 \
  status --holdout-count 5
```

结果：

| 字段 | 值 |
|---|---:|
| 已审核题目 | 14 |
| 历史题目 | 14 |
| fresh eligible | 0 |
| 尚缺 fresh 题目 | 5 |
| ready_to_seed | false |

因此现有生产门禁保持关闭是正确行为。CE-505/G5 继续标记为 `WAIT-5`，直到积累 5 道不属于既有 manifest 的新鲜、教师审核题目。

## 6. 当前已证明与未证明

当前确定性系统能够证明：

- 输出结构满足 `wuli.analysis.v2` 等既有契约；
- 学生主线方法不缺失、不超过五步且不使用明确禁用的超纲方法；
- W3 的目标、义务、冲突仲裁等结构字段满足现有验证器；
- fresh holdout 不足时不会误开生产门禁。

当前确定性系统不能证明：

- 每个前提都准确来自题干且没有遗漏隐含边界；
- 每个模型假设、物理定律和适用条件均成立；
- 每次数值计算、量纲、区间覆盖和事件先后均正确；
- 每个中间结论都有已验证上游依赖；
- 最终答案的每个分支都能回溯到经验证的前提；
- verifier 与 solver 没有共享同一错误前提或错误推理模板。

## 7. 后续基线约束

后续 CE-1 至 CE-4 的实现必须满足：

1. 现有 28 个 W3 专项测试不得回归；
2. 全量测试使用上述工作区依赖 Python；
3. Claim、证书、挑战和回跳均保存在私有影子产物中；
4. Agent 只能提交 `candidate`，不能自报 `verified`；
5. 没有完整证书覆盖时只能输出 `PROVISIONAL/UNRESOLVED`；
6. replay 可用于故障诊断，不能替代 CE-505 的 fresh holdout。
