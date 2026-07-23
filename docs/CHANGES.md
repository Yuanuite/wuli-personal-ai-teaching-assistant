# 变更记录

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
- 策略确认新增显式教师动作并绑定最近一次慢循环报告；新报告会使旧确认失效，确认事件始终标记 `applies_policy=false`。连续两期方向比较只看 RAG/检索策略，不被临时调度诊断干扰。
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
