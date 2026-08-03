# 受控认知环同条件消融报告 v1

- 生成时间：`2026-07-29T02:45:00+08:00`
- 场景：4；故障场景：3
- 固定随机种子：`20260729`
- 同题、同模型、同证据：true
- 错误晋升 off / on：0 / 0
- 候选答案一致率：100.0%
- Challenge off / on：0 / 6
- 故障场景熔断数（on）：3
- 固定种子联想选择：3；重放一致率：100.0%；真值晋升：0

| 场景 | 注入结果 | off 状态 | on 状态 | on Challenge | on 熔断 |
|---|---|---|---|---:|---|
| normal | pass | PROVISIONAL | PROVISIONAL | 0 | false |
| conflict | conflict | UNRESOLVED | UNRESOLVED | 2 | true |
| insufficient | insufficient | PROVISIONAL | PROVISIONAL | 2 | true |
| fuse | insufficient | PROVISIONAL | PROVISIONAL | 2 | true |

## 结论

认知环开启后增加了与具体 Claim 绑定的 Challenge，并在没有新证据时有限熔断；它没有
改写候选答案，也没有把注入错误晋升为正确。

随机性只用于从受约束、可证伪的候选中选择下一项搜索任务；固定种子可完全重放，被选中
的假设仍保持 `candidate`，不能进入证明 DAG 或改变真值。

这说明当前方案实现的是“安全的诊断增强”，不是已经证明“答案正确率提升”。本消融只有
fake adapter 和确定性故障，不能替代新鲜、教师评分的独立 holdout。

## 可重复生成

```bash
python3 -B teacher-console/scripts/correctness_cognitive_loop_ablation.py \
  --generated-at 2026-07-29T02:45:00+08:00 \
  --markdown
```
