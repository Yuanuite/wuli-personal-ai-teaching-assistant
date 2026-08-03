# 解析失败修复、W3 全链可观测与逐步验证原子 Work-Tree

> 状态：已实现并验证（A1-A3、B1-B4、C1、D1-D4、E1-E3 完成：72/72 严格单测、4 条新增可观测 E2E + 既有 E2E 全部通过；E4 真实无隐私 smoke 为维护者批准项，C2/C3 运行时事件埋点为后续演进；历史失败作业报告给出 diagnosed=output_truncated / recorded=candidate_no_change）
> 版本：`wuli-analysis-run-observability-v1`
> 日期：2026-08-02
> 类型：故障修复 + 运行可观测性 + W3 过程验证 + 文档治理

## 1. 目标与真值政策

本计划解决四个可独立失败的目标：

| ID | 目标 | 成功标准 | 风险 |
|---|---|---|---|
| T1 | 修复本次解析失败的分类与直接原因 | reasoning-only 截断稳定分类为 `output_truncated`；不再显示 `candidate_no_change` | P0 |
| T2 | 输出一次解析的完整运行账本 | 脚本可列出实际路由、模型、provider、请求次数、检查点、回撤、补充分析、验证与终态 | P0 |
| T3 | 让 Core 与 W3 每一步都有可执行验证 | 每个阶段都有输入摘要、输出摘要、验证器、结果和失败关闭状态 | P0 |
| T4 | 消除配置、网页、CLI 与文档叙述分叉 | 所有入口消费同一运行事件；文档只描述当前可达模式 | P1 |

权威顺序：

1. `.cache/agent-jobs/<job-id>.json` 中的实际终态、attempt 和 timing；
2. Gateway 结构化结果、Candidate Archive 与 W3 私有报告；
3. 当前路由快照和 model registry 摘要；
4. canonical 条目摘要及领域验证器结果；
5. 页面文案和说明文档。

脚本完成不等于物理答案正确。确定性 Gate 只能证明契约、算术、量纲、接口或覆盖等
对应性质；自由文本物理变换仍需独立 verifier 或教师复核。报告必须保留
`VERIFIED / PROVISIONAL / UNRESOLVED / FAILED / NOT-RUN`，不得把缺证据渲染成通过。

## 2. 本次失败的事实锁

最近一次失败作业：

```text
job_id             9cf0a400e43d4222964dc48ccc5bb607
kind               analysis.generate
route              core-first → core
model_id           deepseek-v4-flash-api
provider           openai-compatible
duration           60.969 s
target_count        6
evidence_context    3 references / 7586 serialized chars / truncated=true
changed_files       []
```

provider stderr 的关键事实：

```text
finish_reason       length
content_chars       0
reasoning_chars     18547
completion_tokens   6000
```

### 2.1 根因链

```text
六目标复杂题 + 较大结构化契约 + 裁剪 Evidence Pack
→ DeepSeek Flash 直接 API 请求未显式关闭 thinking
→ 上游默认把 6000 completion token 全部消耗于 reasoning
→ 没有产生 JSON 正文
→ adapter 以非零状态退出，候选区没有文件变化
→ Gateway 未识别 “reached max_tokens” 为截断
→ 因 requires_change=true 且 changed_files=[]，误分类 candidate_no_change
→ 单次运行超过 30 秒预算保护阈值
→ 不调用第二 provider，安全停止
```

直接故障是 `output_truncated`，不是 Agent “忘记修改文件”。预算保护停止第二次完整
推理符合现行策略；错误在于请求控制和失败分类，而不是预算保护本身。

### 2.2 促成因素与非原因

- 促成因素：`TEACHER_CONSOLE_AGENT_API_MAX_OUTPUT_TOKENS` 默认 6000；reasoning 与 JSON
  共用输出预算；`TEACHER_CONSOLE_AGENT_API_THINKING` 未设置时沿用上游默认。
- 促成因素：当前 `core-first` 对复杂题仍强制一次核心调用；旧 W3 路由配置不会覆盖
  外层 `analysis-production-routing.json=core-first`。
- 分类缺口：`classify_agent_failure()` 识别单词 `truncated`，但没有识别
  `finish_reason=length`、`reached max_tokens` 或 reasoning-only completion。
- 遥测缺口：adapter 异常没有把上游 usage/finish_reason 放入结构化失败 envelope，作业
  最终显示 `usage.measurement=unavailable`。
- 非原因：MiMo 没有参与本次核心求解；它只在适用时生成视觉事实或进行图后软评审。
- 非原因：没有发生 canonical 写入、批准或回撤；候选文件数为零。

### 2.3 安全附带发现

本地 model registry 含内联凭据字段。报告脚本必须永不读取、输出或计算这些值；实施前
应轮换现有密钥，并改为仅通过 allowlist 环境变量解析。任何测试夹具只能使用假 key。

## 3. 当前模式与真实调用边界

### 3.1 当前生产 `core-first`

```text
source review passed
→ Target Brief（本地）                         [0 模型调用]
→ Knowledge Evidence Pack（本地检索）          [0]
→ core-solve / DeepSeek Flash                  [1]
→ Core Gate（本地确定性）                      [0]
→ 学生版/教师版确定性渲染                      [0]
→ Render Fidelity Gate（本地确定性）           [0]
→ 候选提升                                     [0]
→ 教师答案复核                                 [人工]
```

MiMo 视觉提取是题干复核前的独立动作，不是 DeepSeek 核心求解后的协调器。静态图 MiMo
软评审是答案后的可选动作，也不属于本次解析调用数。

### 3.2 `legacy-adaptive` / W3 shadow

只有显式回滚到 `legacy-adaptive`，或执行 `analyze-w3-shadow` / `w3_shadow_run.py` 时，
才运行 W3：

```text
复杂度初筛
→ problem.decompose 双层蓝图
→ Obligation Discovery + 覆盖 Gate
→ 定向召回（默认 3、最多 5，连续两路无收益停止）
→ Solver A
→ 阶段接口确定性检查
→ [Claim Evidence 开启]
   Claim Ledger → 确定性证书 → Claim verifier batches → Proof Aggregator
   → Challenge → 最小冲突锥 → 有界控制面回跳
→ [Claim Evidence 关闭]
   风险 verifier → 必要时 Solver B → 必要时 adjudicator
→ W3R/旧 renderer → Render Gate
→ 生产模式才允许候选提升；shadow 永不覆盖正式答案
→ 教师复核
```

模型身份必须从运行事件读取，不能由文档推测。当前默认意图是 Solver 使用
`Deepseek-v4-pro`、Claim verifier 使用不同身份 `Deepseek-v4-flash`；provider 可为契约
允许且实际配置通过探针的 `claude` 或 `openai-compatible`。报告必须同时输出注册 ID、
上游 model、provider 和 config digest。

W3 的调用数不是固定常数：

| 阶段 | 模型请求数 |
|---|---:|
| decompose | 0 或 1（检查点命中为 0） |
| Solver A | 1 个逻辑阶段；OpenAI-compatible 紧凑契约可能拆成 2 个 HTTP 请求 |
| 风险 verifier | 0 或 1 |
| Solver B | 0 或 1 |
| adjudicator | 0 或 1 |
| Claim verifier | `ceil(semantic_claim_count / 8)`，检查点命中批次为 0 |
| W3R renderer | 0 或 1；仅表达故障允许同 Brief 再试 1 次 |

因此必须分别统计 `logical_stage_count`、`provider_attempt_count` 和
`upstream_request_count`。

## 4. 计数语义

报告中的三个数字不得混用：

- `rollback_count`：已接受的 Claim/阶段因具名 Challenge 被失效，并实际重新执行受影响
  任务的次数。只生成 Challenge、只改变控制状态或只回放检查点不算回撤。
- `supplemental_analysis_count`：主 Solver 完成后新增的模型分析请求数，包括风险 verifier、
  Solver B、adjudicator、Claim verifier 批次和 W3R 表达重试；本地 Gate 不计。
- `control_transition_count`：认知环状态机转移次数。它可能大于零而
  `rollback_count=0`，当前实现中的 Claim Evidence 多数运行正是这种情况。

每次回撤必须包含：触发 Challenge、冲突证据、最小依赖锥、失效版本、重试任务指纹、
新版本、验证结果与停止原因。没有这些证据时只能记为 `rollback_observed=false`。

## 5. 目标脚本与输出契约

新增只读脚本：

```bash
python3 -B teacher-console/scripts/analysis_run_report.py \
  --job-id <job-id> --format markdown --verify

python3 -B teacher-console/scripts/analysis_run_report.py \
  --entry-id <entry-id> --latest --format json \
  --output /private/tmp/analysis-run-report.json
```

脚本输出契约：`wuli.analysis-run-report.v1`。

```json
{
  "schema": "wuli.analysis-run-report.v1",
  "run": {
    "job_id": "...",
    "entry_id": "...",
    "status": "failed",
    "mode": "core-first",
    "selected_route": "core"
  },
  "runtime_identities": [],
  "steps": [],
  "counts": {
    "logical_stage_count": 0,
    "provider_attempt_count": 0,
    "upstream_request_count": 0,
    "checkpoint_replay_count": 0,
    "rollback_count": 0,
    "supplemental_analysis_count": 0,
    "control_transition_count": 0
  },
  "verification_summary": {},
  "terminal": {},
  "redactions": []
}
```

每个 `steps[]` 必须包含：

```text
step_id / name / category
input_fingerprint / contract
runtime_identity 或 local-verifier
started_at / duration / attempt_count / upstream_request_count
artifact_refs（仅仓库相对路径）
verification_obligations[]
verification_result: passed|failed|provisional|not-run
failure_type / retry_or_backjump / terminal_effect
```

隐私规则：不输出 API key、Authorization、完整 prompt、学生原图、答案全文、绝对路径或
provider 原始 reasoning。默认只输出摘要、计数、指纹、限长失败信息和仓库相对工件名。

退出码：

| code | 含义 |
|---:|---|
| 0 | 所有适用自动 Gate 通过；人工复核可仍为 pending |
| 2 | 自动链完整但物理证据为 `PROVISIONAL/UNRESOLVED` |
| 3 | 作业失败、报告证据不完整或摘要不一致 |
| 4 | 输入不存在、schema 不兼容或检测到敏感字段泄漏 |

## 6. 逐步验证矩阵

| Step | 输入/产物 | 必须验证 | 失败状态 |
|---|---|---|---|
| P00 | job + route snapshot | job/entry/kind 一致；快照 digest 当前或明确 stale | FAILED |
| P01 | source review | 批准状态与 problem/source fingerprint 当前 | BLOCKED |
| P02 | visual facts（可选） | schema、source fingerprint、人工复核门禁 | PROVISIONAL/BLOCKED |
| P03 | Target Brief | 目标 ID 唯一、覆盖题目小问、method profile 合法 | FAILED |
| P04 | Evidence Pack | 字符预算、来源类型、冲突摘要、无隐私泄漏 | PROVISIONAL |
| P05 | 路由选择 | 外层 mode、内层配置、模型 capability、probe、config digest | FAILED |
| P06 | provider request | 契约摘要、thinking/output 策略、request index、超时/预算 | FAILED |
| P07 | provider response | finish_reason、usage、正文存在、JSON/schema 完整 | FAILED |
| P08 | Core/W3 materialize | 结构规范化、控制字符、LaTeX、目标/条件绑定 | FAILED |
| P09 | 物理 Gate | 量纲、符号、边界、事件序、分支、阶段接口 | PROVISIONAL/FAILED |
| P10 | 独立验证 | verifier 与 Solver 身份不同；证书绑定当前 Claim 版本 | PROVISIONAL |
| P11 | 回跳/补充分析 | 最小依赖锥、次数上限、版本递增、无重复指纹 | UNRESOLVED/FAILED |
| P12 | Proof aggregation | 仅聚合已验证 Claim；未决项显式保留 | PROVISIONAL/UNRESOLVED |
| P13 | renderer | 不新增 Claim；最终答案、条件、公式来源忠实 | FAILED |
| P14 | candidate promotion | allowed/denied paths、canonical 摘要、事务锁与回滚 | FAILED |
| P15 | teacher review | 当前答案 digest 对应当前工件；只允许教师批准 | PENDING |

## 7. 原子任务 DAG

通用原子契约：除任务单独声明外，每项最多执行 1 次；失败不得修改 canonical 条目，
终态只能是 `completed / failed / blocked / provisional`。重试必须有新输入版本或新策略
指纹，并只重跑最小依赖锥。每项的“验证”字段就是独立验收路由，不能由实现者口头确认。

### Wave A：冻结事实与安全边界

#### A1 · 固化失败夹具

- 动词：extract
- 输入：作业 `9cf0...607` 的脱敏字段。
- 输出：`teacher-console/tests/fixtures/analysis-run/reasoning-only-length.json`。
- 验证：fixture 不含条目正文、绝对路径、prompt、reasoning 内容或凭据。
- 最大尝试：1；终态：`completed|blocked-security`。

#### A2 · 轮换并移除内联凭据

- 动词：sanitize
- 输入：model registry 凭据字段与环境变量 allowlist。
- 输出：仅 env 引用的本地配置及脱敏测试报告。
- 验证：仓库、job、report、日志全文扫描无 key；真实旧 key 由维护者轮换。
- 最大尝试：1；批准者：维护者。

#### A3 · 冻结报告 schema

- 动词：model
- 输入：第 5、6 节。
- 输出：`teacher-console/schemas/analysis-run-report.v1.schema.json`。
- 验证：合法 Core/W3 样例通过，未知字段和敏感字段失败。
- 依赖：A1。

### Wave B：修复直接失败与失败语义

#### B1 · 纠正截断分类

- 动词：classify
- 输入：adapter error envelope。
- 输出：`finish_reason=length`、`reached max_tokens`、reasoning-only completion
  均映射 `output_truncated`。
- 验证：原失败夹具不再映射 `candidate_no_change`；真正零修改成功响应仍映射后者。
- 依赖：A1。

#### B2 · 结构化 adapter 失败 envelope

- 动词：extract
- 输入：OpenAI-compatible error response。
- 输出：failure_type、finish_reason、usage、content/reasoning 字符计数、request_count；
  不包含 reasoning 正文。
- 验证：异常退出后 job 的 usage 不再为 `unavailable`。
- 依赖：A3。

#### B3 · 校准 thinking/output 策略

- 动词：compare
- 输入：同一冻结复杂题，至少比较 `thinking=disabled` 与上游默认；保持模型、契约、
  evidence、温度一致。
- 输出：结构成功率、正确性 Gate、token、延迟对照报告。
- 验证：不得仅因更快切换；必须 JSON 完整且答案 Gate 不退化。
- 最大样本：先 3 个合成/公开复杂题；不得使用正式学生原图。
- 依赖：B2。

#### B4 · 建立请求体预检

- 动词：verify
- 输入：target count、contract 大小、evidence 字符数、模型输出上限。
- 输出：`request_preflight`；超界时显式选择缩减 evidence、分段契约或停止。
- 验证：不允许通过静默提高费用上限解决；决策写入运行报告。
- 依赖：B3。

#### B5 · 保持预算保护

- 动词：verify
- 输入：失败 attempt duration/usage。
- 输出：重试/不重试决定及原因。
- 验证：有实质消耗或超过 30 秒仍不得自动完整重跑；零 token 协议错误可按策略处理。
- 依赖：B1、B2。

### Wave C：统一运行事件

#### C1 · 定义阶段事件

- 动词：model
- 输出：`wuli.analysis-stage-event.v1`，覆盖 queued、gate、provider-request、
  provider-response、checkpoint、verification、backjump、promotion、review。
- 验证：事件有单调序号、run ID、step ID、输入/输出指纹和脱敏证明。
- 依赖：A3。

#### C2 · 埋点 Core 全链

- 动词：instrument
- 输出：P00-P15 中所有适用 Core 事件。
- 验证：成功、截断、schema invalid、validation failed 四条 fixture 无断链。
- 依赖：B1-B5、C1。

#### C3 · 埋点 W3 阶段与 HTTP 子请求

- 动词：instrument
- 输出：decompose、recall、Solver A 两段、verifier、Solver B、adjudicator、Claim
  batches、W3R 的逻辑阶段与 upstream request 分离事件。
- 验证：阶段 `attempt_count` 不再冒充 HTTP request count；检查点命中请求数为 0。
- 依赖：C1。

#### C4 · 记录回撤与补充分析

- 动词：count
- 输出：`rollback_count`、`supplemental_analysis_count`、
  `control_transition_count` 及逐项证据。
- 验证：只有实际失效 + 重执行才增加 rollback；Challenge-only fixture 必须为 0。
- 依赖：C3。

#### C5 · 绑定提升与人工复核

- 动词：bind
- 输出：候选摘要、canonical before/after、批准 digest 对应事件。
- 验证：shadow 无 promotion；Agent 无 approve 事件；摘要漂移时报告失败。
- 依赖：C2、C3。

### Wave D：实现报告脚本

#### D1 · 聚合事件

- 动词：aggregate
- 输出：`analysis_run_report.py` 的 JSON 数据模型。
- 输入：job、stage events、W3 report、Candidate Archive、canonical 摘要。
- 验证：只接受同一 run/entry/fingerprint 的事件；缺失项显示 `not-run/missing`。
- 依赖：C2-C5。

#### D2 · 渲染 Markdown

- 动词：render
- 输出：人类可读报告，依次展示结论、路由、模型、调用数、时间线、验证、回撤、
  补充分析、终态和下一步。
- 验证：Markdown 与 JSON 由同一对象渲染，数字逐字段相等。
- 依赖：D1。

#### D3 · 实现 `--verify`

- 动词：verify
- 输出：逐步 Gate 表和退出码 0/2/3/4。
- 验证：篡改摘要、遗漏事件、混入 API key、伪造 passed 均失败。
- 依赖：D1。

#### D4 · 统一 Web/CLI 消费

- 动词：integrate
- 输出：网页 job 详情与 CLI 都读取同一 report schema；网页只显示安全摘要。
- 验证：同一 job 的状态、模型、调用、回撤和失败类型完全一致。
- 依赖：D2、D3。

### Wave E：验证与文档收口

#### E1 · 单元测试

- 动词：test
- 覆盖：reasoning-only length、正常 JSON、真正 no-change、usage 保存、脱敏、计数语义、
  checkpoint、Challenge-only、一次真实回撤、上限熔断。
- 成功标准：missing/skipped 视为失败。

#### E2 · 隔离 Core E2E

- 动词：test
- 场景：成功、reasoning-only 截断、预检阻断、candidate validation failed。
- 成功标准：Web/CLI 同报告；正式库/output/student-site 零写入。

#### E3 · 隔离 W3 E2E

- 动词：test
- 场景：全检查点、Claim batches、Challenge-only、一次最小锥回撤、熔断、W3R 表达重试。
- 成功标准：每步验证存在；调用/回撤/补充分析计数与 mock 账本完全相等。

#### E4 · 真实无隐私 smoke

- 动词：test
- 输入：公开或合成长题，不含学生原图。
- 成功标准：实际模型身份、finish_reason、usage、请求数进入报告；失败也必须可解释。
- 批准者：维护者；不得自动提高预算。

#### E5 · 同步文档

- 动词：reconcile
- 修改：`agent-gateway.md`、`failure-intelligence.md`、`w3-reasoning-pipeline.md`、
  `operator-runbook.md`、`teacher-console-api.md`、`CHANGES.md`。
- 验证：core-first/W3 优先级、MiMo 边界、provider/model 身份、回撤定义和脚本命令一致。

## 8. 状态传递契约

| Producer | Artifact / schema | Consumer | 前置条件 | 失效规则 |
|---|---|---|---|---|
| queue | `RouteSnapshot.v1` | Core/W3 runner、报告脚本 | registry/config digest 完整 | 任一配置摘要变化即 stale |
| source gate | problem/source fingerprints | Target Brief | 当前 source review passed | 题干或源摘要变化 |
| Target Brief | `target_brief` + digest | Core/W3 solver | 目标唯一且覆盖 | 题干、方法 profile 变化 |
| retrieval | `knowledge-evidence.json` + digest | Solver | 隐私裁剪、预算通过 | 当前题干/检索策略变化 |
| provider adapter | `analysis-stage-event.v1` | Gateway、报告脚本 | failure envelope 已脱敏 | request/contract/model 变化 |
| Core/W3 materializer | candidate + gate report | promotion | 所有必需领域 Gate 通过 | 候选任一字节变化 |
| Claim verifier | version-bound certificates | Proof Aggregator | 独立身份、input fingerprint 匹配 | Claim/依赖任一版本变化 |
| cognitive control | Challenge/backjump events | 最小锥任务 | 冲突证据具名、未触发 fuse | snapshot 版本变化 |
| renderer | Markdown + fidelity gate | promotion/review | 只消费已接受真值工件 | final Claim/条件/公式变化 |
| promotion | canonical before/after digests | teacher review | 单题锁、路径与摘要检查通过 | 答案相关工件变化 |
| report aggregator | `analysis-run-report.v1` | CLI/Web/维护者 | 所有事件同 run/entry | 任一来源事件或摘要变化 |

最终聚合只接受验证通过的步骤；`provisional/unresolved/failed/missing` 必须原样进入
`verification_summary`。Markdown 只是同一 JSON 对象的展示层，不能补写模型调用、
回撤或正确性结论。

## 9. 状态转移与回跳规则

```text
queued
→ inputs-verified
→ route-frozen
→ generating
→ candidate-materialized
→ domain-verified
→ rendered
→ promoted
→ needs-answer-review
```

失败只回到最小影响锥：

| 触发 | 回跳点 | 最大次数 | 终态 |
|---|---|---:|---|
| reasoning-only / length | B3/B4 请求策略，不自动重跑当前付费调用 | 0 自动 | FAILED |
| schema invalid（推理前） | contract adapter | 1 修复后人工重提 | BLOCKED |
| Blueprint 漏义务 | decompose/obligation | 1 | PROVISIONAL |
| Claim 冲突 | 最小依赖锥对应 atomic task | 每 task 1 | VERIFIED/UNRESOLVED |
| 相同任务指纹 | 不执行 | 0 | DEDUPLICATED |
| 连续无证据增量 | 换证伪策略；达到 fuse 停止 | 按冻结 policy | UNRESOLVED |
| renderer 表达故障 | 相同 Brief renderer | 1 | COMPLETED/FAILED |
| canonical 摘要漂移 | 不提升，重新冻结输入 | 0 | STALE |

## 10. 验收命令

实现后至少执行：

```bash
/Users/qingyuan/miniconda3/bin/python3 -B teacher-console/scripts/run_tests.py \
  --python /Users/qingyuan/miniconda3/bin/python3 --strict

/Users/qingyuan/miniconda3/bin/python3 -B teacher-console/scripts/run_tests.py \
  --python /Users/qingyuan/miniconda3/bin/python3 --all --strict

python3 -B teacher-console/scripts/analysis_run_report.py \
  --job-id 9cf0a400e43d4222964dc48ccc5bb607 --format markdown --verify

python3 -B teacher-console/e2e/run_e2e.py \
  --scenario analysis-core-report \
  --scenario analysis-core-truncated \
  --scenario analysis-w3-report \
  --scenario analysis-w3-backjump

git diff --check
graphify update .
```

历史失败作业的报告应得到 `output_truncated` 的诊断性重分类，但不得改写原 job；报告中
同时保留 `recorded_failure_type=candidate_no_change` 与
`diagnosed_failure_type=output_truncated`，保证审计可追溯。

## 11. 最终完成条件

只有同时满足以下条件才可标记 `accepted`：

1. 本次失败稳定诊断为 reasoning-only `output_truncated`；
2. adapter 保存 finish_reason、usage 和请求数但不保存 reasoning 正文；
3. thinking/output 策略通过同条件正确性对照，不靠静默加预算；
4. Core 和 W3 的所有适用阶段都有验证事件；
5. 模型注册 ID、上游 model、provider、config digest 和 route snapshot 可追踪；
6. 逻辑阶段、provider attempt、HTTP request、检查点、回撤、补充分析分别计数；
7. Challenge-only 不冒充真实回撤；真实回撤具备最小依赖锥与版本证据；
8. Web/CLI 对同一 job 输出一致；
9. 报告不含密钥、原图、完整 prompt/reasoning、绝对路径或答案全文；
10. 严格单测、Core/W3 E2E、原 lifecycle E2E 和干净归档检查全部通过；
11. 文档不再同时声称 core-first 与“复杂题默认 W3”；
12. 维护者查看一份 Core 成功、Core 截断、W3 有补充分析和 W3 有回撤的报告后签署。

若只有报告与失败分类完成，而真实回撤场景或模型策略对照未完成，状态必须为
`provisional`，不能用测试数量代替缺失证据。
