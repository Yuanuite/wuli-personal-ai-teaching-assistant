# 教学正确性证据链旧题 replay 诊断 v1

- 生成时间：`2026-07-29T02:50:00+08:00`
- 实验：`w3-shadow-w4-replay-1`
- 旧题 / 冻结目标：5 / 15
- 当前 Claim 投影：113；义务：37
- 投影可重放率：100.0%
- 无证书时 `PROVISIONAL`：5/5
- 旧报告自带 Claim Evidence：0/5
- 当前答案摘要与旧 manifest 不一致：1/5
- 历史 W2 / W3 目标正确数：14/15
- 已知 W3 增益目标 / 题：1 / 1
- 当前故障集检出：14/14；错误晋升：0

## 门禁

- `truth_lock_intact`：true
- `all_legacy_cases_projected`：true
- `projection_replayable`：true
- `legacy_outputs_not_auto_trusted`：true
- `all_declared_faults_detected`：true
- `zero_false_promotion`：true
- `canonical_unchanged`：true
- `production_authorized`：false

## 历史分数可复用性

- `historical_scores_reusable_without_refresh`：false
- `reference_refresh_required`：true

旧 replay 的 5 题、15 个目标都能投影到当前 Claim Ledger，且两次投影完全一致。由于
历史报告产生于 Claim Evidence 接入前，没有当前版本的证书，因此 5/5 都正确保持
`PROVISIONAL`，不会因为旧 W3 曾获得 15/15 就被自动信任。

另有 1 题的当前教师答案摘要已不同于旧 manifest。历史 `14/15` 对 `15/15` 可作为当时
的已知差异记录，但在刷新冻结参考前，不能再作为当前可重算的分数。

## 解释边界

- 历史输出产生于 Claim Evidence 接入前，当前步骤只做确定性投影。
- 无当前版本证书的投影全部保持 `PROVISIONAL`，旧答案分数不会自动变成证书。
- 旧题已参与开发与教师复核，只能用于链路和已知故障诊断。
- 本报告不能替代 fresh holdout，也不能授权生产。

## 可重复生成

```bash
python3 -B teacher-console/scripts/correctness_replay_diagnostic.py \
  --experiment student-error-library/evals/w3-shadow-w4-replay-1 \
  --markdown
```
