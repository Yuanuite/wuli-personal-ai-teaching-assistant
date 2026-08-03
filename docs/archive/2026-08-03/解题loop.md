# 解题 Loop 技术文档

## 定位

本文把复杂物理题的“发散思考、假设提出、检验、否定、重构”整理成可执行的 Agent 工程架构。它补充
[`技术执行计划书.md`](技术执行计划书.md) 中的 Claim Evidence 与受控认知环设计，面向维护者回答：

- 怎样把一个复杂题拆成有限个原子任务；
- 怎样允许有价值的随机联想，同时避免无限循环；
- 怎样在多阶段求解中传递状态、回退、重建和汇总；
- 怎样把“模型答案看起来合理”提升为“每个结论有证据或明确未决”。

本机制默认属于 W3 私有影子链路和闭卷评测工具，不替代教师答案批准，也不直接发布到学生端。

## 设计原则

1. 最终证明图是 DAG，搜索过程可以有环。
2. 环只能存在于控制平面，不能直接改写证明 DAG。
3. Agent 可以提出假设，但假设必须先成为可证伪任务。
4. 随机性只能选择下一类搜索算子，不能选择真值。
5. 任一阶段失败只污染自己的候选区，不污染 canonical 条目。
6. 停滞、预算耗尽或证据不足时输出 `PROVISIONAL/UNRESOLVED`，不得把猜测晋升为正确。

人的发散思考可以随时质疑“之前的工作”；Agent 系统不能把这种自由质疑原样复刻。工程上要把它改写成
“有限挑战票据 + 影响锥回跳 + 指纹去重 + 证据增强才继续”的状态机。

## 核心对象

| 对象 | 含义 | 是否能改真值 |
|---|---|---|
| `Claim` | 一个物理结论、数值、边界条件或阶段接口结论 | 否，真值由证书汇总器重算 |
| `Certificate` | 算术、量纲、区间、事件顺序或语义复算证据 | 否，只提供晋升依据 |
| `AtomicTask` | 单一认知动词的可执行任务 | 否，只产出候选 Claim/Certificate |
| `ChallengeTicket` | 对某个 Claim 或接口的具体质疑 | 否，只触发诊断与回跳 |
| `Hypothesis` | 用于解释缺口的备选假设 | 否，不能进入证明 DAG |
| `ProofDAG` | 当前可审计推理图 | 是，只有汇总器可更新状态 |
| `Orchestrator` | 编排任务、预算、去重、回跳和熔断 | 否 |

## 原子任务契约

一个原子任务必须满足：

- 只有一个认知动词，例如 `extract_claim`、`verify_claim`、`diagnose_conflict`、`propose_hypothesis`；
- 输入版本冻结，包含题干摘要、上游 Claim 版本、证书版本和随机种子；
- 输出结构化、可独立校验；
- 失败不修改共享状态；
- 相同任务指纹可直接复用缓存结果；
- 能在不重跑整题的情况下单独作废；
- 有明确的最大尝试次数、时间预算和终止状态。

示例：

```json
{
  "task_id": "verify-C17-v1",
  "verb": "verify_claim",
  "target_ids": ["C17"],
  "input_snapshot": 4,
  "input_fingerprint": "...",
  "output_contract": "wuli.claim-verify.v1",
  "strategy": "event-order",
  "random_seed": null,
  "status": "pending"
}
```

不合格的任务示例是“重新想想这道题”“检查哪里错了”“让另一个 Agent 再做一遍”。这些任务没有单一动词、
没有冻结输入，也无法判断何时停止。

## 任务拆解

复杂物理题按两层拆解。

第一层按题目结构拆：

```text
整题
→ 小问
→ 目标结论
→ 必要前提
→ 阶段接口
→ 可验证 Claim
```

第二层按认知动作拆：

```text
提取条件 → 建模选择 → 推导中间量 → 计算/化简 → 边界检查
→ 单位/量纲检查 → 极限/特殊值检查 → 接口一致性检查 → 汇总
```

这里最容易出错的不是“某个算式算错”，而是“该验证的物理问题没有先拆出来”。因此在
`目标结论 → 可验证 Claim` 之间必须插入义务发现门禁：

```text
题干目标
→ 物理义务发现 Obligation Discovery
→ 目标/义务覆盖检查
→ Claim Builder
→ Claim Ledger
→ Certificate Verifier
```

义务发现要把题干中的物理限定翻译成显式 `verification_obligations`，至少覆盖：

- 量词：全部可能、至少、至多、唯一、任意、存在；
- 顺序：第一次、最后一次、再次经过、返回、到达前/后；
- 边界：进入/离开区域、临界相切、端点是否包含、是否允许越界后返回；
- 分支：正负根、多个周期、多个区域、多个释放时刻、多个对象；
- 模型：研究对象、参考系、正方向、受力/场区选择、忽略项是否仍满足题设；
- 适用域：时间区间、空间区间、参数范围、非零条件和根的物理可行性。

如果这些义务没有被声明，后续算术、量纲或 CAS 证书只能证明“已拆出来的窄 Claim 没错”，
不能证明整道题物理结论正确。比如 `t=3T` 的代数计算通过，不等于它就是“第一次进入”；
还必须有事件顺序 Claim 证明不存在更早可行事件。

当前工程试验采用 shadow-only 策略：`problem_decomposition.infer_default_obligation_suggestions()`
会识别题干未限定唯一时的“默认全物理解支”建议，并写入 W3 私有 summary 的
`default_obligation_suggestions`。这些建议不会自动并入 `verification_obligations`，
也不会传给 Solver 或改变 `VERIFIED` 门禁；只有经过回放和教师确认后，才考虑升级为强制 Gate。
当前只读回放统计命令为：

```bash
python3 -B teacher-console/scripts/default_obligation_shadow_report.py
```

报告写入 `docs/reports/default-obligation-shadow-report.md` 和同名 JSON。2026-07-29 的首轮
存量回放显示：23 个 W3 报告中 7 题触发、共 10 条建议；其中已出现“最高点时刻”“初速度竖直
分量”等疑似过宽命中。因此该规则目前只证明有诊断价值，不具备升级硬门禁的条件。

对 IPhO 这类官方试卷，官方小问天然是第一层原子目标；整题共享状态只在同一题内传递，不让多个 Agent
自由反复互相改答案。2021 IPhO 理论卷测试表明，这种“小问原子化 + 整题共享状态 + 隔离阅卷”的结构比
无界多 Agent 质疑更稳定。

## 状态传递

状态只通过版本化对象传递，不通过自由文本口头记忆传递：

```text
SourceSnapshot
  → ClaimLedger
  → CertificateLedger
  → ChallengeQueue
  → HypothesisPool
  → ProofSummary
```

每次任务只能读取自己的输入快照。若上游 Claim 被作废，下游任务不会被直接删除，而是进入
`stale/disputed`，等待编排器根据影响锥决定是否重算。

## 反馈回路

反馈回路只在出现明确事件时启动：

```text
验证失败或接口冲突
→ 创建 ChallengeTicket
→ 定位最小冲突集合
→ 计算影响锥
→ 标记受影响 Claim 为 disputed
→ 生成替代 Claim 或 Hypothesis 任务
→ 重新验证受影响子图
→ Proof Aggregator 汇总状态
```

允许的联想算子包括：

- 极端与边界；
- 反例构造；
- 逆向推理；
- 时间顺序重排；
- 坐标系或参考系变换；
- 守恒量与不变量；
- 对称性与对称破缺；
- 相似模型迁移；
- 隐含自由度；
- 替代问题分解。

每个 Hypothesis 必须说明它解释哪个缺口、与已有候选的实质差异、一次有限证伪方法，以及成立时影响哪些
Claim。没有这些字段的“灵感”不能进入任务队列。

## 终止条件

解题 loop 同时使用软终止和硬熔断。

软终止：

- 所有最终 Claim 和桥接 Claim 已有有效证书；
- 没有未解决的 `blocking` Challenge；
- 所有下游 Claim 都绑定当前输入版本；
- Proof Aggregator 可以输出 `VERIFIED` 或明确的 `PROVISIONAL` 审核包。

硬熔断：

- 连续任务没有新 Claim、新证书或更小冲突集合；
- 同一任务指纹重复出现；
- 同一影响锥反复回跳超过上限；
- 随机联想批次没有产生新颖候选；
- 时间、调用次数或预算达到上限；
- Gateway 任务契约自相矛盾。

硬熔断不是失败兜底成成功，而是停止执行并输出未决原因。

## 防重复与防错误积累

| 风险 | 防线 |
|---|---|
| 无限循环 | 任务指纹、影响锥回跳上限、无进展熔断 |
| 重复推理 | 相同输入快照和任务指纹直接复用结果 |
| 错误积累 | 候选只在隔离区；汇总器按当前证书重算状态 |
| 多 Agent 投票幻觉 | 投票不能决定物理真值，只能触发语义复算或教师审核 |
| 旧证据污染 | 证书绑定 Claim 版本，输入变更后旧证书失效 |
| 物理义务漏拆 | 目标必须先生成 `verification_obligations`；Claim DAG 要覆盖全部目标和义务，否则只能 `PROVISIONAL` |
| 编排错误伪装成模型错误 | Gateway 启动前检查路径契约，如 allowed 输出被 denied 覆盖则返回 `task_contract_invalid` |
| 预算耗尽后强行给结论 | 输出 `PROVISIONAL/UNRESOLVED` 和审核包 |

`candidate-answer.json` 首轮被同时列入允许和拒绝路径的 IPhO 脚手架事故说明：正确性系统不能只验证答案，
还必须验证“验证器和编排器自身是否自洽”。因此路径契约检查必须在 provider 调用前完成，避免把编排错误烧成
一次完整推理消耗。

## 结果汇总

Proof Aggregator 是唯一能把候选状态汇总成面向教师的结论层的组件。它输出：

- 已验证 Claim 列表；
- 未决 Claim 与阻塞原因；
- 每个最终答案依赖的证书；
- 被否定的假设及证伪证据；
- 熔断原因与预算使用；
- 教师需要裁决的最小问题包。

学生版答案只能来自通过教学门禁的最短主线；教师版可以展示更完整的证据账本、未决项和错误定位。

## 当前实现落点

| 能力 | 主要文件 |
|---|---|
| Claim、证书、DAG 和影响锥 | `teacher-console/claim_ledger.py`、`teacher-console/claim_validation.py` |
| 受控认知环、Hypothesis 和熔断 | `teacher-console/cognitive_loop.py` |
| 证明汇总 | `teacher-console/proof_aggregation.py` |
| W3 拆题、求解、验证和仲裁 | `teacher-console/problem_decomposition.py`、`teacher-console/w3_pipeline.py`、`teacher-console/solution_verification.py` |
| Gateway 候选隔离与路径契约 | `teacher-console/agent_gateway.py` |
| 闭卷整卷评测 | `teacher-console/scripts/ipho_closed_book_eval.py` |
| 故障注入、消融和只读回放 | `teacher-console/scripts/correctness_evidence_benchmark.py`、`teacher-console/scripts/correctness_cognitive_loop_ablation.py`、`teacher-console/scripts/correctness_replay_diagnostic.py` |

当前 Claim Evidence 与受控认知环仍默认关闭在私有影子层。生产默认路由是
`wuli-analysis-adaptive-v1`：复杂题进入 W3，低风险题保持 W2，W3 门禁失败时自动回退 W2。

## 验证命令

```bash
# Gateway 路径契约、候选隔离与 IPhO 闭卷编排
python3 -m unittest teacher-console/tests/test_agent_gateway.py \
  teacher-console/tests/test_ipho_closed_book_eval.py

# 受控认知环同条件消融
python3 -B teacher-console/scripts/correctness_cognitive_loop_ablation.py --markdown

# 旧题只读投影诊断
python3 -B teacher-console/scripts/correctness_replay_diagnostic.py \
  --experiment student-error-library/evals/w3-shadow-w4-replay-1 --markdown
```

修改本机制后，还要按 [`ai-editing-map.md`](ai-editing-map.md) 的“教学正确性证据链、Claim Ledger、
冲突回跳或受控认知环”行补对应单元测试、故障注入、`git diff --check` 和 `graphify update .`。
