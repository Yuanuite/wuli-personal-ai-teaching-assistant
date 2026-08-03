# W3R 上线就绪报告 v1

结论：W3R 工程链路完成，但生产灰度与默认启用均未获证据授权；当前继续使用 legacy
renderer。

| 门禁 | 当前结果 | 灰度要求 | 默认要求 |
|---|---:|---:|---:|
| 同 Brief 配对题 | 2 | ≥ 2 | ≥ 2 |
| 教师盲审题 | 0 | ≥ 2 | ≥ 2 |
| 新鲜 holdout | 0 题 / 0 目标 | 不要求 | ≥ 5 题 / ≥ 12 目标 |
| Final Answer Fidelity | 1.0 | 1.0 | 1.0 |
| Claim Support Coverage | 1.0 | 1.0 | 1.0 |
| Condition Retention | 1.0 | 1.0 | 1.0 |
| Target Coverage | 1.0 | 1.0 | 1.0 |
| LaTeX Validity | 1.0 | 1.0 | 1.0 |
| Unsupported Claim Rate | 0.0 | 0.0 | 0.0 |
| 教师可读性偏好 | 未评 | candidate > 0.5 | candidate > 0.5 |
| 教师修改率不劣化 | 未评 | 必须成立 | 必须成立 |

已完成的工程门禁：

- VERIFIED Proof、Brief fingerprint、Render Gate 和生产候选四重绑定；
- `off / shadow / gray / default` 严格配置，非法配置失败关闭；
- 灰度白名单和证据阈值；
- W3R 学生版不含 Claim、证书、span 或内部 fingerprint；
- 候选写入仍触发 `needs-answer-review`，教师必须重新批准；
- `mode: off` 只恢复 legacy renderer，不重跑 W3、不删除 trace；
- A/B 盲审分两阶段：先锁定偏好，再打开私钥核对 Claim/span。

待人工完成：

1. 使用 `w3r-blind-review-packet-v1.json` 完成两道真实教师盲审；
2. 用私钥进行第二阶段 Claim/span 核对并提交结构化 review；
3. 若要申请默认启用，另建从未用于开发的 5 题、至少 12 目标 holdout；
4. 将脚本生成的证据完整写入活动配置后，先做小范围 `gray`；
5. 灰度逐题复核通过后再单独申请 `default`，不得自动切换。

关联产物：

- `w3r-shadow-benchmark-v1.json`
- `w3r-rollout-evidence-v1.json`
- `w3r-blind-review-packet-v1.json`
- `../w3r-routing.example.json`
