# 变更记录

## 2026-08-03：Core–W3/W3R 复杂题质量恢复（wuli-core-w3-w3r-quality-recovery-v1）

- 物理质量门禁（A0.2/A1.2/T1）：新增 `wuli.physics-quality-gate.v1`
  （`teacher-console/physics_quality.py`），在 `core_analysis.normalize_payload`
  中作为硬门禁执行——候选在提升前被拒绝，不再出现“core-solution.json 记录
  gate=passed 但推导与答案自相矛盾”。四个确定性 reason code：
  `derivation-answer-mismatch`（对数做功 vs 倒数差结论，同变量对）、
  `sign-flip-unjustified`（题设同向 + 负解 + 取绝对值但最终答案未陈述方向调和）、
  `internal-recheck-conflict`（复核行无条件为负而最终答案为正）、
  `symbol-undefined`（下标符号从未定义）。量纲与适用条件按 A0.2 回跳规则记录为
  `deferred-verifier`，不写成硬门禁。
- 失败夹具与回归（A0.1）：`tests/test_physics_quality.py` 14 项，覆盖两类缺陷
  （对数→倒数差跳跃、负值无依据取绝对值）+ 复核自相矛盾 + 符号未定义，相邻正确
  样例不误拒绝；真实失败样例
  （20260802-…-830117d9，Q3/Q4i/Q4iii）经门禁全部命中。
- 渲染真值化（A3.1）：`core-solution.json` 的 `gate` 改为真实报告
  （contract/status/obligations/reason codes），阶段序列新增
  `physics-quality-gate`；`deterministic-teaching-render` 后增加真实
  `render-fidelity-gate`（final_answer 与 key_relations 必须逐字出现在学生版）。
- W3R 配置真源（A2.1/DC-4/T4）：`w3r-production-routing.json` 迁移到
  `wuli-w3r-routing-v1` + 完整 evidence 字段，mode 保持 `off`；旧契约
  （`teacher-console.w3r-renderer.v1`）规范化 fail-closed 到 off。迁移测试 +
  失效配置测试各 1 项。
- 资格 canary 同步：`analysis_qualification_canary.py` 的 `_validate` 现在传入
  problem 文本，资格判定包含物理质量门禁，反映生产实际提升路径。
- 路线决策门 G1（A1.4）与 W3R 默认化（A5.3）仍为维护者+教师代表批准项。

## 2026-08-03：Provider 超时与结构化输出失配修复 · 阶段收口（wuli-analysis-provider-reliability-v1）

- 阶段进度（A3.2）：OpenAI-compatible adapter 在成功与失败两条路径都输出
  `stage_progress`（阶段名、finish reason、usage、字符计数、剩余期限），
  两阶段第二段超时能与“第一阶段未返回”区分；失败 envelope 携带阶段信息，
  不保存 reasoning 正文。`test_provider_reliability` 4 项覆盖单阶段成功、
  软超时 envelope 与阶段回传。
- 真实 canary（A5.4，维护者批准后执行）：`teacher-console/scripts/
  analysis_qualification_canary.py` 用 3 道合成复杂物理题（每题 6 目标、
  无学生数据）经生产 adapter 调用 `deepseek-v4-flash-api`，复用
  `wuli.core-solve.v1` 契约（Target Brief digest + `normalize_payload` Gate）；
  2026-08-03 实测 3/3 结构完整且领域 Gate 通过（p50 5.97s / p95 7.11s），
  已写入 `wuli.analysis-qualification.v1` 资格记录（结论 `qualified`），
  脱敏报告见 `docs/reports/analysis-qualification-canary-v1.{md,json}`。
- E2E 收口：`analysis-qualified-route` / `analysis-soft-timeout` /
  `analysis-route-preview` 三个场景经 `run_e2e.py` 全量验证通过；修正
  `visualization` 场景过期 token 断言（420→450，确定性组成
  source.clean 60 + analysis 120 + visualization 270）。
- 模型默认路由决议（A2.3/A5.5 条件 8）仍为维护者批准项。

## 2026-08-02：Provider 超时与结构化输出失配修复（wuli-analysis-provider-reliability-v1）

- 任务级模型资格（T1/A2.2）：新增 `wuli.analysis-qualification.v1` 与
  `record_analysis_qualification`；`analysis.generate` 默认解析只选择当前
  契约/配置摘要下 `qualified` 的模型，未资格化/过期/能力缺失在 provider
  调用前失败关闭；显式指定仅作为实验性自定义放行。
- 统一期限契约（T2/A2.1/A2.4）：`wuli.deadline-budget.v1` 三层期限
  （task/attempt/http_soft + cleanup_grace）满足有序不变量；Gateway 把 adapter
  HTTP 期限封顶到 soft deadline，作业记录冻结预算与问题列表；历史 90 秒
  provider_timeout 场景（f7afb816...）被夹具固化。
- 路由预览（T4/A4.1/A4.2）：`POST /api/entries/<id>/route-preview` 返回
  `wuli.route-preview.v1`（resolved model/provider/资格/期限/路由摘要），UI 在
  点击“运行解析流程”前显示本次解析真实路由，不再让通用 Codex 状态冒充解析模型。
- 夹具与测试：`tests/fixtures/analysis-run/provider-soft-timeout.json`、
  `valid-json-completed.json`；`test_deadline_budget` 6 项、model_registry 资格
  门禁 8 项、route-preview HTTP 一致性测试；全套 73/73。

## 2026-08-02：解析失败修复与运行可观测（wuli-analysis-run-observability-v1）

- 截断分类修复（T1/B1）：`output_truncated` 现在同时接受文本标记（`reached
  max_tokens`/`finish_reason`/`content_chars`/`reasoning_chars`/`output token
  limit`）与结构化信号（attempt 的 `finish_reason=length` 或
  `content_chars=0 且 reasoning_chars>0`）；reasoning-only 截断不再误报
  `candidate_no_change`，历史失败作业保留 `recorded_failure_type` 与
  `diagnosed_failure_type` 双分类。
- 结构化失败 envelope（T1/B2）：JSON adapter 失败向 stderr 输出脱敏的
  `WULI_AGENT_FAILURE_ENVELOPE:`（finish_reason/usage/content_chars/
  reasoning_chars/request_count，无 reasoning 正文/密钥），Gateway 并入 attempt
  并聚合 usage，失败作业不再 `usage=unavailable`。
- 复杂题预算（B3/B4）：目标数 ≥5、evidence 裁剪或契约 >4KB 的复杂题使用
  `max_tokens=30000` 并强制 `thinking=disabled`，决策写入 `request_preflight`；
  预算保护不变（实质消耗或 >30s 不自动完整重跑）。
- 分析运行报告（A3/D）：新增 `wuli.analysis-run-report.v1` schema 与只读脚本
  `teacher-console/scripts/analysis_run_report.py`（--job-id/--entry-id/--latest/
  --format markdown|json/--verify；退出码 0/2/3/4；计数语义区分 logical stage /
  provider attempt / upstream request / rollback / supplemental）。
- 脱敏夹具（A1）：`tests/fixtures/analysis-run/reasoning-only-length.json` 固化
  本次失败事实（finish_reason=length、content_chars=0、reasoning_chars=18547）。

## 2026-08-02：收口提交自包含 + 视觉服务去重 + 路由快照 + 静态图显式入口（wuli-mimo-deepseek-consistency-closure-v1）

- 提交自包含闭包（C0/C1）：基线报告 `docs/reports/mimo-deepseek-closure-dependency-baseline-v1.json`
  坐实 026b104 不自包含；归位 19 个未跟踪运行模块与验收测试/夹具后，`git archive HEAD`
  解包可导入、可跑严格测试（C1.3/C1.4 通过）。
- 视觉应用服务（C2.1-C2.5）：新增 `visual_application.run_visual_extract()`，
  网页上传与薄 CLI 共用同一编排并输出 `wuli.visual-extract-outcome.v1` 与脱敏
  调用账本 `visual-extract-request.json`；移除 source.clean 的
  `vision_images/requires_vision` 与 Gateway `_maybe_vision_preprocess`，
  同一 source fingerprint 默认只发起一次 MiMo 视觉调用。
- 作业路由快照（C3）：入队冻结 `wuli.route-snapshot.v1`，执行前校验配置 digest，
  变化即 `route_snapshot_stale` 失败关闭；job public 序列化含快照。
- 静态图显式入口（C4）：`POST /api/entries/<id>/build-diagram` 与
  `teacher-console/scripts/entry_action.py build-diagram` 共用
  `diagram_application.build_diagram()`（题干已批准+答案存在才运行；scene→SVG→硬门
  →至多一次 Patch→一次非阻断 MiMo 软评审）；改动 `explanatory.svg` 使旧答案批准
  失效回到答案复核。
- 严格测试入口（C5.1）：`run_tests.py --strict` 将 missing/skipped 计为失败；
  修复 test_agent_http 的 W3 shadow 夹具（claude solver/verifier 身份 + 强制
  adapter，与 E2E 同模式），4 个失败清零。验收 18/18，全量 70/70。

## 2026-08-02：MiMo–DeepSeek 网页/CLI 视觉协作一致性（wuli-mimo-deepseek-consistency-v1）

- 模型注册表 trait 路由 fail-closed：`resolve_model_id_for_trait` /
  `model_config_for_trait` 要求候选声明目标 trait；cost tier 覆盖不再把
  `vision + economy` 解析到纯文本模型（回退 `defaults.vision`），显式指定
  缺 trait 模型返回稳定错误。
- 新增视觉探针 `wuli.vision-probe.v1`：与生产提取同一 endpoint/图片/JSON 契约，
  使用 `teacher-console/tests/fixtures/visual-routing/` 合成图；结果持久化为
  `probe.vision`，`vision_probe=failed` 的模型被排除出视觉路由，公开状态如实
  报告（不显示为“视觉测试通过”）。
- CLI 默认视觉路由改为模型注册表 `defaults.vision`：`process_uploads.py start`
  `--source-review-mode registry` 只调用确定性薄 CLI
  `teacher-console/scripts/entry_visual_extract.py`，与网页上传共用
  `extract_visual_facts → stage_visual_extraction`，产出同形工件；旧
  `VISUAL_REVIEW_*` 边车保留为显式兼容 override（`legacy-adapter`）。
- `/api/health` 新增运行身份：`runtime_identity` / `runtime_identity_snapshot` /
  `runtime_stale`（`server_started_at`、`code_digest`、`analysis_route`、
  `route_config_digest`、`model_registry_digest`）；页面顶部显示只读“服务需重启”
  横幅，不自动重启、不阻断编辑。
- 新增固定测试运行时 `teacher-console/scripts/run_tests.py`：从项目根任意目录
  可复现运行验收单测，不再依赖偶然的工作目录（Pillow 缺失会快速报告）。

## 2026-08-02：生产解析收敛为统一核心求解

- 新增 `wuli.core-solve.v1` 与轻量 Target Brief：一次 Flash 调用只返回所有小问的最终
  结论、决定性推导、条件和复算检查，不再默认生成阶段接口、Solver B、仲裁、Claim
  Ledger/证书链或教学 Markdown。
- Core Gate 绑定题干目标摘要、拒绝漏目标和待定答案，并按
  `high_school_standard` / `olympiad_official` 检查方法范围；教学 Markdown 改由
  确定性 renderer 生成，最终答案逐字来自核心产物。
- `analysis-production-routing.json` 默认 `core-first`，旧 W2/W3 自适应链保留为
  `legacy-adaptive` 显式回滚；核心失败后不重复 W2 求解。
- 静态物理图从首次答案组合事务中移出，成为答案后的可选增强；无图不再阻塞答案审核
  或交付，但所有实际引用的图片仍执行路径与存在性校验。
- DeepSeek 直接 API 不再隐式写入 `thinking=disabled`；只有显式环境设置才覆盖服务端
  默认，避免新版 Flash 被旧适配器参数降级。

## 2026-08-02：新版 Flash 全年最小门禁复测

- 新增 `flash_competition_direct_eval.py` 隔离盲测入口：直接调用
  `deepseek-v4-flash` API，不使用 W3 拆解、RAG、风险 verifier、Solver B、仲裁、
  阶段接口或首次教学渲染；核心契约只保留全部小问、最终答案和每问至多四条关键关系。
- CPhO 2021 全年 8 题在候选冻结后由官方标准答案代替教师审核。6K 核心契约首轮
  结构成功 7/8，成功题耗时 4.6–11.2 秒；第 7 题首轮输出截断，10K 预算重试后
  7.2 秒恢复，因此首轮 90 秒端到端通过仍只计 7/8。
- 严格整题正确率和教学批准率均为 0/8。最小契约证明复杂首次渲染门禁可以后移，
  但新版 Flash 仍不能替代标准答案/隐藏真值正确性门禁，也不能切为无人值守生产默认。
- 评测包仅保存冻结摘要、哈希、耗时和错误原因码，不打包题目、候选、标准答案、
  API key 或私有模型注册表；失败遥测新增实际调用耗时，避免截断样本丢失 SLA 证据。

## 2026-08-01：静态解释图改为独立强类型物理场景任务

- `analysis.generate` 不再要求 2–6 个逻辑节点，也不再默认生成流程图；兼容字段 `diagram` 固定为 `null`。
- 新增 `diagram.scene` / `wuli.physics-diagram-scene.v1`：MiMo 提供经复核视觉事实，DeepSeek 只规划场区、对象、轨迹和标注，本地确定性渲染 SVG。
- 新增物理语义门控与覆盖账本：高置信非标签物理事实必须实际绑定图元，高置信标签必须绘制或显式说明省略；带电粒子题强制具有场区、轨迹与粒子/关键点。
- 答案与物理图按组合事务处理；图生成、语义门控或安全校验失败时恢复原候选，禁止流程图 fallback。
- 原流程图渲染器移入显式 `logic-flowchart` 可选插件，默认链和 W3 候选均不自动选择它。
- 物理路径新增独立 `geometry` 合同与真实三点圆弧 SVG，标签采用确定性近似避碰，场纹理和箭头降低视觉权重；两个 fake adapter 已同步新合同。
- 周期 B/E 时间图和空间投影进入生成提示与语义门控；全量 588 项测试通过、4 项跳过。
- 两道真实题的两轮隔离回放仍未通过：圆弧三点近共线、周期/空间内容被模型拆成超过合同上限的 4 个面板。因此本批候选尚未完成生产验收，不记录为图像质量提升完成。
- 将图形门控从“整图驳回重生”改为“裁决＋最小修订”：失败候选只保留在 Gateway 隔离区，输出带稳定错误码和 JSON Pointer 的诊断；可修订错误最多触发一次 `wuli.physics-diagram-scene-patch.v1`，Flash 只能提交受限 JSON Patch。越界、无进展、再次失败及来源/安全硬门均立即停止，正式条目仍只接收完整通过门控并确定性渲染的 SVG。
- 新增 `wuli.diagram-obligations.v1` 前置义务编译：周期电磁场固定为 `motion + b-time + e-time` 三槽位，空间投影合并到 motion，避免 Flash 在门控后才发现漏图或扩成第四面板。预检不再遇到首个圆弧即停止，而是一次汇总全部路径几何、面板和可判定语义错误；Patch 轮只注入候选、诊断和义务清单。
- 两道冻结真题的真实 DeepSeek V4 Flash API 回放：磁场偏转题首轮同时发现两个退化圆弧，一次 151-output-token Patch 后完整通过；交替周期场题在义务前移后首轮直接生成三面板并通过语义门、安全渲染和 provenance。该结果验证结构收敛，不代表视觉美观已批准；周期场运动面板仍可见轨迹/文字重叠，需后续 MiMo 软视觉诊断或教师复核。
- 图像生成进一步改为语义素材编译：新增版本化组件目录，素材保存“平行板、粒子发射、场区、事件轨迹段、方波、坐标系”的物理含义与组合责任，不保存固定 SVG 碎片。DeepSeek 负责教学配方；存在 `physics-model.json` 时本地编译器从事件段确定性投影轨迹并纠正 P/Q 边界顺序。门控缩减为来源、拓扑、模型和安全/provenance 四类硬门；周期图标注、空间投影说明和美观排版降为软提示。新增回归覆盖“事实同时绑定和省略”、上下板反置以及自由轨迹被模型轨迹替换。
- 修复磁场轨迹“点对了、形状却不明显”的表达缺陷：三面板布局给运动面板双倍宽度，x-y 投影采用等比例缩放；解析圆弧直接输出真实圆弧，离散模型点通过确定性共圆检验后才能提升为圆弧。不同问次使用红/紫轨迹与同色方向箭头，并从模型补充换向点、圆心和半径构造线；z 方向只保留“位移另计”的投影说明，避免伪透视扭曲圆周几何。

## 2026-07-31：W3 Flash 紧凑契约、直接 API 与失败即停

- OpenAI-compatible adapter 将 Flash 的大型 `wuli.solution-reasoning.v2.1.solver-*`
  调用拆成核心结论与阶段接口两段紧凑契约；两段合并后仍按原始完整 schema 校验，
  并对方向、状态键和相邻阶段转换做确定性规范化。
- DeepSeek 兼容端点可直接执行 W3，不经过 Claude Code；关闭不受支持的 thinking，
  使用 JSON object 响应并累计两段 token 遥测。最小 JSON 修复只处理可唯一定位的
  字符串内部未转义引号，不吞掉缺逗号等一般结构错误。
- 自适应路由新增 `w3_failure_policy`。当前生产配置为 `stop`，W3 失败后记录
  `W3 → none` 并停止，不再重复求解 W2；`fallback-w2` 仅保留为显式回滚项。
- CPhO 2021 全年 8 题隔离网页盲测在候选冻结后才读取标准答案，并由标准答案代替
  教师审核：结构成功 8/8，冷启动等价均不超过 90 秒（中位 55.0 秒、最慢 88.0 秒），
  但严格整题正确仅 1/8、教学批准 0/8。直接 API 已消除结构瓶颈，但 Flash 内容质量
  与 verifier 的共同错误尚未达到生产切换标准，因此不修改正式模型注册表默认路由。

## 2026-07-30：W3 Claude-only 独立验证与竞赛方法分层

- Claim verifier 仍按每批最多 8 条运行，但多批次默认以最大并发 2 调用；设置
  `TEACHER_CONSOLE_W3_CLAIM_VERIFY_CONCURRENCY=1` 可立即回滚串行。并发批次的
  检查点统一延迟到全部批次结束后写入，避免合法缓存写入触发兄弟 Gateway 事务的
  `canonical_changed`，canonical 门禁本身没有放宽。
- W3 阶段遥测新增总耗时、provider 耗时、框架开销、尝试次数和 Claim 批次编号。
  同一弹簧题复用相同分解/Solver 检查点的真实 Claude A/B 从 190.26 秒降至
  67.81 秒（-64.36%），19 个 Claim、22 份证书及 W3R 六项门禁保持全通过。
- 新增语义审计最小化评估。当前兼容投影的自由文本中间关系没有可执行确定性
  `check_spec`，因此弹簧题 18 个语义请求的安全可删数为 0；系统不会以删证书换速度，
  后续必须先扩展 Solver 的机器可检查 Claim 契约。
- 新增独立于分解模型的源题领域硬义务：显式“首次/最早”“所有/全部”、定义域/边界、
  参考系，以及既有多流体完整润湿区间，都在 Solver 前补入缺失验证义务，降低错误
  Blueprint 同时污染 Solver 和 verifier 的风险。
- Claim Evidence 启用时，W3 Solver 与 claim verifier 只允许使用 `claude` provider，
  默认绑定 `Deepseek-v4-pro` / `Deepseek-v4-flash` 两个不同模型身份；同模型自证和
  非 Claude provider 在运行前失败关闭。
- `wuli.solution-reasoning.v2.1` 新增逐阶段接口、相邻阶段状态转换和事件引入状态；
  `wuli.claim-verify.v2` 同时审核 Claim 与语义接口，单批最多 8 条；聚合器改为消费真实
  确定性接口检查，不再对所有实时题目硬编码 `legacy-stage-interface-unavailable`。
- Claim Evidence 启用时取消重复的旧目标 verifier、Solver B 与仲裁调用；中间 Claim
  使用单一独立语义证书，关键最终 Claim 仍要求本地聚合证书与独立 verifier 双路径。
- 新增 `high_school_standard` 与 `olympiad_official` 方法 profile。竞赛 profile
  允许官方竞赛常见的微积分工具，但仍禁止未经来源授权的大学分析力学形式。
- W3 报告和 29 日隔离评测分别记录求解执行、Proof 忠实性、方法合规和教学渲染，
  避免把“答案正确但方法不符合当前教学口径”统计成求解错误。
- IPhO 多流体静力复测暴露 Solver 与 Flash 共享错误拆解、共同漏掉轻流体高出外侧
  液面的板段。新增来源触发的自由液面/完整润湿区间/密度比硬义务后，简单密度差式
  会被拒绝，正确式 `wg h²ρ₀(ρ₀-ρ_oil)/(2ρ_oil)` 通过。弹簧题与该题的 Proof、
  接口、方法及 W3R 门禁均通过；复杂 IPhO Pro 调用仅在临时 1.00 美元上限下完成，
  正式默认 0.50 美元不变且超限失败关闭。
- 详见 [`reports/w3-claim-concurrency-20260730.md`](reports/w3-claim-concurrency-20260730.md)。

## 2026-07-29：W3R 非求解教学渲染与忠实性门禁

- 新增版本化 `wuli.w3r-brief.v1` 与 `wuli.w3r-render-result.v1`：只有整体验证为
  `VERIFIED` 的 Proof Package 才能投影，Proof Skeleton 只保留最终 Claim 及其已验证
  祖先；缺少目标、证书、条件、依赖或已闭合义务时返回 `needs_render_material`。
- 新增独立确定性学生版/教师版 renderer 和 Render Gate，检查最终答案、Claim span、
  条件、目标、LaTeX、公式来源与高中方法；纯表达失败最多对同一 Brief 重试一次，
  Claim 漂移、条件遗漏或无来源公式立即拒绝，禁止回退 Solver。
- W3 只新增 shadow 字段，不改当前 `recommended_student_solution`、生产候选路由、
  教师批准或公开发布。现有 legacy stage-interface 仍为 `PROVISIONAL`，因此实时
  W3R 会失败关闭而不是越权渲染。
- 冻结 5 个基线来源和 2 个结构化 Brief 条件矩阵；同 Brief 配对评测的 Final Answer、
  Claim Support、Condition、Target、LaTeX 均为 100%，Unsupported Claim Rate 为 0，
  但生产默认资格明确保持 false。

## 2026-07-29：默认物理义务 shadow 试验暂缓

- 新增默认义务 shadow 统计脚本和报告，回放旧 W3 blueprint 中“题干未限定唯一/首次时，
  是否应枚举全部物理解支”的建议触发情况；脚本只读，不重新调用 Solver，不改
  `verification_obligations`，不影响 `VERIFIED`、评分或交付。
- 首轮 23 个 W3 报告中 7 题触发、共 10 条建议，说明规则有诊断价值；但“最高点时刻”
  “初速度竖直分量”等疑似过宽命中表明当前不具备升级硬门禁条件。
- 已将后续方向沉淀为 `docs/github-issues.md` 的 Issue 15：先人工标注
  `useful / false-positive / already-covered`，再收窄规则并评估是否进入教师软提示。

## 2026-07-29：解题 loop 架构沉淀与 Gateway 契约自检

- 新增 `docs/解题loop.md`，把复杂物理题中的发散、假设提出、检验、否定和重构整理为
  有限 Agent 状态机：原子任务、冻结输入、Challenge 回跳、影响锥、Hypothesis Pool、
  证书汇总和硬熔断各有边界。
- 明确“最终证明图是 DAG，搜索过程可以有环”：随机联想只选择可证伪搜索算子，不能直接
  改写证明 DAG 或晋升真值；停滞、缺证或预算耗尽只能输出 `PROVISIONAL/UNRESOLVED`。
- Agent Gateway 新增任务路径契约自检：允许输出若被 denied pattern 覆盖，启动前返回
  `task_contract_invalid`，不调用 provider。IPhO 首轮 `candidate-answer.json` 范围冲突被
  沉淀为“验证器也必须先验证自身契约”的工程红线。

## 2026-07-29：2021 IPhO 理论卷隔离整卷测试

- 新增可重放的 IPhO 闭卷评测脚本：官方英文题面与解答分区存放，三道候选全部冻结后才
  允许解锁官方解答；实验题按范围排除。
- 2021 IPhO T1–T3 共 36 个小问完成完整作答和独立逐小问阅卷，得到 30/30 分、
  36/36 `full-credit`，无教师待裁决项。
- 每题分别记录有效作答时间、脚手架失败消耗、独立阅卷时间和 provider 用量；求解 provider
  未返回 token 时明确标记不可得，不以字符数估算。首轮脚手架范围冲突造成的三次失败记录
  和 281.260 秒消耗被保留。

## 2026-07-29：W3 自适应复杂题路由默认启用

- `wuli-analysis-adaptive-v1` 从限量灰度切换为 `mode=default`：确定性初筛命中的
  复杂题进入 W3，低结构风险题继续使用 W2；调用、延迟、教师核对卡、阶段完整性、
  候选校验或高中方法门禁失败时自动由 W2 接管。
- 新鲜 holdout 5 题/15 目标中 W2 与 W3 均为 100%，独立性完整；首批生产灰度
  Q13/Q14/Q25 共 9 个目标正确，补充 IPhO、APhO、全国赛三题共 8 个原子目标正确。
- 实际完成 `default → off → default` 回滚演练：同一复杂题探针依次路由
  `W3 → W2 → W3`，最终配置恢复 `default`；provider、候选隔离和教师审批边界未变。
- APhO Q3-A8 与全国赛首版摘录暴露 `source-adaptation-incomplete`：官方 PDF
  可信不代表人工原子摘录必然条件闭合，后续评测须独立核对原题—摘录条件保真。
- 全量 385 项单元测试通过、4 项因沙箱 loopback 限制跳过；lifecycle、
  visualization、publication、claim-evidence 四个隔离 E2E 场景全部通过。

## 2026-07-29：断言级正确性证据链与受控认知环

- W3 新增默认关闭的 `claim_evidence_shadow_v1`：把 Solver 关系投影为版本化 Claim
  DAG，显式绑定条件、依赖、题目目标和验证义务；Agent 只能提交候选，晋升状态由
  确定性编排器根据当前版本证书重算。
- 新增受限算术、量纲、区间、边界与事件顺序检查器，以及上下文隔离的
  `wuli.claim-verify.v1` 语义复算；缺证、旧证、自验、冲突和 `insufficient` 均失败
  关闭，不能靠多 Agent 投票决定物理真值。
- 新增跨阶段接口检查、Challenge、最小冲突定位、依赖定向回跳、任务指纹去重和硬
  熔断。随机联想只选择下一种有限证伪算子，Hypothesis Pool 不进入证明 DAG；
  停滞或预算耗尽只输出 `PROVISIONAL/UNRESOLVED`。
- 教师端 W3 私有快照和折叠式证据账本可显示完整暂定答案、全部 Claim、证书与未决
  义务；运行时身份、内部指纹和原始语义审计被裁剪。影子运行不改 canonical 答案、
  审批、交付或学生站。
- 固定 14 类故障注入全部检出且错误晋升为 0；完成认知环 off/on 同条件消融和 5 道
  旧题只读投影诊断。旧题诊断发现 1 题当前答案摘要与旧 manifest 不一致，因此历史
  分数不能直接复用。
- 当时生产默认仍为 W2，WAIT-5 新鲜可用题为 0/5；该历史阻塞已在同日后续 W4
  新鲜 holdout、生产灰度和默认启用中解除。

## 2026-07-27：分段场释放时刻补全与逐案例停止事件

- 修正“交替电场与磁场中带电粒子的分段运动”第三问：完整枚举 $0\sim6\tau$
  的六个释放区间，并继续按题图规律追踪 $6\tau$ 后的 $4E_0$ 电场；除
  $t_0=\tau/2$ 外，新增 $t_0=13\tau/3$ 的有效释放支。
- 学生版、教师版、共享 `physics-model.json` 与解释图同步更新；旧答案批准和旧仿真
  批准自动失效，必须先复核新答案，再重建并复核交互仿真。
- 二维分段场渲染器支持 `event_model.cases[*].stop_event_id`，不同解支可以各自高亮并
  暂停在真实捕获事件；未设置时继续使用顶层 `event_model.stop_event_id`，兼容旧模型。
- 每个解支的进度条改为从该解支的实际释放时刻开始，并按分段结束时刻稳定判定当前阶段；
  因此 `13\tau/3` 释放支在 0% 显示第三次电场加速，在 100% 显示第四次电场首次命中
  $M$，不再从全局 `t=0` 空等或在起点误报最后一段。
- 教师端“重新构建预览”新增显式强制重建；模型摘要未变但模板代码升级时也会刷新 HTML、
  ZIP 和运行时截图。命令行可用 `prepare-visualization <entry-id> --force` 完成同一操作。
- 仿真模型校验在运行环境缺少可选 `jsonschema` 时主动降级为确定性结构与领域校验并给出
  warning，不再把合法模型误判为构建失败；安装依赖的环境仍执行完整 JSON Schema 校验。

## 2026-07-27：难度评分器 v8 组合拓扑与必要计算链

- “过程与状态复杂度”不再统计场类型或阶段数量，改为评价学生已知正确知识点后仍必须
  完成的状态与过程组合：单一状态、标准串联、时序组合、同步耦合、分支耦合、嵌套全局
  组合分别使用 `0.8/1.6/2.5/3.4/4.2/5.0` 固定锚点；完全相同的周期重复继续折叠，
  截断段、多方向同步、多对象同步和独立路径分支不再被折叠。
- “运算与表达负荷”改为正确建模后最短标准路径上的必要计算量：以最重计算链为主，
  根据不可复用的非线性表达、参数函数、联立消元、分段/分支计算和范围/边界求解作有限
  修正，不按公式行数、答案篇幅或教学展开步数累计。
- “条件与完备性”定义不变，但会读取目标完成切片中绑定决定性关系的 `audit` 节点，
  修复空间命中、时间窗口、解集完整性与唯一性已经在解题图中出现却未进入评分证据的问题。
- 23 道题经只读回归后由 v7 迁移到 v8；题库中位数由 53 调整为 56。交替场分段运动、
  轴向螺旋圆柱壁临界、三维复合场共圆心三题分别为 `79/66/82`，提升来自 D/E/F，
  未修改 W3 解题契约、知识深度标杆、知识模块口径、模型调用或教师门禁。

## 2026-07-27：难度评分器 v7 最小充分模块校正

- 知识整合改按“完成标准解法所需的最小充分知识模块集”计数：同一磁场圆周运动模型中的
  `qvB=mv²/r`、`r=mv/qB` 和回旋半径计算只计一个模块；相切/临界几何、非标准
  转角—时间映射、外部释放/相位时序仅在其决定性关系不可绕过时分别计数。线动量的
  水平/竖直分量仍只计一个模块，线动量与角动量则分别计数。
- 通电导体的宏观 `BIL` 归入安培力模型，不与其微观洛伦兹力解释重复计数；普通图像
  读取不再冒充竞赛标杆中的“实验连续谱与模型检验”。
- 过程复杂度只计不同物理状态模板；同类场区的重复进入、正负场切换、周期重复和终点
  判定会折叠，三维/多对象耦合只作小幅修正。条件维度从决定性关系识别真实的临界、
  首次、分类或全局完备性负担，不再按 W3 审核标签数量累加。
- 13 道旧题补齐经题干摘要锁定的规范化标准解题路径，全库 23 道已按 v7 重算并同步
  学生端难度摘要；该维护不增加模型调用、解题门禁或发布门禁。

## 2026-07-27：难度评分与 W3 解题主链解耦

- 正式 W3 双层蓝图恢复为纯解题契约，不要求 `cognitive_operation`、
  `knowledge_units` 或 `type_distance`；评分字段缺失或错误不会拒绝蓝图，也不会进入
  检索、Solver、验证或仲裁上下文。
- 保留独立的 `difficulty-shadow-v1` 影子实验契约，可在成对非退化评测中输出认知枚举、
  关系绑定知识单元和题型距离；规范化后强制拆成纯解题蓝图与私有评分标注，二者不混传。
- 正式评分改为解题后的单向证据投影：优先读取 Solver 支持关系、验证器决定性检查和
  仲裁关系，再按固定知识单元注册表确定性去重；没有解后证据时才保守回退蓝图关系。
- 线动量的不同分量、重复使用和代数变形只计一个单元；线动量与角动量等独立规律分别
  计数。未知关系降低评分覆盖度，不阻断答案或增加教师门禁。

## 2026-07-27：知识深度固定标杆与概念关键路径

- 固定 A–O 知识深度标杆集：A–J 反校后覆盖 `1.0–3.7`，竞赛题 O/M/N/L/K
  覆盖 `4.2/4.6/4.8/4.9/5.2` 的内部校准坐标；正式六维评分仍统一封顶 5，
  超过 5 的标杆只用于保持高端校准间距。
- 难度影子契约允许 `reasoning_steps` 提交 `cognitive_operation` 枚举并绑定
  `decisive_relations`；该标注只用于成对验证，不进入正式 W3 解题主链。评分器按目标
  反向切片，纯代数、重复描述和未被下游消费的无关细节不能抬高知识深度。
- 知识深度由最长不可绕过概念链确定，再经过固定标杆曲线换算；内部越过 5 还必须
  包含第一性原理重建、至少四个有效概念节点，并由独立验证器通过。条件不全时确定性
  降级，不新增教师操作门禁，也不增加模型调用。
- 旧 W2 标准路径继续保守评分，但因缺少显式认知枚举而不能进入 5 分段。教师端、
  学生端和总分仍使用统一 `0–5` 量尺，教师校准优先并保留自动基线和内部轨迹。

## 2026-07-26：客观难度评分器 v4

- 六维中的“建模与表征转换”升级为“题型距离与建模转换”，使用教材母题、常规变式、标准迁移、模型重构、隐蔽桥梁、非常规构造六级固定锚点；Agent 只提交枚举和可复核依据，不直接提交该维分数。
- 知识深度改按去重后的独立推导类型评分，过程复杂度只看真实物理阶段与状态转移，运算负荷改看单步局部关系密度，消除同题因答案拆成 2 步或 4 步产生显著分差的问题。
- `analysis.generate` 的 `standard_solution_path` 新增 `type_distance`；W3 旧蓝图通过固定信号投影到同一契约，不建立第二套评分逻辑。

## 2026-07-26：W4-1 真值冻结与 W4-2 成对回放

- W3 评测新增 `status`、`seed --fresh-only` 和 `freeze-truth`：排除所有曾进入旧
  W3 manifest 的题，并要求至少 5 题、12 个教师批准目标在影子运行前写入不可覆盖的
  `truth-lock.json`。
- W4-1 评分会复核教师答案、逐目标真值和锁文件摘要；任何变化或目标集合不一致都
  失败关闭。影子运行入口可绑定 `--experiment`，没有有效真值锁时不会消耗模型调用。
- 当前 14 道教师复核题全部属于旧实验，新鲜样本为 0，尚缺 5 道；机制完成但生产
  门禁保持关闭，不复用旧题制造上线结论。
- 新增显式 `--reuse-case` 回放对照组：旧题可以进入冻结、评分和成本回归，但始终
  标记为 `replay`，不计入独立 holdout。首批 5 题、15 目标 W3 回放为 15/15，平均
  内部调用 4.4、平均教师核对卡 0.4；旧报告的教师稿 baseline 不再冒充 W2 生成成绩，
  生产门禁仍关闭。
- W4-2 新增固定 `routing_tier`、`model_id` 的真实网页 W2 候选和 `paired-score`：
  只有浏览器点击来源、candidate evidence、相同模型/路由与完整逐目标标签才可配对。
  首个 replay 样本修复后 W2/W3 均为 3/3，W2 交付质量通过；准确率增量为 0，
  不据此宣称 W3 优于 W2。
- 解析物化补齐 compact 分数与 JSON 控制字符的原子修复：`rac34`、form-feed +
  `rac12`、tab + `frac34` 会恢复为合法 `\frac`，ANSI 序列被清除，其他控制字符
  失败关闭；`\dfrac/\tfrac` 同步规范化。
- `method_check.decisive_relations` 单项上限从 120 放宽至 360 字，允许保留完整可复算
  关系；成对网页重跑会保留上一轮成功候选，失败尝试单独记录，不再破坏评测基线。
- W4-2 已扩展至全部 5 道 replay、15 个目标：W2 为 14/15，W3 为 15/15，W2
  可交付性通过 3/5。W2 在三维电磁题漏掉 `z0=l、任意 x0` 同相位分支，并在双区域
  磁场题留下首次性证明缺口；其余三题准确率与交付质量均通过。
- 唯一准确率差异目标曾由旧 W3 输出推动教师补充真值，因此 +6.67 个百分点只作
  replay 诊断，不作泛化或上线证据；生产资格仍等待新的独立 holdout。

## 2026-07-26：题目难度、可视化复核与冲突审计

- 客观难度评分升级为固定锚点的标准路径量表：只读取已复核题干与规范化最短高中解法，不再把答案篇幅、公式文本、物理模型规模或运行过程混入题目难度；六维权重调整为 `20/15/20/20/10/15` 并保持纯加权平均。
- `analysis.generate` 的私有方法自检新增物理阶段、必要推理步骤、决定性关系、表征转换和条件检查，并持久化为统一 `standard_solution_path`；缺少标准路径时明确显示待评分，不生成伪精确分。
- 教师难度校准继续优先且不增加门禁，同时保留自动基线、变更维度和校准依据；只有题干或标准解题路径变化才令旧校准过期。
- 教师端“重新构建预览”现在具备幂等保护：当前模型已有可用预览时直接返回原版本，不重新构建、不撤销教师审批；前端同时正确区分 `unchanged`、`ok` 与失败状态，避免误触后显示“未形成可用的交互可视化”。
- 教师端解析复核中的“建议重点核对”改为默认收起的原生可访问折叠区，仅显示重点数量；教师按需展开后仍可查看最多两项决定性核对提示，W3 审计数据和审批流程不变。
- W3 冲突卡片新增按目标展开的只读审计详情：主候选、独立复算、差异候选、仲裁结果和决定性关系均可逐段复制；候选结论可一键填入教师“修改意见”，但不会自动改写或批准答案。

## 2026-07-26：W3 自适应复杂题推理进入影子验收

- “三维复合场双电子”新增整周期延迟交互预设：取 $x_0=0.8r$、$z_0=l$、$\Delta t=T$，页面实时显示两电子连线与恒定的 $AB/r=6.334$；运行检查现在逐一切换所有情形，而不只测试默认预设。
- 3D 确定性模板同步视口约定：正视方向、$z$ 轴投影和无夹限俯仰拖拽恢复一致；粒子跨多段轨迹时只绘制当前末态标记，避免入口处出现重复粒子。
- 新增确定性复杂度初筛与 `problem.decompose` 双层蓝图，明确区分真实物理过程和求解推理过程；配套轻量 `decompose-physics-problem` Skill。
- 新增蓝图驱动的 3–5 路定向召回，最终继续复用 W2 `evidence-set-v2` 做统一选集、去重、冲突阻断和预算裁剪。
- 新增 Solver A、目标级收益审计、独立验证器、按风险触发的盲解 Solver B，以及基于证据和可复算关系的非投票仲裁。
- 结构化结果由确定性渲染器生成单一学生版候选，不额外调用模型；W3 内部阶段按输入/契约摘要保存私有检查点，失败重试不重复消耗已完成阶段。
- 验证器输入会删除历史答案正文和内部条目标识；教师端只显示最多两张“建议重点核对”卡，不暴露 unresolved 状态或多角色争论。
- 新增私有单题影子运行、冻结教师真值和独立 holdout 评测工具；W3 未通过目标准确率不退化硬门槛前不替换 W2 生产答案。
- 首轮文本一致性评分为校准集 16/16、holdout 15/16；教师随后确认唯一分歧是原参考答案遗漏的整周期同相位合法分支。新增 `valid-supplement` 目标判定，要求题干约束、可复算关系、边界枚举和教师确认全部具备，修订后数学正确率为 16/16。
- 参考答案若由本轮影子输出触发修订，会自动令 `independent_holdout_intact=false`、`fresh_holdout_required=true`；因此本轮仍不授予生产资格，默认继续使用 W2，必须用新的未见 holdout 验证 W3。

## 2026-07-26：RAG W2 主动证据集构建与验收

- 新增 `evidence-set-v2`：在单条精度门禁后，按证据槽位增益和路由多样性主动选集，并去除近重复、阻断引用间领域/几何/目标硬冲突。
- evidence pack 记录逐条选择、拒绝与上下文预算省略原因，避免把“策略选中但未物化”误报为已注入模型。
- 独立 holdout 上 Recall@5 `1.0000`、MRR `0.7639`，精度门禁与最终集合的相关证据保留率均为 `1.0000`，选中近重复对/冲突对均为 `0/0`。
- 中等、较难、挑战三档真实网页候选的关键结论均与教师复核答案一致；较难题无可靠引用时安全降级为空证据。
- W2 验收只授权继续扩大影子答案样本；生产检索与证据选择默认仍为 baseline，不自动切换。

## 2026-07-26：RAG W1 独立 holdout 与证据精度门禁

- 检索评测正式区分 calibration 与独立 holdout；旧 30 条固定集只作 calibration，策略切换还要求至少 12 条覆盖四类查询的教师批准 holdout。
- Knowledge Store 为每条候选输出共享词、共同适用条件、冲突条件和确定性精度决策；跨领域、显式几何及求解目标冲突可被候选门禁剔除。
- 新增只供离线 `web-candidate` 使用的 `precision-gated-v1`；全部候选低精度时降级为空证据，生产 baseline 不变。
- W0 较难反例的真实网页候选由 3 条低精度历史引用降级为空证据后，恢复教师稿的平均速度 `2v₀/(3π)` 和首次相遇时间 `3πm/(qB)`；该单例只证明错误被止住，不授权上线。
- 教师批准 12 条独立 holdout 后，baseline 与 intent-augmented 均达到 Recall@5 `1.0000`、MRR `0.7639`，条件审计相关证据保留率 `1.0000`；multi-route Recall@5 仅 `0.9167`，继续停留实验态。

## 2026-07-26：答案评测支持无 RAG / 当前 RAG 三组对照

- 网页评测可分别注入空历史证据包和冻结的当前 RAG 证据包，其他网页操作、模型与生成契约保持一致。
- 每轮私有评测保存实际注入的脱敏 evidence 快照、SHA-256、引用数和生成 provenance。
- 报告区分 `direct`、`web-no-rag`、`web-current`，并为未来真实精排策略预留 `web-candidate`；教师复核答案仍是唯一真值。
- 首个中等题样本中，无 RAG 组误判 B、D，当前 RAG 组使用 3 条引用后恢复教师稿 A、C；该结果只作为个案，不授权策略切换。
- W0 补齐中等、较难、挑战三档真实网页对照后，current-RAG 为 1 次获益、1 次事实性退化、1 次结论持平但格式退化；生产默认策略保持不变，下一门禁转向独立 holdout 与证据条件相容性。
- 评测器强制校验 `web-no-rag` / `web-current` 的 `evidence_mode`，旧零引用网页候选不再能冒充真实 RAG；解析物化同时修复 JSON 转义导致的裸 `rac{…}` LaTeX 残片。

## 2026-07-25：答案质量三层成对评测

- 新增同题 `direct / web / teacher-reviewed` 对照，直出答案只作基线，教师复核答案作为真值；
- 按五档客观难度分别报告选项一致性、关键公式保留、关键物理纠正和语义修改比例；
- 网页产物只从 `.agent-baseline` 捕获，缺失时明确标记未就绪，不允许用最终答案冒充；
- 首批固定清单包含 7 题，覆盖较易、中等、较难和挑战档，待在隔离题库中补齐同模型成对生成。

## 2026-07-25：解析重生成绑定已有物理模型

- 修复已有物理模型条目重新生成解析时的语义回归：`analysis.generate` 现在读取
  `physics-model.json`，确定性拦截与模型明确选项判断冲突的答案，并保留已有物理过程
  SVG，避免被通用解题流程图覆盖。
- 以“磁场偏转与电容器的电荷累积”为回归样本，恢复“返回 Q 后继续进入磁场”的完整
  事件链、真实二维轨迹图和 `BCD` 结论。

## 2026-07-25：多路 RAG 召回框架进入影子评测

- Knowledge Store 新增可审计查询计划，以及标签、题干、解析三条独立 BM25 路由；
- 条目层新增 RRF 融合贡献、各路候选数和影子前列结果，不改变统一 `build_agent_evidence()` 契约；
- 固定检索评测新增 `baseline|multi-route` 策略切换。首版多路 Recall@5/MRR 为
  `0.7875/0.8561`，低于基线 `0.8292/0.8633`，因此生产检索继续使用基线排序，
  实验策略不得自动激活。
- 新增 `intent-augmented` 影子策略：保留基线前三名，并从去除教师任务套话后的
  意图视图补入最多两条候选。同一固定集 Recall@5/MRR 达到 `0.8653/0.8650`，
  `teacher_phrase` Recall@5 达到 `0.7321`；达到离线门槛但仍等待教师确认激活。
- 查询结果和 Agent evidence 新增候选证据集合诊断，报告标签、题干、方法槽位覆盖、
  精确重复计数和来源可回溯计数，不以低相关证据强行补齐槽位。

## 2026-07-25：客观难度评分器 v2

- 六维评分改为 0–5、步长 0.1；总分仍映射为 0–100 与五档难度。
- 评分改由题干、学生解析和可用物理模型的过程证据共同支撑；教师端展示各维证据摘要。
- 加入评分版本、输入摘要、校准状态与首个交替场粒子题（93 分）回归样本；批量回填不会覆盖教师校准结果。
- 学生端难度弹层收敛为“难度结论 + 六维雷达图”，六个方向直接标注维度名和 `/5` 分值，并采用响应式磨砂玻璃科技风格。
- 学生端题库与解析阅读页统一升级为科技感背景和玻璃卡片，支持跟随系统的明暗主题、手动切换及本地偏好记忆。
- 难度弹层改为浏览器顶层 Popover，并按视口自动选择题目圆点上方或下方，避免被玻璃卡片、相邻题目和页头的层叠上下文遮挡。
- 题目卡片上的实心数字圆点改为无数字的微型六维雷达标记；难度仍由五档颜色编码，具体总分只在顶层弹层中呈现。
- 难度入口进一步改为五档难度色实心背景与白色六维雷达图标，并增加轻微高光和描边，提升明暗主题下的辨识度。
- 教师端“题目难度”入口移动到学生版/教师版切换旁，点击后在当前答案页直接展开六维调节面板，避免折叠入口被编辑区挤出视线。
- 难度教师校准补充小数步长回归测试，明确 `3.7` 等十分位评分可保存；本地服务更新评分器后需重启，避免驻留旧版 `0–4` 整数校验。
- 结构化解析契约升级为 `wuli.analysis.v2`：生成前必须提交不公开的方法自检，优先高中范围内的最短路径；候选校验会阻断学生版中的积分、导数等超纲方法、缺失最短主线或超过五步的推导。
- 首次解析接入与答案返修相同的隐私裁剪 RAG 证据；Knowledge Store 将教师可编辑的知识点、错因、方法和二级结论纳入独立检索文档，并对常见教师表达做可审计的规范词扩展。30 条教师确认固定集上，Hit@5 从 0.8667 提升到 1.0000，Recall@5 从 0.7208 提升到 0.8292，MRR 从 0.7217 提升到 0.8633。

## 2026-07-25：教师反馈闭环与派生证据一致性

- 公开 SVG 改为有复杂度上限的 XML 解析器白名单，拒绝 DTD/ENTITY、外部资源、事件属性、活动元素、命名空间绕过和危险 CSS；仓库现有 SVG 全量兼容。
- Candidate Archive 改用 UUID，新增候选关联、结构化反馈和教师 `answer.save`；语义 diff 覆盖新增/删除/二进制产物，并自动推断采用等级。
- 答案与可视化反馈进入私有事件链，脱敏教训可供后续相似题 evidence 使用；知识点、错因、难度和年级继续作为可编辑建议，不增加独立确认步骤。
- Knowledge Store 查询改为严格只读，缺库/旧库/脏索引明确返回不可用；显式重建清除 freshness 标记。慢循环只比较不重叠观察期。
- RAG 观测使用最终批准评价与顶层 `AgentRequestOutcome`，新增返工率、首轮采用率和教师改动量；Evaluator 移除误导性的 `correctness`。
- `source.clean` 成功事件以可配置防抖合并索引刷新；服务关闭时强制 flush。公开目录上传时间降为日期粒度。

## 2026-07-25：客观题目难度量表

- 每题在题干稳定且学生版答案生成后自动得到 0–100 客观难度分与五档标签；六维为知识点深度、多知识点交叉、题型距离与表征转换、题目长度与结构、运算与规范表达、干扰信息与条件辨析。
- 教师端以可展开量表展示总分、各维评分、核心判断和难度总结，默认生效但可随时直接修改；题干或答案改变会重算，不给上传/OCR 阶段增加操作。
- 公开草稿只携带学生所需的难度摘要。学生端以五色圆点和精确分数展示，桌面悬停/聚焦、手机点击可查看磨砂六维雷达图与判断；公开更新仍须走既有隐私发布门禁。

## 2026-07-24：学生端按上传时间排序

- 公开目录条目新增不含内部标识的 `uploaded_at` 字段，来源为知识库条目的首次创建日期；不公开具体时间和时区，以后重新发布不会改变其上传顺序。
- 学生端目录页在题目数量摘要旁提供紧凑的“最新上传 / 最早上传”下拉按钮，搜索结果保持当前排序，并在题目卡片展示日期；后续可在同一入口扩展难度或题型排序。
- 老目录兼容缺少 `uploaded_at` 的条目，使用 `published_at` 回退；桌面与窄屏浏览器检查覆盖排序正确性和横向溢出。

## 2026-07-24：三维分段场粒子交互渲染器

- 新增 `piecewise-field-particle-3d` 离线确定性渲染器，支持立方体、平面、圆筒和通用线框区域，以及三维直线、匀加速轨迹、任意平面圆弧、螺旋线与多粒子同步运动。
- 三维轨迹在 `physics-model.json` 中保留解析参数，Canvas 只执行正交投影、旋转、缩放、事件播放和分层显示；不依赖 Three.js、CDN、远程字体或服务器。
- 交互新增立体/正视/俯视预设和拖拽旋转，同时复用二维紧凑布局、首屏播放进度与高级控制折叠。三维只用于第三坐标改变物理结论的题目，禁止把二维过程做成立体装饰。
- 教师端模型白名单、物理校验、静态契约和构建测试已同步接入；首批场景面向纸面外电位移、斜置磁场立方体、圆筒电子落点带和双电子螺旋运动。

## 2026-07-24：二维分段场粒子交互渲染器

- 新增 `piecewise-field-particle-2d` 确定性渲染器，统一表达矩形、圆形、半平面和多边形电/磁场，以及直线、圆弧、采样轨迹、多粒子、多案例、反射、暂停和截止事件。
- 学生端采用已批准的紧凑布局：桌面画布与控制并排，手机首屏保留画布、当前结论、播放和进度；案例横向切换，高级图层与控制默认折叠。
- 教师端白名单、模型校验、构建模板、静态契约和 Skill Schema 已同步接入；首批二维模型覆盖反射板与磁场、交变电磁场、磁聚焦/发散和方波电场回转四题。新增模型后仍须重新通过答案复核，再构建和批准最终 HTML/ZIP。

## 2026-07-24：Agent 结果统一与 evidence 轻量化预检

- 新增隐私最小化的 `AgentRequestOutcome`，统一 provider/模型、失败类型、实测 Token、尝试耗时、预算保护、检查点恢复和 evidence 预算；作业 API、Candidate Archive 与批量基准复用同一口径，旧作业保持兼容。
- Knowledge evidence 增加确定性预算审计和稳定内容哈希；预算只裁剪历史证据，不接触当前题干、答案或教师指令。
- 新增只读 `evidence_budget_benchmark.py`：至少 20 条教师批准样本、必须事实保留率 100%、中位字符节省至少 25% 才可进入成对模型评测；它不会调用模型或自动改变策略。
- `wuli.analysis.v1` 移除 Codex 结构化输出不支持的条件 `allOf`，改为全字段必需、分支字段可空并由本地规范化器执行条件校验；Schema 400 现在归类为 `structured_output_schema_invalid`，优先于旁路 timeout 日志，且不再误报已消耗推理预算或已完成 provider 降级。
- 复杂电学题的解析图生成准则改为优先抽象等效电路；不同材料段等实际对象需映射为电动势源、内阻、负载和测量端，并明确区分感应电动势与端电压。

## 2026-07-24：Agent 运行时、模型后端与工具边界完成统一

- 明确 `provider` 表示执行运行时而非上游模型厂商：Claude Code 可连接教师配置的兼容后端，但是否开放 Skill/文件工具由任务契约决定。
- `analysis.generate` 对 Claude/Codex 同样采用无工具结构化输出；答案返修和可视化才使用受限文件 Agent。OpenAI-compatible/LiteLLM 始终不运行本地工具，由后端确定性落盘、校验和构建。
- README、Gateway、API、LiteLLM 与运维文档补充兼容矩阵、第三方 Agent adapter 接入条件，以及“网页配置和终端配置彼此隔离”等排障说明。

## 2026-07-24：Claude Code Agent 支持每模型后端与密钥隔离

- 模型注册表中的 Claude Code Agent 现在保留 `base_url`、真实模型名和本地 API Key；任务启动时在隔离子进程中覆盖 `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN` 与 `ANTHROPIC_API_KEY`，不再静默继承另一模型的全局认证。
- provider 与模型厂商正式解耦：`claude` 表示使用 Claude Code 运行时，也可连接教师配置的兼容后端；文件工具是否开放由任务契约决定。`openai-compatible` 仍表示只返回结构化候选的单次 API provider。
- 模型测试摘要新增 Claude 地址与模型名，修改地址、模型或 Key 后旧通过状态自动失效；远程 Claude 兼容后端同样受 `privacy.allow_remote_agent` 门禁约束。
- 设置页按 provider 显示短说明，并禁用 Codex/JSON Adapter 不使用的 API 字段，避免“模型名是 DeepSeek、实际沿用旧 Claude 全局令牌”的误配。
- 本机 DeepSeek–Claude Code 配置使用各自保存的 Key 完成无学生数据文件能力实测；该私有配置不进入仓库。低成本模型只保留经基准验证的任务能力，不能仅凭 probe 通过推断它适合完整解析。

## 2026-07-24：教师工作台新增真实 E2E 驱动层

- 新增 3 条 Playwright 浏览器/API 场景，覆盖基础交付、交互可视化生成与复核、公开题图脱敏与本地发布；HTTP、生命周期门禁、仿真构建、文件导出和浏览器交互均走生产实现。
- E2E 每个场景使用独立临时知识库、输出目录和公开站，并用确定性 adapter 隔离 Agent 外部波动；测试不读取或修改真实题库，也不会由教师日常处理题目自动触发或录制。
- 单元测试与 E2E 的两个确定性 adapter 统一镜像 `wuli.analysis.v1`；结构化解析契约变化需同步更新两处替身并执行完整 E2E，避免单元测试通过而浏览器流程在候选物化前失败。
- `evaluator.py` 和 `pipeline_quality_eval.py` 接入测试断言层，分别核对领域评价以及内容、流程、Token、耗时诊断；它们不是 UI/API 驱动器。
- CI 在 `main` push、Pull Request 和手动触发时运行单元测试与 E2E、保留诊断产物；学生站部署仍只允许测试通过后的 `main` push。
- Scout 的自动化探针改为统计可执行 E2E 场景，不再把辅助脚本误报为 0 条测试。

## 2026-07-24：Agent 运行环境可在网页自检与修复

- 教师端把 Codex 运行环境收进“添加 Codex 可视化预设”旁的小状态框：默认收起，点击后才显示 CLI、代理与无学生数据的真实探测。
- 新增本地私有 `agent-runtime.json` 与 Runtime Doctor API；Codex 路径和代理修改后立即作用于 Gateway，无需重启服务，检测结果与配置摘要绑定并可跨页面刷新保留。
- 网页只允许无凭据的本机回环代理，探测到端口不会自动启用；Gateway 增加 `ALL_PROXY/all_proxy` 安全白名单传递。
- 新增运行环境、动态重载、代理安全、HTTP 路由和静态 UI 契约测试；浏览器实测 ChatGPT 内置 Codex `0.145.0-alpha.30` 经本机 `127.0.0.1:7890` 真实探测通过。

## 2026-07-23：Evolve 模块入库、model_registry 提取与质量基础设施就位

- Agent 任务 prompt 大幅瘦身：删除了已被 Gateway `allowed_paths`/`denied_paths` 和领域 validator 结构性兜底的"不要做 X"约束，移除 `project-rules.md` 和 `responsibility-matrix.md` 两个不必要的上下文文件，教师反馈前置到 prompt 最前。
- 新增 `source.clean` Agent 任务（server 端 `source_clean_task()` + `run_source_clean()` handler）：OCR 后由 Agent 自动修正公式/符号/换行错误，并从题干提取内容相关中文标题写入 `record.json`。默认走 economy 档，不暴露原图，允许修改 `problem.md` 和 `record.json`（仅教学元数据字段）。
- Knowledge Store 证据注入正式接入 Agent Gateway：`agent_evidence_payload()` 封装 `build_agent_evidence()`，`answer.revise` 和 `visualization.model` 任务自动接收裁剪后的历史相似题证据（方法、易错点、既往失败教训），当前题干和教师意见始终优先。economy 最多 2 条/3500 字符，expert 最多 4 条/9000 字符。
- 新增 `agent_scheduler_config()`：读取/初始化 `agent-scheduler.json` 配置文件，返回标准化调度配置。
- 检索评测后端接口就位：`retrieval_benchmark` 模块通过 server 暴露，`retrieval_review_snapshot()` 和 `save_retrieval_review()` 为前端评测复核页提供数据。
- `save_answer_entry` 保存后自动运行 evaluator 并返回 `evaluation` 字段，前端可立即看到评分变化。
- 网页端新增题目标题点击编辑：`rename-entry` action 直接更新 `record.json` 并刷新索引，Enter/blur 保存、Escape 取消。
- 修复测试文件中引用的 5 个缺失函数/模块（`agent_evidence_payload`、`source_clean_routing_tier`、`agent_scheduler_config`、`retrieval_benchmark`、检索评测 API），全部 113 个测试通过。
- Claude provider 保存时持久化 `model` 字段，并在 CC CLI 调用时传入 `--model` 参数；`api_key` 从注册表注入子进程 `ANTHROPIC_API_KEY`，优先于服务器环境变量。
- dpsk 模型注册表配置统一：`api_key_env` 统一为 `ANTHROPIC_API_KEY`，修复两个条目回退行为不一致的问题。
- evolve 实验模块全部纳入版本控制：`failure_intelligence.py`、`candidate_archive.py`、`evaluator.py`、`knowledge_store.py`、`teacher-console/scripts/`（6 个 benchmark/report 脚本）、对应 9 个测试文件及 6 篇文档。
- `model_registry.py` 从 `server.py` 提取为独立模块（371 行）：模型注册 CRUD、probe 摘要/验证、config 解析/持久化。`server.py` 减少 326 行。`agent_gateway.py` 可直接引用。
- 新增 `teacher-console/log.py` 共享日志模块，所有核心模块接入。
- `test_answer_review.py` 新增 `ValidateAnswerCandidateTest`（6 个纯单元测试，0.06s），覆盖缺失文件/内容不一致等边界。
- 质量基础设施就位：`pyproject.toml` 配置 ruff（E/F/W/I/UP）+ mypy strict；ruff format 格式化 41 个文件；56 个 auto-fix；mypy 从 224 错误降至 38 个真实可疑项。
- 删除 `.gitignore` 中过时的 evolve 模块排除行。

### 2026-07-22：Agent 失败检索与一次性纠正
- `candidate_validation_failed`、`output_truncated`、`candidate_no_change` 会在全新隔离候选区最多纠正一次；越权、canonical 冲突、provider/adapter 故障、构建失败和服务中断仍禁止立即自动重试。
- 作业 API 与 Candidate Archive 新增 `failure_repair`，批量 Benchmark 汇总 `repair_outcomes`。慢循环拆出可靠性观察门槛：5 个终态任务且至少 1 个结构化失败即可记录只读排障报告，不再要求失败样本先凑足教师闭环。

## 2026-07-22：RAG 效果观察与慢循环门槛

- 教师工作台新增“检索评测”可视化复核：用原题图、题干摘要、知识点和错因卡片替代手工编辑 JSONL，支持筛选、多选、保存草稿、驳回与批准后跳到下一条；数据仍只写本地私有固定集，不进入学生端。
- 新增 `slow_loop_report.py` 只读慢循环骨架：组合 RAG 教师闭环、固定检索集和调度基准，输出周报/策略/策略变更/自动应用四级 readiness；样本不足只列缺口，不调用模型、不应用策略。
- 慢循环只把明确批准或教师返修计为教师闭环；调度建议至少需要 5 个同类作业且忽略 `unknown_failed`。教学质量周报仍要求 20 个 RAG 完成任务和 10 个教师闭环；可靠性观察可由 5 个终态作业与至少 1 个结构化失败单独开启。
- 策略确认新增显式教师动作并绑定当前最新的慢循环报告；新报告会使旧确认失效，确认事件始终标记 `applies_policy=false`。连续两期方向比较只看 RAG/检索策略，不被临时调度诊断干扰。
- 新增 `retrieval_benchmark.py` 固定集工具：可从 canonical 元数据生成 30 条本地 `draft` 草稿，校验教师标签，并按知识点、题型、错因、教师表达报告 Hit@k、Recall@k、MRR 和空结果率；不调用模型、不自动修改检索策略。
- 只有至少 30 条教师确认的 `approved` 查询才允许把聚合结果记录为 `evolve.observation.retrieval`；持久事件排除查询正文、相关条目 ID 和逐题结果，草稿结果不能触发后端升级。
- 新增只读 `rag_effectiveness_report.py`，按 `retrieved / empty / unavailable / legacy-no-rag` 汇总 Agent 成功率、耗时、用量、Evaluator 分数、教师返修与最终批准。
- 只有同一任务类型的对照组分别达到样本门槛时才标记 `comparison_ready=true`；报告明确属于观察性证据，不在线随机关闭 RAG。
- 显式 `--record` 可写入全库 Candidate Archive 的 `evolve.observation.rag`，Knowledge Store 新增 `evolve_observation` 派生表。
- 新增 Evolve 路线门槛：先做 30 条标注检索集，再按召回缺陷增强后端；慢循环从只读周报开始，策略生效必须经过样本、固定测试集、教师确认和回滚门禁。

## 2026-07-22：Knowledge Store 证据注入 Agent

- `answer.revise` 与 `visualization.model` 会在任务开始前读取本地 Knowledge Store，把相似题方法、错因、Evaluator 摘要和近期失败教训作为限量只读证据放入 Gateway 隔离区。
- 证据包排除当前条目和内部路径/ID，经济模式最多 2 条，其他模式最多 4 条；数据库缺失或检索失败时 fail-soft，不触发重建、不阻塞主任务。
- Gateway 新增受限的内联上下文运输，只允许写入 `.agent-context/`，并从 adapter stdin 元数据移除原始 payload；作业结果仅记录证据状态和引用数，便于后续 Evaluator/Evolve 对照收益。

## 2026-07-22：Agent 失败原因结构化

- Agent Gateway 在失败发生时写入低基数 `failure_type`，区分 provider 超时/限流/执行失败、adapter 协议错误、无候选变化、输出截断、领域校验失败、越权修改和 canonical 冲突。
- Agent Scheduler 为服务重启中断和生命周期回调异常分别记录 `worker_interrupted`、`task_exception`；可视化后处理构建失败记录 `simulation_build_failed`。
- 后台作业公开结果、Candidate Archive 和批量 Benchmark 透传同一失败码；Benchmark 仅对旧作业保留文本推断兼容，为后续 Knowledge Store 检索和 Evolve 调度决策提供稳定证据。

## 2026-07-22

- 新增 Evaluator 薄切片：`process_uploads.py evaluate <entry-id>` 可手动生成 `evaluation.json`，关键生命周期动作和 Agent 任务结束后自动刷新，`finish` 后把评价摘要写入 `delivery-manifest.json`；报告记录解析结构、来源/答案复核、可视化、交付、安全提示和 0-5 分评分，为后续 Candidate Archive、题库 RAG 与 AI 审计 RAG 提供统一证据入口。
- 新增 Candidate Archive 薄切片：教师反馈/批准、答案保存、Agent 解析/返修/可视化结果、可视化构建、公开发布和最终交付会追加写入每题与全库 JSONL 档案；档案只保存摘要、状态、变更文件、失败原因和 Evaluator 摘要，自动脱敏密钥字段，为后续 RAG 检索和慢循环复盘沉淀成败历史。
- 新增 Knowledge Store 薄切片：`student-error-library/indexes/wuli-memory.db` 作为可重建的本地 SQLite/FTS 派生索引，聚合条目 Markdown/JSON、Evaluator 摘要和 Candidate Archive 事件；`knowledge_store.py query` 返回带证据片段、标签、评分与候选历史的 evidence pack，供后续题库 RAG、AI 审计和 Evolve 比较使用。
- 新增 Agent Scheduler Phase 1：后台 Agent 作业从固定线程池升级为可配置优先级调度，默认各任务类型上限为 4、全局上限为 6，同题仍互斥；等待同类并发额度的作业不再占住 worker。配置文件为 `student-error-library/config/agent-scheduler.json`，保留 provider limits 接口供后续 Evolve 自适应调度使用。
- 新增 `agent_batch_benchmark.py` 基准脚本：默认只读复盘 `.cache/agent-jobs/` 中的等待时间、运行时间、P50/P90、最大并发、provider/model 分布、失败类型和 token 用量；显式 `--record` 时追加全库级 `scheduler.benchmark` Candidate Archive 事件并刷新 Knowledge Store，用于比较批量录入优化前后的真实收益。
- 后端批量录入提速：`source.clean` 未显式选择档位时默认走 `economy`，成功后改为防抖刷新知识索引；Evaluator 和 Candidate Archive 仍逐题沉淀结果。
- 新增可审查的 Archify 系统架构图与错题处理 Pipeline：JSON 作为布局和语义真源，单文件 HTML 提供主题、搜索、语义聚焦和分章节 Story 导览；Story Follow Camera 现在以当前执行模块为优先锚点，在图表只部分进入浏览器视口时自适应留白并保证模块完整可见，同时保留前后步骤上下文，不接管页面滚动。
- 新增基于 graphify 的架构治理与 AI 入口地图：`.graphifyignore` 排除生成物、公开题库内容和 vendor/minified 依赖，`docs/ai-editing-map.md` 指导 AI 按任务类型选择最小上下文，`docs/architecture-governance.md` 成为功能归位、复杂度删减和变更影响分析的根规则触发文档。
- 教师端模型设置改为可插拔注册表 UI：支持“自动 / 经济 / 深度 / 自定义”模式，自定义时选择具体模型；本地默认可把可视化建模路由到 `Codex 可视化 Agent`。
- OpenAI-compatible 模型可在设置页填写 API 地址、真实模型名和 API Key；Key 仅保存到已忽略的本地 `student-error-library/config/model-registry.json`，接口不回显明文，Agent 子进程只通过环境变量接收。
- 每个可选模型新增独立“测试”按钮；测试请求不含学生题目数据，只有测试通过且当前配置未变化的模型才会参与自动/默认路由，未测试、失败或改过配置的模型置灰。
- LiteLLM 推荐接入方式明确为本机 Proxy 模型网关：悟理继续通过 Agent Gateway 管理候选隔离、教师复核和交付门禁，LiteLLM 只负责多供应商别名、回退、限流和成本统计。

## 2026-07-20

- 项目添加 MIT License。
- 公开学生端 PDF 下载统一为题目阅读页的 `带答案错题.pdf`；公开 PDF 不再使用旧 `answer.pdf` 名称，也不直接复制私有 `output/` PDF，而是从脱敏 Markdown 与公开题图重新生成。PDF 生成新增 `pandoc+xelatex → reportlab` 降级链，降级版保留 Markdown 中的 LaTeX 编码。
- 仿真器新增 `planar-magnetic-multi-particle` 类型，支持平面分界同向磁场、多正电粒子分段圆弧、关键事件暂停和相遇判定；最新上下分区磁场双粒子题已生成预审 HTML/ZIP，并通过模型、静态和浏览器交互检查。
- 教师端 Agent 新增“自动 / 经济 / 深度”档位；OpenAI-compatible provider 支持标准、经济和深度模型映射，缺少可选模型时明确记录降级，并公开模型名与 token 用量。任务按类型和档位裁剪上下文，经济返修不再发送 `record.json`、整套 Skill 或无关物理模型/素材。
- 文档审计按知识库真源修正竞赛口径为 13 个条目（11 个已交付、1 个待可视化复核、1 个待答案复核），并明确结构化推理不接收原图、远程视觉复核仍受双隐私门禁约束。
- 架构演进清单不再建议 AI 自动审批教学内容；风险评分只用于排序、聚焦和简化人工复核。本地 SLM 方向改为复用现有 JSON/OpenAI-compatible 传输层，补充自动发现与配置，而不是再造 provider 协议。
- 两类物理仿真模板改为响应式紧凑布局：桌面端画布与控制栏并排，手机端同屏保留画布、当前结论、播放和进度，次要控制默认折叠；已批准的同心圆复合场仿真按该布局重新构建、运行检查并发布到学生站。
- 教师端新增 provider-neutral Agent Gateway：Codex、Claude、JSON command adapter 与经授权的 OpenAI-compatible API 使用统一任务契约；修复 Codex CLI 旧参数导致答案返修和可视化同时失效的问题。
- 解析、答案返修和可视化建模改为持久化后台作业，页面支持任务状态、轮询和刷新恢复；同一题并发修改被拒绝，服务重启后的未完成任务明确标记失败。
- Agent 只在系统临时候选区工作，原始题图不进入候选区；允许文件通过范围、内容和 canonical 并发摘要检查后才提升，失败 provider 仅能在零修改时安全降级。
- Gateway 新增无学生数据的主动连通探测、单 provider 超时与运行失败熔断；Codex/Claude 后台调用隔离个人配置并关闭 stdin，结构化 API 在 `auto` 中优先于通用 CLI。
- Gateway 候选区改为输入文件白名单，保护 `record.json` 来源/审批字段，过滤子进程环境，并加入教师端 OS 单实例锁、单题事务锁、索引写锁和交付下载双白名单。
- 项目竞赛品牌统一为“悟理”，正式全称为“悟理——面向乡村课堂的端侧可信 AI 全流程教学助教平台”；完整说明与精简申报稿分工明确，不再保留旧候选名称。
- 竞赛精简稿改为仓库事实可核验版本：同步真实条目、交付、物理模型、结论库和自动化测试数量，并明确尚未完成学校规模化试点与跨学科验证。
- 新增教师工作台 API 文档，集中说明本地路由、写操作请求头、流程门禁、状态码和推荐调用顺序。
- 学生端发布门禁新增公开题图编辑器：自动建议裁剪、四边手动调整、拖拽不透明遮挡、逐页选择及教师摘要确认；只发布新生成的 WebP 副本，原图保持私有不变。
- 新增独立只读 `student-site/`：浏览器渲染 Markdown，提供可用 PDF 下载和已批准交互仿真入口，可部署到 GitHub Pages。
- 教师工作台交付页新增公开草稿预览、隐私确认和本地发布门禁；公开导出默认排除原始上传、教师版解析、内部 JSON、绝对路径和未批准仿真，且不自动推送 GitHub。

## 2026-07-19

- 解析复核意见现可直接调用本地 Agent 修改当前条目的分层答案与引用解释图；SVG/PNG 字节纳入答案摘要，修改后必须重新批准。
- “可视化（可选）”页面现在对所有题目保留；无模型表示“尚未生成”，教师明确输入生成要求或点击按钮后才调用仿真 Skill，静态 SVG 仍留在解析复核。
- 高中物理解题技巧从常驻根规则迁入独立速查文档，根规则只保留边界与文档指针；自动检索仍以带条件的 JSON 结论库为准。
- 修正 `grill-me` 中指向不存在脚本、Agent 和参考文件的死引用，改为直接使用当前 Agent 的代码侦查与对话能力。
- 教师工作台新增交付前动态可视化复核：记录产物摘要、支持向本地 Agent 提交修复反馈；最终交付复制教师批准的预审产物。
- 题库左栏改为“上传日期文件夹 → 具体题目”，首次启动创建 `student-error-library/folders/` 同步视图，网页改名同步本地但不移动 canonical entry。
- 交付页改为成品白名单，只展示学生包、PDF、Markdown 和必要的仿真 HTML/ZIP，并逐一解释用途。
- 教师工作台改为固定视口单屏布局：侧栏、原图、编辑器和预览分别滚动，可一键收起题库。
- 题干与学生/教师答案新增本地 Markdown + KaTeX 实时编译预览，支持页面编辑、保存和 `Cmd/Ctrl+S`。
- 教师手工编辑物理模型条目的答案后进入 `manual` 渲染模式，交付不再静默覆盖 Markdown。
- 新增 `teacher-console/` 本地教师工作台，串联上传、OCR、题干复核、Agent 解析、答案复核、返修、交付和下载。
- 新增 `answer-review.json` 与答案联合摘要；答案、题干或共享物理模型改变后必须重新批准，未批准版本不能交付。
- 工作台后端复用生命周期脚本且默认只监听 `127.0.0.1`，不复制业务逻辑、不公开学生材料。
- 建立“上传 → OCR → 来源复核 → 分层答案 → 按需仿真 → PDF/学生包”的统一生命周期。
- 将 `.claude/skills/` 设为 Skill 唯一真源，`.agents/skills/` 统一为兼容软链接。
- 用 `physics-model.json` 统一答案、事件、轨迹和仿真数据，并接入 JSON Schema、静态检查和浏览器运行检查。
- 增加适用于 DeepSeek 等纯文本主模型的视觉复核边车协议。
- 增加本地 OpenAI-compatible 多模态适配器、远程双隐私门禁与教师人工复核兜底。
- 教师批准现在记录 reviewer、复核时间、输入摘要和正式题干哈希；未经批准的来源不能进入解题交付。
- 新增答案质量三层成对评测：教师已复核 `student-solution.md` 是唯一标准答案；同模型直出与隔离网页点击生成均为候选。网页样本从“题干已复核、无答案/物理模型”状态运行，要求浏览器点击来源证明；原图信息未进入直出上下文的样本自动排除胜负统计。
