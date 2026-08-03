# W4-5 W3 自适应路由最终生产验收

```yaml
accepted_at: 2026-07-29
status: passed
routing_policy: wuli-analysis-adaptive-v1
w3_policy: wuli-w3-shadow-v1
analysis_contract: wuli.analysis.v2
production_mode: default
complex_route: w3
low_risk_route: w2
fallback_route: w2
max_agent_calls: 6
max_teacher_focus: 2
max_latency_seconds: 900
```

## 验收结论

`wuli-analysis-adaptive-v1` 已通过新鲜独立 holdout、生产灰度、官方竞赛补充灰度和
可逆回滚演练，现设为解析生成的默认路由策略。默认不等于“所有题强制多 Agent”：

- 确定性复杂度初筛命中的题进入 W3；
- 低结构风险题继续走 W2；
- W3 任一生产门禁失败时，在候选未污染 canonical 的前提下由 W2 接管；
- 所有生成结果仍回到教师答案复核，Agent 不获得批准或交付权限。

Claim Evidence 与受控认知环仍保持默认关闭的私有影子层；本次上线没有把
`PROVISIONAL/UNRESOLVED` 当作正式证明，也没有扩大其生产权限。

## 证据汇总

| 阶段 | 范围 | 结果 |
|---|---|---|
| 新鲜独立 holdout | 5 题、15 目标 | W2 15/15，W3 15/15，差值 0；独立性完整；平均 W3 调用 2.6，平均教师核对卡 0 |
| 首批生产灰度 | Q13/Q14/Q25，9 目标 | 9/9 correct；Q13、Q25 为 W3，Q14 保持 W2；最终回退 0 |
| 官方竞赛补充灰度 | IPhO、APhO、全国赛，8 原子目标 | 8/8 correct；两道复杂题 W3，一道低风险题 W2；最终回退 0 |
| 有限认知环与故障注入 | 14 类故障及 claim-evidence E2E | 错误晋升 0；重复任务 0；冲突/缺证触发熔断并保持 canonical 不变 |

新鲜 holdout 的 `production_eligible=true`，没有参考答案被本轮 W3 输出反向修订。
Q9-T1 的两份候选各保留一条长度量纲省略的可交付性警告，但目标结论正确，且不影响
W3 对 W2 的准确率非退化结论。

## 灰度失败与恢复记录

1. Q13 首轮学生方法出现微分表述，被确定性高中方法门禁拒绝；改写为
   `s=at²/2` 后通过。
2. Q25 的非必要显式加速度展开被教师要求改为端点与连续性方法；修订后重新批准。
3. IPhO 静水压力候选中的“表压积分”被方法门禁拒绝；只将等价表达改写为三角形
   压强—深度图面积，检查点重放后通过，物理结论未改变。
4. APhO Q3-A8 和全国赛首版摘录因人工原子化遗漏必要条件被排除，分类为
   `source-adaptation-incomplete`，不伪装成模型错误或正确样本。
5. APhO Q3-A8 的一次 adjudicator 协议失败仅进行一次作业级重放；
   `candidate_no_change` 只触发一次策略允许的纠正尝试，没有无限循环。

这些失败均有明确类型、有限重试和停止条件；无未经教师批准的候选进入交付。

## 回滚演练

使用同一条复杂题探针“粒子第一次进入区域后，求所有可能的返回时刻”执行实际配置
切换：

| 顺序 | 配置 | 结果 | 原因 |
|---:|---|---|---|
| 1 | `mode=default` | W3 | `deterministic-complexity-screen` |
| 2 | `mode=off` | W2 | `w3-production-disabled` |
| 3 | 恢复 `mode=default` | W3 | `deterministic-complexity-screen` |

最终生产配置为 `default`。配置在每次解析任务开始时重新读取；回滚不需要改变
provider、Gateway、候选隔离、答案契约或教师门禁。

## 最终验证

- Python 单元测试：385 项通过，4 项因当前沙箱禁止 loopback socket 跳过；
- 隔离 E2E：lifecycle、visualization、publication、claim-evidence 四个场景全部通过；
- `git diff --check`：通过；
- graphify：最终代码和文档更新后重新生成。

## 持续运营边界

W4 到此完成。W5 只继续积累教师闭环并输出只读慢循环报告。证据预算、模型路由、
检索策略或认知环开关的后续变化仍需新的同条件评测、样本门槛和教师明确确认，不能
因为 W3 已上线而自动获权。

