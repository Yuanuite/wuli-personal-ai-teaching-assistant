# 统一核心求解与旧 W3 回滚链

> 2026-08-02 起，生产默认已从 W2/W3 分流切换为 `wuli.core-solve.v1` 统一核心求解。
> 本文后半部保留旧 W3 契约，作为显式回滚与历史评测说明，不再描述默认生产路径。

默认链只进行一次模型求解调用。复杂度不再选择不同求解管线，只决定是否向同一次
调用附加裁剪证据，以及在将来是否启动真正独立的验证器或阶段状态 sidecar。旧 W3
完整原子 DAG 曾提高可审计性，但也把模型共同假设、结构失败和多次调用延迟叠加到
每道复杂题；它现由 `analysis-production-routing.json` 的 `legacy-adaptive` 模式显式恢复。

完成状态、五道新题的固定命令顺序、生产灰度和最终回滚验收统一见
[`rag-completion-work-tree.md`](rag-completion-work-tree.md)；本文只维护 W3 契约与
实验事实。

## 当前默认流程

```text
教师已复核题干 + 可选 MiMo 事实
→ 轻量 Target Brief（目标 ID、方法 profile、风险信号）
→ 可选裁剪 Evidence Pack
→ DeepSeek Flash：wuli.core-solve.v1（唯一求解调用）
→ Core Gate（目标绑定、结论非待定、方法范围、结构与条件完整）
→ 确定性教学渲染（不调用模型、不改 final_answer）
→ Render Fidelity Gate
→ 教师审核；隔离评测由冻结标准答案代替教师审核
```

核心产物保存为私有 `core-solution.json`。学生版、教师版和兼容版 Markdown 仅从该
产物确定性投影；静态图与交互仿真均为答案通过后的可选增强，不再阻塞解析生成。
一次核心调用失败后直接结束，不再自动换旧 W2 提示重复求解。

`student-error-library/config/analysis-production-routing.json`：

- `mode: core-first`：当前默认；一条求解链、一次模型调用；
- `mode: legacy-adaptive`：显式回滚到下文保留的 W2/W3 自适应链；
- `max_latency_seconds`：核心调用的硬超时，当前为 90 秒。

条件增强仅由风险触发：定向召回可注入同一次核心调用；独立验证只有在冻结标准答案
或真正不同模型/证据视角存在时才有权改变正确性状态；阶段状态只服务真实多阶段传递
或 `physics-model.json`；仲裁只处理两个真正独立结论的冲突。

## 旧 W3 固定流程（仅回滚/研究）

```text
题干
→ 确定性复杂度初筛
  ├─ 低结构风险：保持 W2
  └─ 复杂题：
     problem.decompose 双层蓝图
     → Obligation Discovery：把量词、首次/唯一、边界、分支和模型适用域转成校验义务
     → 目标/义务覆盖 Gate：每个目标和必要物理义务必须被 Claim 覆盖
     → 3 路默认、最多 5 路定向召回
     → W2 evidence-set-v2 统一选集
     → Solver A 两段紧凑结构化求解（核心结论 / 阶段接口）
     → Claim Evidence 开启时：
       Claim Ledger + 确定性证书
       → 独立 Claim/阶段接口语义复算（每批最多 8 条）
       → Proof Aggregator
     → Claim Evidence 关闭时保留目标级风险验证、Solver B 与仲裁旧路径
     → 单一推荐结果 + 最多 2 张教师核对卡
```

外部调度器仍只看到一个 `analysis.generate` 作业。拆题、求解、验证和仲裁是该作业
内部的结构化阶段，不新增模型 capability，也不绕过同题单任务锁。

OpenAI-compatible provider 对 `wuli.solution-reasoning.v2.1.solver-*` 不再发送一个
大型 Schema：第一段只返回目标结论、决定性关系、选项和蓝图覆盖，第二段只返回阶段
结果、接口与转换；本地合并后仍按原始完整契约校验。两段都限制字符串、数组和动态
对象体积，接口键再做确定性规范化。拆分只改变传输形状，不降低最终 W3 契约。

2026-08-02 的新版 Flash 隔离复测还增加了 `competition-minimal-core-v1` 对照组：
它跳过上述 W3 内部阶段，只要求每小问一个最终答案和至多四条决定性关系。该对照用于
判断模型本身的速度与正确率，不是新的生产路由。全年首轮结构成功 7/8、完成题耗时
均低于 12 秒，但严格整题正确为 0/8；因此教学展开可以推迟到核心答案通过以后，
标准答案/隐藏真值正确性门禁不能删除。

## 两层蓝图

- `physical_stages`：真实发生的物理过程、进入/退出条件、跨阶段继承的状态；
- `reasoning_steps`：建模、列式、分类、排除、复算等求解操作；
- 正式解题蓝图不包含难度评分字段；`cognitive_operation`、`knowledge_units` 和
  `type_distance` 只存在于 `difficulty-shadow-v1` 成对实验契约，实验输出在进入
  检索、Solver、验证器和仲裁前必须剥离这些标注；
- `stage_step_links`：只表达两层之间的对应关系，不把解题流程冒充物理过程；
- `verification_obligations`：每个题目目标至少绑定一项必须复核的断言；
- `retrieval_needs`：按目标/阶段聚合的方法需求，而不是把整道长题只检索一次。

### 义务发现 Gate

“有没有把该验证的物理问题先拆出来”发生在 `problem.decompose` 和 Solver 之间。这个阶段不求解，
只把题干中的限制条件翻译成必须被覆盖的 `verification_obligations`。它是防止数学证书误导的前置门禁：
如果只生成了代数 Claim，而漏掉“首次”“唯一”“是否允许返回”“全部分支”“根的物理域”等义务，
后面的算术、量纲或 CAS checker 即使全部通过，也只能证明局部数学变形正确，不能证明物理答案正确。

必须显式拆出的义务包括：

- 目标量词：全部可能、唯一、任意、存在、最大/最小；
- 事件顺序：第一次进入、再次经过、返回前后、最后一次命中；
- 边界归属：端点是否包含、进入/离开区域、临界相切、越界后是否允许回到目标域；
- 分支完整性：正负根、多周期、多区域、多对象、多释放时刻；
- 模型适用域：参考系、正方向、受力/场区、忽略项、参数非零和时间/空间范围。

Gate 的失败不是 verifier 失败，而是拆解失败。此时应补 `verification_obligations` 或输出
`PROVISIONAL` 审核包，不能继续用一个较窄的数学 Claim 冒充整题验证通过。

对“水被较轻液体完全排开、竖直板受静水合力”这一类已识别的多流体题，编排器还会
确定性追加关键义务：先由同深度压强连续确定实际自由液面，再覆盖各流体的完整润湿
区间，最终式必须保留由液柱高度产生的密度比。该门禁专门阻止 Solver 与语义 verifier
共享同一错误拆解时，把漏掉外侧液面以上轻流体板段的简单密度差式共同判对。

蓝图是规划提示，不是答案真源。Solver 可以标记 `revised-equivalent` 并给出等价修订；
验证器检查的是结论与关系，不检查答案是否逐字照抄蓝图。

难度评分是 W3 的下游消费者，不是 W3 的生成约束。正式评分优先投影 Solver 的
`supporting_relations`、验证器的 `decisive_checks` 和仲裁决定性关系，再按固定知识单元
注册表确定性去重；没有解后证据时才回退蓝图关系。影子评分契约必须通过同题、同模型、
同参数的成对非退化评测，才能申请并入正式蓝图契约。

## 召回、验证与隐私边界

`build_blueprint_evidence()` 默认执行优先级最高的 3 个需求；后续需求只有在增加目标
覆盖或新候选时才继续，最多 5 路，连续两路无边际收益就停止。所有候选最终仍经过
W2 的精度门禁、去重、冲突阻断和统一字符预算。

独立验证器不读取历史答案正文。`verification_evidence_view()` 会删除标题、命中片段、
条目/路径标识，只保留知识点、方法、带条件二级结论和证据冲突摘要。验证器只获得
当前题干、蓝图、待验证目标及其可复算关系。

Solver B 不是常驻调用：仅当目标风险达到 `0.9` 的挑战阈值，或验证器发现事实冲突时
启动，而且只接收命中的目标子蓝图，不重算其余低风险目标。
仲裁禁止多数投票，必须给出决定性关系；仍无法确定时内部标记教师核对，但教师端
不显示 `unresolved`、角色争论或长日志，只给最多两张行动卡。

行动卡默认收起。教师点开具体冲突后，可查看经过限长和角色中性化处理的主候选结论、
独立复算结论、差异候选、仲裁采用结论与决定性关系；每段支持复制，候选结论还可一键
填入“修改意见”。该操作只减少人工转写，不会直接覆盖答案，也不会绕过教师复核。

结构化结论不会再触发一次纯排版模型调用。`render_recommended_student_solution()`
确定性生成包含“答案速览、一眼识别、详细解答、易错点、30 秒自测”的单一候选，
并复用现有高中方法/五步上限检查。每个内部阶段按题干、上下文、契约和模型摘要写入
私有检查点；失败后重试只复用完全相同输入的阶段，避免重复消耗。

## 断言级正确性证据影子

2026-07-29 起，W3 内部另有一层默认关闭的 `claim_evidence_shadow_v1`。它没有把
自然语言物理题包装成“已经形式证明”，而是把可审计范围下沉到每个必要断言：

```text
Solver 关系
→ Claim Ledger（版本、条件、依赖、义务）
→ 算术/量纲/区间/事件顺序确定性证书
  或 wuli.claim-verify.v2 隔离语义复算
→ 跨阶段接口组合检查
→ Challenge + 最小冲突定位 + 依赖定向回跳
→ Proof Aggregator 从已验证子图汇总
```

随机联想只用于选择“下一种有限证伪搜索算子”。新假设必须相关、新颖、可证伪，并
始终留在隔离 Hypothesis Pool；它不能写入证明 DAG，也不能因为多个 Agent 同意就
晋升为真。相同任务指纹不重复调用，连续无证据增量会换策略，达到硬上限只输出
`PROVISIONAL/UNRESOLVED`。

运行报告把三个数字严格区分（`wuli.analysis-run-report.v1` 的 counts）：

- `logical_stage_count`：阶段数（decompose / Solver A / verifier / Solver B /
  adjudicator / Claim batches / W3R renderer），检查点命中阶段计 0；
- `provider_attempt_count`：Gateway 实际执行次数（含重试与回撤后的重执行）；
- `upstream_request_count`：到上游模型的 HTTP 请求数（OpenAI-compatible 紧凑
  契约可能把一个逻辑阶段拆成 2 个请求）。
- `rollback_count`：已接受结论因具名 Challenge 被失效并**实际重新执行**受影响
  任务的次数；只生成 Challenge、只改控制状态或只回放检查点不计回撤。
- `supplemental_analysis_count`：主 Solver 后新增的模型分析请求（verifier、
  Solver B、adjudicator、Claim verifier 批次、W3R 表达重试）。
- `control_transition_count`：认知环状态机转移次数；可能 >0 而 rollback=0。

每次真实回撤必须携带：触发 Challenge、冲突证据、最小依赖锥、失效版本、重试
任务指纹、新版本、验证结果与停止原因；没有这些证据时只记
`rollback_observed=false`。

开发或隔离 E2E 可在进程启动前设置：

```bash
export TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW=1
```

该变量不是生产开关：默认值为关闭，启用后也只扩充私有
`w3-shadow-report.json`、紧凑 Candidate Archive 指标和教师端
`w3_shadow.claim_evidence` 安全快照；不改正式答案、答案批准、交付文件或学生站。
旧阶段输出可以兼容投影为 Claim，但自由文本关系仍需语义证书，不能把兼容投影本身
当作证明。

当前 Solver 契约为 `wuli.solution-reasoning.v2.1`：每个物理阶段声明坐标系、时间原点、
方向和进出状态，相邻阶段显式声明状态映射以及由边界事件新引入的状态。确定性检查先
拒绝硬冲突；只有自由文本变换等语义项可由独立 verifier 审核。Claim verifier 使用与
Solver 不同的 Claude Code 上游模型身份，默认是
`Deepseek-v4-pro` / `Deepseek-v4-flash`；请求按最多 8 条 Claim 分批，以服从默认
单次 0.50 美元预算。多批次默认最大并发为 2；设置
`TEACHER_CONSOLE_W3_CLAIM_VERIFY_CONCURRENCY=1` 可回滚为串行。检查点在全部并发
批次完成后统一落盘，避免缓存写入改变兄弟事务的 canonical 摘要；Gateway 的
canonical 并发门禁保持不变。启用该链路后不再重复调用旧目标 verifier、Solver B
和仲裁器。

每次 W3 运行的 `stages` 遥测记录 `duration_seconds`、`provider_seconds`、
`overhead_seconds`、`attempt_count`，Claim 批次还记录 `batch_index`。语义审计报告
同时记录实际 wall time、并发度和最小化评估。当前 `wuli.solution-reasoning.v2.1`
兼容投影仍把自由文本阶段结果与关系标为 `semantic-required`；在没有可执行确定性
checker 前，安全可删请求数为 0，不能通过只审最终 Claim 来换取速度。

方法合规独立于物理正确性：普通课堂使用 `high_school_standard`，竞赛官方解答测试
使用 `olympiad_official`，后者允许竞赛常见微积分但不自动放行分析力学。报告分别记录
求解、Proof、接口、方法和渲染门禁，渲染全绿不能替代教师真值核对。

2026-07-30 对 29 日题目做了隔离真值复测：弹簧题得到
`F_net=3k(r-l)`、`r=3kl/(3k-mω²)`；多流体 IPhO 题得到
`F_x=wg h²ρ₀(ρ₀-ρ_oil)/(2ρ_oil)`，方向 `+x`。两题 Proof、接口、方法和
W3R 忠实性均通过。最后一次增量运行分别约 190 秒和 387 秒，但包含不同数量的检查点
重放，不能与旧版“仅求解约 90 秒”直接比较。IPhO 的 Pro 求解调用使用了临时 1.00
美元上限；生产默认仍为 0.50，超预算时继续失败关闭。

同一弹簧题随后用相同的分解/Solver 检查点只重跑 3 个 Claim 批次：串行历史基线
190.26 秒，并发 2 为 67.81 秒，下降 64.36%；三批 provider 工作量合计 113.22 秒，
但关键路径为 67.73 秒。19 个 Claim、22 份证书、接口检查和 W3R 六项门禁均保持
通过，因此 Claim 多批并发已成为默认值，串行仍作为即时回滚设置。

在 Blueprint 进入 Solver 前，确定性源题领域门禁会检查显式量词和适用域：首次/最早
必须排除更早事件；所有/全部必须覆盖全部物理分支；范围、区间和边界必须核对定义域
与端点；参考系/相对运动必须固定坐标与状态变换。它们与多流体完整润湿区间义务一样，
只补题干明确要求但 Blueprint 漏掉的义务，不依赖 Solver 或 verifier 的共同理解。

现有证据包括 14 类故障注入全部检出且错误晋升为 0、认知环开/关同条件消融，以及
5 道旧题 113 个 Claim 的只读诊断投影。旧题回放发现 1 题当前答案摘要已偏离旧
manifest，历史分数因此必须刷新。详见
[`技术执行计划书.md`](技术执行计划书.md)、
[`reports/correctness-evidence-metrics-v1.md`](reports/correctness-evidence-metrics-v1.md)、
[`reports/correctness-cognitive-loop-ablation-v1.md`](reports/correctness-cognitive-loop-ablation-v1.md)
和
[`reports/correctness-replay-diagnostic-v1.md`](reports/correctness-replay-diagnostic-v1.md)。
这些结果证明链路会失败关闭并可重放，但单独看仍不证明未见题泛化。后续 WAIT-5
已经由五道新教师复核题和十五个冻结目标满足：W2/W3 均为 15/15，首批生产灰度
9/9、官方竞赛补充灰度 8/8，并完成 W2 可逆回滚演练。这些是旧自适应链的历史验收
事实；当前外层 `analysis-production-routing.json=core-first` 优先，复杂题也不会自动进入
W3。只有显式切换为 `legacy-adaptive` 或运行 W3 shadow 命令才执行下文 W3 阶段。
Claim Evidence 与受控认知环仍保持默认关闭的私有影子层。

2026-08-03（w3-w3r-route-deadline-repair）实际路线核实：route-preview 现返回
`wuli.route-execution-plan.v1`（planned solver / W3R mode / renderer / 预期阶段 /
config digest），core 与 W3 是显式不同计划，教师端不再从按钮文案推断路线。
真实 provider 的 W3 shadow 实测（合成题，solver=deepseek-v4-flash-api、
verifier=deepseek-v4-pro-api）执行到 `decompose` 阶段后 provider_timeout，
如实记录 `failed-stage`；W3R 仅消费 VERIFIED Proof，未 VERIFIED 时
`not-run-unverified`。W3/W3R 默认放量仍需 rollout evidence 与维护者批准。

## 影子门禁与命令

冻结评测集：

```bash
python3 -B teacher-console/scripts/w3_shadow_benchmark.py seed --holdout-count 5
```

运行单题影子重放（不改正式答案）：

```bash
python3 -B teacher-console/scripts/w3_shadow_run.py <entry-id> \
  --routing-tier expert --model-id codex-visualization
```

每题由教师按 `labels/<entry-id>.json` 做目标级判定；然后执行：

```bash
python3 -B teacher-console/scripts/w3_shadow_benchmark.py score
```

当前影子策略在进入 holdout 前冻结以下参数：挑战阈值 `0.9`、验证收益函数
`positive-expected-gain-v1`、默认/最大召回路数 `3/5`、证据选择器
`evidence-set-v2`。生产资格硬门槛：

- 独立 holdout 至少 5 题且至少 12 个目标；
- W3 目标准确率不低于 W2；
- 每题教师核对卡平均不超过 2；
- 真值摘要、标签、影子报告全部完整。

准确率是第一门槛。即使调用数下降或教师核对更快，只要正确率退化，就不得切换生产。

### 教师参考答案可被证据挑战

教师复核稿是主参考，但“文本不一致”不再直接等同于“答案错误”。目标判定支持：

- `correct`：与当前参考答案一致且复算成立；
- `incorrect`：违反题干、决定性关系或边界条件；
- `valid-supplement`：参考答案未覆盖，但教师确认题干约束、可复算关系和边界枚举三项均成立；
- `needs-review`：尚不能确定，不进入评分。

`valid-supplement` 计入数学正确率，同时单独记录补充解数量和参考答案修订。若补充解由
本轮影子输出触发，修订后的准确率可以纠正假阴性，但该 holdout 已不再独立；
`fresh_holdout_required=true`，不得用它直接宣称生产门禁通过。

## 2026-07-26 验收结果

冻结策略完成 5 道校准题和 5 道独立 holdout：

| Cohort | 题数 | 目标数 | W3 准确率 | W2 准确率 | 平均调用 | 平均核对卡 |
|---|---:|---:|---:|---:|---:|---:|
| calibration | 5 | 16 | 100% | 100% | 4.2 | 0 |
| holdout（修订前文本一致性口径） | 5 | 16 | 93.75% | 100% | 4.6 | 0.4 |
| holdout（教师确认数学正确性口径） | 5 | 16 | 100% | 100% | 4.6 | 0.4 |

原先唯一“错误”来自“三维复合场中电子的类平抛、螺旋运动与共圆心条件”。教师复算
确认：题干没有排除发射延迟 $\Delta t=nT$；此时两电子横向相位复现，$z_0=l$ 且
任意 $x_0$ 都是合法补充分支。正式解析和参考答案已修订，该目标标为
`valid-supplement`，所以修订后数学正确率为 16/16。

这次修订也意味着 W3 输出参与发现了参考答案缺口，本轮 holdout 不再是未见独立集：

- `holdout_ready=true`；
- `accuracy_non_regression=true`；
- `teacher_focus_bounded=true`；
- `independent_holdout_intact=false`；
- `fresh_holdout_required=true`；
- `production_eligible=false`。

因此 W3 的架构与数学正确性验收完成，W2 暂时继续作为默认解析路径；生产切换需要
一组新的未见 holdout，不能复用本轮五题证明泛化。

## W4-1：新鲜 holdout 冻结

新一轮不再允许“先运行 W3、再补教师目标标签”。独立评测分为四个不可倒置的动作：

```text
检查未见题数量
→ 生成 fresh-only manifest
→ 教师批准逐目标真值并生成 truth-lock.json
→ 携带 experiment 参数运行 W3 影子重放
```

历史上进入任何 W3 manifest 的题都会被排除，即使它当时属于 calibration、没有生成
标签或没有完成影子运行，也不能重新包装成新 holdout。新批次至少需要 5 道从未进入
旧 W3 manifest 的教师复核题；真值锁至少覆盖 12 个目标。`student-solution.md`、
逐目标真值或锁文件任一摘要改变后，评分失败关闭。

```bash
# 只读检查当前是否有足够的新鲜样本
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-fresh-1 \
  status --holdout-count 5

# 样本充足后冻结批次；不足时不创建任何实验目录
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-fresh-1 \
  seed --fresh-only --holdout-count 5 --batch-id w4-fresh-1

# 教师把 truth/*.json 的目标补全并设为 approved 后，生成不可覆盖的真值锁
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-fresh-1 \
  freeze-truth

# 只有携带有效真值锁的批次才进入正式 W4-1 重放
python3 -B teacher-console/scripts/w3_shadow_run.py <entry-id...> \
  --experiment student-error-library/evals/w3-shadow-w4-fresh-1 \
  --routing-tier expert --model-id codex-visualization
```

截至 2026-07-26，14 道教师复核题均已进入 `w3-shadow-v1` manifest，新鲜可用题为
0，距离 W4-1 最低门槛还缺 5 道。因此当前只能验收冻结机制，不能生成合格的新
holdout，也不能改变 `production_eligible=false`。

教师可以显式选择旧题进入 `replay` 对照组。回放题同样先冻结当前教师真值，可用于
检查答案回归、调用量和教师核对负担，但聚合报告将其与 `holdout` 分栏；无论回放题
数量和准确率多高，都不计入独立 holdout，也不能单独打开生产门禁。

首批 `w3-shadow-w4-replay-1` 选择 5 道旧题、冻结 15 个目标。持久化 W3 输出回放
结果为 15/15，平均内部调用 4.4，平均教师核对卡 0.4；旧报告中的 W2 字段只是教师
复核稿基线，不计作 W2 生成成绩，真实 W2 成对候选在 W4-2 单独生成；验证记录完整，
但 `holdout_ready=false`、`fresh_holdout_required=true`、
`production_eligible=false`。

## W4-2：同条件 W2/W3 成对回放

W4-2 不再把教师复核稿当作 W2 输出。W2 候选必须从隔离题库的真实教师页面点击
“生成解析”，并固定 `routing_tier`、`model_id` 和 candidate evidence；W3 候选必须
使用相同模型与路由档位。`paired-score` 只接受浏览器来源、已完成状态、冻结目标完整
标注和一致运行条件，并同时报告目标准确率与交付质量：

```bash
python3 -B teacher-console/scripts/paired_answer_web_run.py <entry-id> \
  --evidence-mode candidate --routing-tier expert --model-id codex-visualization \
  --artifact-dir student-error-library/evals/w3-shadow-w4-replay-1/artifacts/<entry-id>

python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-replay-1 paired-score
```

首个真实配对为交替电场与磁场分段运动题。初次诊断先后暴露 compact LaTeX 分数
残片、JSON 单反斜杠产生的隐藏控制字符和过窄的决定性关系长度上限；物化层现会原子
恢复这些分数命令、清除 ANSI 序列、拒绝其他控制字符，并允许最长 360 字的可复算
复合关系。失败重跑也不再覆盖上一份成功候选。

修复后完成了全部 5 道真实网页配对，共 15 个冻结目标：

| 指标 | W2 | W3 |
|---|---:|---:|
| 目标准确率 | 14/15（93.33%） | 15/15（100%） |
| 相对 W2 增量 | — | +6.67 个百分点 |
| W2 可交付性通过 | 3/5 | — |

唯一准确率差异来自三维电磁运动题的完整解集：W2 把“回旋圆心重合”误当必要条件，
漏掉 `z0=l、任意 x0` 的同相位分支；W3 保留了该分支。另有一道双区域磁场题结论
正确，但首次相遇证明对两个中间时段只写“联立无解”，未展示位置式，因此 W2 交付
完整性未通过。

这 5 题全部属于 replay，且唯一差异目标曾由旧 W3 输出推动教师补充真值，不能作为
独立泛化增益。`production_evidence=false` 继续成立；本轮证明成对链路可用、W3 能
在已知高风险分支上避免 W2 漏解，但不能替代新鲜 holdout。

## 主要实现

- `teacher-console/problem_decomposition.py`
- `teacher-console/solution_reasoning.py`
- `teacher-console/solution_verification.py`
- `teacher-console/w3_pipeline.py`
- `.claude/skills/decompose-physics-problem/`
- `teacher-console/scripts/w3_shadow_run.py`
- `teacher-console/scripts/w3_shadow_benchmark.py`

## W3R：已验证证明的非求解教学渲染

W3R 已接入独立的 `off / shadow / gray / default` renderer 路由。当前本地配置文件虽
存在，但字段与 `teacher-console.w3r-renderer.v1` 契约不一致，会按 fail-closed 规则视为
无效配置并回退旧 renderer，不替换生产答案。它只消费冻结的
`wuli.w3r-brief.v1`，输入必须来自整体状态为 `VERIFIED` 的 Proof Package；投影器只选择
最终 Claim 及其已验证祖先，不能读取历史答案、补推导或重新调用 Solver。

独立 renderer 输出学生版、教师版和 `claim_span_map`。Render Gate 确定性检查最终答案
签名、目标覆盖、条件保持、Claim/Step 引用、跨度文本指纹、LaTeX 配对、公式是否来自
Brief，以及当前方法 profile。`high_school_standard` 禁止积分、导数等超纲方法；
`olympiad_official` 按竞赛官方口径允许微积分等工具，但默认仍禁止未获来源授权的
拉格朗日/哈密顿形式。纯表达故障最多用完全相同的 Brief 重试一次；Claim 漂移、
条件遗漏或无来源公式立即拒绝；材料不足返回 `needs_render_material`。

只有 VERIFIED Proof、逐题 Render Gate 和绑定评测证据同时通过时，灰度/默认配置才会
物化 W3R 候选；否则只回退当前 renderer，不重跑 W3。候选写入后仍进入既有
`needs-answer-review`，旧批准摘要自动失效。W3 求解契约现要求逐阶段的坐标系、时间
原点、方向、入口/出口状态以及相邻阶段状态映射；确定性接口检查通过后才允许整体
Proof 达到 `VERIFIED`，真实变换尚未完成语义复核时仍保持 `PROVISIONAL`。

Claim Evidence 启用时，Solver 与 claim verifier 都只允许使用 `claude` provider，
但必须绑定不同模型身份；当前默认分别为 `Deepseek-v4-pro` 与
`Deepseek-v4-flash`。同模型自证、非 Claude provider 或缺少独立 verifier 配置都会
在运行前失败关闭。评测分别报告求解完成、Proof 忠实性、方法 profile 合规和 W3R
教学渲染四层结果，不再把方法范围问题混同为答案正确性问题。基线、配对、盲审包和
上线状态见：

- `docs/reports/w3r-baseline-v1.md`
- `docs/reports/w3r-shadow-benchmark-v1.md`
- `docs/reports/w3r-blind-review-packet-v1.json`
- `docs/reports/w3r-rollout-readiness-v1.md`
- `docs/构建-W3R-非求解教学渲染与忠实性门禁-原子执行.md`
