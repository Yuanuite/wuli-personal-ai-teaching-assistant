# 变更记录

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
