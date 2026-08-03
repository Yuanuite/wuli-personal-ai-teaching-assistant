# 教学正确性证据链指标报告 v1

- 生成时间：`2026-07-29T02:38:26+08:00`
- 评测范围：`deterministic-faults-and-fake-adapter-shadow`
- 故障发现：14/14（100.0%）
- 错误晋升：0
- 影子 Claim：16；证书：16
- 已验证 Claim 比例：0.4375
- 关键 Claim 证书覆盖均值：0.25
- 重复任务率：0.0
- 回跳 precision / recall：1.0 / 1.0
- 未验证场景率 / 硬冲突率：1.0 / 0.25

## 故障分类

| 分类 | 检出 | 总数 | 检出率 |
|---|---:|---:|---:|
| arithmetic | 2 | 2 | 100.0% |
| backjump | 1 | 1 | 100.0% |
| dimension | 1 | 1 | 100.0% |
| event-order | 3 | 3 | 100.0% |
| hypothesis | 1 | 1 | 100.0% |
| interface | 2 | 2 | 100.0% |
| interval | 1 | 1 | 100.0% |
| loop | 1 | 1 | 100.0% |
| promotion | 2 | 2 | 100.0% |

## 门禁

- `zero_false_promotion`：true
- `all_declared_faults_detected`：true
- `backjump_exact`：true
- `no_repeated_shadow_task`：true
- `shadow_canonical_unchanged`：true
- `production_authorized`：false

## 解释边界

- 故障集只证明已声明检测器的行为，不证明自然题准确率。
- 影子场景使用隔离 fake adapter，不是新鲜 holdout 证据。
- 旧 W3 没有结构化阶段接口，因此正常场景仍保守标记为 `PROVISIONAL`。
- 本报告不能授权生产，也不能绕过 `WAIT-5`。

正常路径的关键 Claim 证书覆盖率为 1.0，但因阶段接口仍是旧的自由文本表示，整题不能
晋升为 `VERIFIED`。其余三个场景是故意注入的冲突、证据不足与熔断，因此 4 个场景的
关键覆盖均值为 0.25、未验证场景率为 1.0；这不是自然题失败率。

## 可重复生成

先运行隔离场景：

```bash
python3 -B teacher-console/e2e/run_e2e.py \
  --scenario claim-evidence \
  --artifacts /private/tmp/wuli-claim-evidence-metrics
```

再生成 JSON 或 Markdown：

```bash
python3 -B teacher-console/scripts/correctness_evidence_benchmark.py \
  --shadow-summary \
  /private/tmp/wuli-claim-evidence-metrics/claim-evidence/claim-evidence-summary.json
```
