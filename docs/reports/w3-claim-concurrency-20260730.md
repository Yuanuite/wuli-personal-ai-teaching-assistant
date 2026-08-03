# W3 Claim 批次并发 A/B（2026-07-30）

## 结论

在同一道 29 日弹簧题上复用完全相同的已验证分解和 Solver 检查点，只重新调用三个
Claude Flash Claim 批次。最大并发 2 将总耗时从串行历史基线 190.2637 秒降到
67.8109 秒，下降 64.36%，加速 2.81 倍。

候选保持 19 个 Claim、22 份证书、0 个未闭合 Claim；阶段接口为 `pass`，Proof 聚合为
`VERIFIED`，W3R Render Gate 六项指标均为 1.0，Unsupported Claim Rate 为 0。

## 分段数据

| 项目 | 结果 |
|---|---:|
| 分解检查点 | 0.0008 s |
| Solver 检查点 | 0.0008 s |
| Claim batch 0 | 45.5236 s |
| Claim batch 1 | 38.0645 s |
| Claim batch 2 | 29.6610 s |
| Provider 工作量合计 | 113.2190 s |
| Claim 关键路径墙钟 | 67.7261 s |
| 整体墙钟 | 67.8109 s |
| 框架外耗时 | 0.0832 s |

三个批次按 0/1 同时开始、先完成者继续领取 batch 2；最终证书和遥测按批次索引恢复
确定顺序。

## 发现并修复的竞态

第一次并发试跑中，先结束的批次立即写入题目目录下的 Claim 检查点，导致仍在运行的
兄弟 Gateway 事务检测到 canonical 摘要改变并以 `canonical_changed` 拒绝。门禁按
预期失败关闭，未产生错误晋升。

修复没有排除路径或放宽摘要检查，而是把已验证 Claim 检查点延迟到全部批次结束后
统一写入。修复后的第二次真实 A/B 无 stage failure，Proof 与 W3R 全通过。

## 为什么不减少 Claim

离线最小化评估显示，本题 18 个非 premise Claim 都需要语义复核：

- 自由文本阶段结果和中间关系仍是 `semantic-required`；
- 最终 Claim 的本地 aggregation 证书只证明依赖链接闭合，不能证明物理语义正确；
- 当前安全可删 Claim 数为 0。

因此本轮选择并发化必要审计，不以减少证据覆盖率换速度。要进一步降低调用量，必须先
把 Solver 契约扩展为带可执行 `arithmetic`、`dimension`、`interval` 或
`event-order` check spec 的原子 Claim，再重新做消融。

## 启用与回滚

默认最大并发已设为 2；只在 Claim Evidence 启用且请求超过一批时生效。

```bash
export TEACHER_CONSOLE_W3_CLAIM_VERIFY_CONCURRENCY=1
```

以上设置即时回滚串行。允许值严格限制为 1 或 2。

## 验证

- Python：492 tests passed，4 skipped。
- 浏览器 E2E：lifecycle、visualization、publication 已通过；同步两个 fake adapter
  的新源题领域义务后，Claim Evidence 的 normal/conflict/insufficient/fuse 四场景
  通过，且 canonical 条目保持不变。
- 真实 Claude：19/19 Claim 闭合，22 份证书，W3R production readiness 为 true。
