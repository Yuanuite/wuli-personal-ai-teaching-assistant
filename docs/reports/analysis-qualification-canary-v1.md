# 分析资格 canary（A5.4，脱敏）

- 模型：`deepseek-v4-flash-api`；结论：`qualified`；样本：3（合成复杂物理题，无学生数据）
- 结构成功 3/3；领域 Gate 3/3；p50 5969ms / p95 7109ms
- 契约：`wuli.core-solve.v1`（Target Brief digest + 顺序 + 方法策略）
- 逐题：
  - charged-particle-critical-motion: structural=True gate=True duration=4.918s failure=-
  - multi-body-energy-chain: structural=True gate=True duration=7.109s failure=-
  - dual-rod-electromagnetic: structural=True gate=True duration=5.969s failure=-
