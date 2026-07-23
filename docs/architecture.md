# 悟理系统架构

## 目标与边界

本项目把一道新上传的错题视为一个有状态的生命周期，而不是一组彼此独立的 OCR、解析、仿真和导出命令。学生材料默认只在本机处理；未经明确授权，不上传到远程 OCR 或其他外部服务。

## 生命周期

```text
uploaded → ingested → source-reviewed → analyzed → answered → answer-reviewed
         → [teacher requests interactive visualization
            → model-created → answer-re-reviewed → visualization-built → visualization-reviewed]
         → validated → delivered → reviewed
```

`manage-student-error-library` 是唯一生命周期总控。它发现上传、建立条目、推进状态、生成答案、维护索引并交付结果。`build-physics-simulator` 只在教师明确请求交互可视化时被调用；若确定性渲染器无法正确表达该过程，必须返回 `unsupported` 及理由。

> **交互式管道图**：[点击查看](https://yuanuite.github.io/wuli-personal-ai-teaching-assistant/diagrams/pipeline.workflow.html)（可缩放、搜索节点、切换暗色/亮色主题）
>
> <img src="diagrams/pipeline.workflow.png" alt="管道图预览" width="80%">
>
> **系统架构图**：[点击查看](https://yuanuite.github.io/wuli-personal-ai-teaching-assistant/diagrams/system.architecture.html)（展示组件关系与信任边界）
>
> <img src="diagrams/system.architecture.png" alt="架构图预览" width="70%">

## 文本模型与视觉边车

主推理模型不必具备视觉能力。输入规范化分为三层：

```text
原图/PDF → OCR 字符草稿 → 视觉语义复核 → 已复核题干 → 推理模型
```

- OCR 负责文字候选，不判断图形语义；
- 可插拔视觉边车负责公式结构、图示、方向、电性和边界；
- 教师是任何适配器失败或存在不确定项时的最终兜底；
- DeepSeek 等纯文本模型只消费已经通过复核的题干和图形事实。

`source-review.json` 记录方法、引擎、本地/远程属性、输入摘要和复核时间。`source-review.md` 是人工路径的本地复核包。任何路径都不得仅凭 OCR 置信度解除门禁。

### 教师确认的信任边界

系统不能证明教师是否认真看过原图，只能证明某位教师或角色明确确认了某个题干版本。因此人工路径采用可审计声明，而不是模型猜测：

1. 教师对照原图修正 `problem.md`；
2. 教师明确执行或授权执行 `approve-source`；
3. 命令拒绝仍含 `[待核对]` 的题干；
4. `source-review.json` 保存 reviewer、时间、原始输入摘要和正式题干哈希；
5. `record.json` 的 `ocr.review_required=false` 与 `source_review.status=passed` 必须同时成立。

这些证据能追溯“谁确认了哪个版本”，不能替代教师本人的专业责任。视觉边车返回任何不确定项时也必须回到该人工路径。

每个条目的 `pipeline.json` 记录当前状态和下一步；脚本负责推进状态，Agent 不手工伪造完成状态。最终完成以输出目录的 `delivery-manifest.json` 为准。

### 教师工作台

`teacher-console/` 是生命周期总控的本地交互外壳，不复制 OCR、校验、导出或仿真逻辑。后端直接调用 `process_uploads.py` 与 `kb.py`，页面负责上传、展示原图、收集题干与答案确认，并对所有条目保留按需可视化入口；只有已生成物理模型的条目才进入动态产物复核。工作台还可通过本机 Agent Gateway 触发已配置的 provider、展示后台作业和提供必要成品下载。默认只绑定 `127.0.0.1`，因此不是公网发布站点。

来源批准绑定题干摘要；答案批准绑定题干、学生版、教师版、同步答案、共享物理模型和答案所引用本地图像的联合摘要。解析意见可交给 Gateway provider 在隔离候选区修改 Markdown 与解释 SVG/PNG，但 Agent 不能自行批准。任一受保护文件发生变化，旧批准失效，`finish` 必须拒绝交付。

可视化页面始终保留，但标准解析不主动创建交互模型。没有 `physics-model.json` 时显示“尚未生成”，教师明确请求后才由 Agent 调用 `build-physics-simulator` 创建模型；这不是对题目适宜性的自动判断。模型创建会使答案摘要失效，因此先回到答案复核。可视化批准只适用于已经生成的动态交互仿真，并独立绑定当前模型、预审 HTML/ZIP、运行时证据和构建报告。动态仿真先在条目 `visualization/` 中构建并由教师通过 sandbox iframe 查看；`finish` 只复制这份已批准产物，不重新渲染。静态 SVG/PNG 始终留在答案复核。

交互仿真的呈现契约同样属于待复核产物：桌面端优先采用画布与控制栏并排，手机端应在首屏同时保留画布、当前结论、播放按钮和进度，案例横向滚动，次要图层与高级控制默认折叠，避免学生为观察结果反复上下滑动。模板变更后必须重新构建并执行桌面与手机视口检查；公开发布复制通过教师批准的字节，不在学生站另做一套布局。

`entries/` 是条目唯一真源。`folders/` 是按上传日期生成、可从真源重建的教师视图，网页文件夹改名只调整该视图和分组元数据，不移动 canonical entry。

工作台的路由、写操作请求头、门禁状态码和本地集成顺序见 [`teacher-console-api.md`](teacher-console-api.md)。该接口只服务本地教师端，不构成学生端的数据通道。

### Agent Gateway 信任边界

```text
HTTP action → persistent job → Agent Gateway → temporary candidate workspace
                                             → provider adapter
                                             → path + domain validation
                                             → locked allowlist promotion + rollback
                                             → lifecycle state/review invalidation
```

- `server.py` 只提交任务类型、教师意见、读写集合和隐私策略，不保存具体 CLI/API 参数；
- provider 在系统临时目录工作，canonical entry 不作为工作目录；候选区按输入白名单构造，原始题图、批准记录和无关内部文件不被 Gateway 复制或写入 prompt；CLI 运行时的额外只读边界仍由其自身沙箱决定，严格披露边界应使用结构化 adapter；
- `answer.revise` 与 `visualization.model` 可从本地 Knowledge Store 获得经过裁剪、限量且排除当前条目的历史证据；检索失败不阻塞任务，证据不得覆盖当前教师复核内容，也不会成为新的 canonical 真源；
- `.agent-context/` 按任务和成本档位提供最小只读规则：答案任务以答案模板与职责边界为主，深度档才附完整知识库 Skill；可视化任务附仿真 Skill 与模型 Schema；
- 候选修改仅限任务白名单，答案候选由知识库验证器检查，可视化候选由仿真模型构建器检查；
- canonical 条目在排队期间变化、候选越权、删除文件或验证失败时均不提升；
- 内容校验失败、输出截断或未形成修改时，失败排障层可在全新隔离区携带脱敏证据纠正一次；它不放宽路径、审批或发布边界，越权与 canonical 冲突永不自动重试；
- provider 只有在尚未产生候选修改时才能安全降级；
- 教师可选择 `auto|economy|expert`；档位只路由模型并裁剪上下文，实际模型、降级说明和 provider 用量写入私有作业记录，不改变复核门禁；
- 后台 Agent 作业由 Scheduler 按优先级和任务类型领取当前可运行任务，不让等待限额的作业占住 worker；`source.clean` 默认进入 `economy` 快速档并允许跨题并发，解析生成、答案返修和可视化建模也可跨题有限并发，同题任务始终互斥；
- `source.clean` 批量完成后以防抖方式刷新知识索引，避免每题成功都立即重建全库；Evaluator 与 Candidate Archive 仍逐题记录结果；
- 版本/help 探测与无学生数据的主动连通探测分离；实际运行失败会触发短期熔断，避免每道题重复等待同一个坏 provider；
- OS 单实例锁阻止两个教师服务同时管理同一知识库；单题事务锁覆盖摘要复查、提升及后处理，库级锁串行化知识索引写入；
- provider 仅继承基础运行变量与自身认证变量，其他环境变量必须显式加入 adapter allowlist；
- 作业状态与 `pipeline.json` 分离，页面刷新可以恢复轮询，服务重启则把状态不明的任务标记失败。

Gateway 不拥有 OCR、答案或物理语义，也不得调用任何 `approve-*`、`finish` 或公开发布动作。详细 provider 协议和隐私门禁见 [`agent-gateway.md`](agent-gateway.md)。

### 学生端公开边界

`student-site/` 是独立、只读、纯静态的发布目标，不是教师工作台的公开模式。数据只能单向流动：原始题图先生成不覆盖原文件的 WebP 公开副本，裁剪、遮挡、源文件摘要和教师确认记录在 `publication-images.json`；源图或副本变化后确认自动失效。已交付条目再生成 `publication-draft/`，安全扫描通过且教师查看预览、勾选隐私确认后，才把白名单产物复制到公开站。公开 ID 使用不可逆摘要，不暴露内部条目 ID；原始上传、教师版解析、流程记录、复核记录、模型 JSON、交付清单、私有交付 PDF 和绝对路径一律禁止进入公开目录。公开页面只读取相对路径的 `catalog.json`、Markdown、题目阅读页中的公开版 `带答案错题.pdf`、公开题图、答案图片及已批准仿真，不调用教师端 API。公开 PDF 从脱敏后的 `content.md` 和公开题图重新生成：优先 `pandoc+xelatex`，失败时降级为 `reportlab`，并保留 Markdown 中的 LaTeX 编码；若两条链路都不可用则标记 `skipped`，Markdown 页面仍可发布且不显示无效下载入口。GitHub 推送是明确的人工后续操作。

教师工作台允许在解析复核阶段直接编辑学生版或教师版 Markdown。保存教师版时同步 `solution.md`，保存任一答案都会撤销旧批准并重建检索。如果条目包含 `physics-model.json`，保存会把 `source.answer_render_mode` 标记为 `manual`；之后 `finish` 尊重教师手工版本，不再静默用模型重新覆盖 Markdown。需要恢复模型生成时，应显式重新运行答案渲染并把该字段改回 `model`。

## 组件职责

| 能力 | 生命周期总控 | Agent Gateway | 仿真专家 |
|---|---|---|---|
| 上传发现、哈希、去重、OCR、原图核对 | 负责 | 不参与 | 不参与 |
| 知识点、错误类型、历史检索 | 负责 | 只运输候选 | 不参与 |
| 学生版/教师版 Markdown、PDF、学生包 | 负责 | 隔离运行并返回候选 | 不参与 |
| 物理区域、事实、事件、轨迹、交互参数 | 协调并保存 | 隔离运行并返回候选 | 负责语义与验证 |
| HTML/ZIP、静态检查、浏览器运行检查 | 调用并接收结果 | 不构建 | 负责 |
| provider 探测、后台作业、失败降级 | 不参与 | 负责 | 不参与 |
| 教师批准、知识索引和复习计划 | 负责 | 永不执行 | 不参与 |

## `physics-model.json` 集成契约

同一道题只保留一个模型文件，避免答案、事件标签、轨迹和 HTML 常量分叉。字段所有权如下：

| 字段 | 所有者 |
|---|---|
| `schema_version`、`entry_id`、`title` | 生命周期总控初始化；双方只做一致性检查 |
| `source`、`technique_ids`、`student_solution`、`teacher_audit` | 生命周期总控 |
| `model_type`、`regions`、`facts`、`event_model`、`trajectory`、`simulation` | 仿真专家 |

仿真专家可以验证教学字段与物理事件是否一致，但不得把教学答案作为自己的第二份真源。生命周期总控不得在答案或导出脚本中另写一套轨迹/事件常量。

机器结构由 `.claude/skills/build-physics-simulator/references/physics-model.schema.json` 校验；跨字段物理关系由 `validate_physics_model.py` 校验。两层都通过后才能构建仿真。

当前确定性仿真器支持四类 `model_type`：同心圆多区场、反向圆形磁场、电场入有界磁场，以及平面分界磁场多粒子轨迹。新增类型必须同时补 renderer、模型校验、Skill 文档和浏览器检查；不得只让 Agent 自创 `model_type`。

## 验证与交付

完整交付依次通过：

1. 原图与题干核对；
2. 教师批准当前答案摘要；
3. 若教师已请求交互可视化，构建预审产物并由教师批准当前产物摘要；未请求时记录 `not-generated` / `not-required`；
4. 答案结构、图片引用和知识库记录校验；
5. JSON Schema 与跨字段物理校验；
6. HTML/JavaScript/ZIP 静态校验；
7. 浏览器运行时检查，或明确记录因依赖不可用而跳过；
8. 来源、答案与可视化复核摘要、PDF 状态、学生包和交付文件清单写入 manifest。

`finish` 成功后还会生成单题 `evaluation.json`，把解析结构、来源/答案复核、可视化状态、交付完整性和本地路径安全提示整理成可审计评分。Evaluator 只记录确定性事实和启发式提示，不替代教师复核；它是后续 Candidate Archive、题库 RAG 和 AI 审计 RAG 的共同证据入口。细节见 [`evaluator.md`](evaluator.md)。

关键教师动作、Agent 任务、确定性构建和交付动作同时追加到 `candidate-archive.jsonl`，记录任务类型、执行者、结果、变更文件、失败原因和 Evaluator 摘要。Archive 不保存密钥、原图或完整候选内容，也不改变审批状态；它为后续题库 RAG、AI 审计 RAG 和慢循环复盘提供“成败历史”。细节见 [`candidate-archive.md`](candidate-archive.md)。

`student-error-library/indexes/wuli-memory.db` 是从条目 Markdown/JSON、`evaluation.json` 和 Candidate Archive 重建出的本地 SQLite Knowledge Store。它启用 WAL 与 FTS5（不可用时降级扫描），把题干、答案、标签、评价摘要和最近候选事件打包成可引用的 evidence pack，供后续题库 RAG、AI 审计和 Evolve 候选比较使用。该数据库是派生缓存，不是审批或教学内容真源；`kb.py rebuild` 会顺带刷新它，失败时不阻断原生命周期。细节见 [`knowledge-store.md`](knowledge-store.md)。

若还要发布学生端，则在以上交付完成后执行公开草稿生成、安全扫描、教师预览与隐私确认；它不改变 `delivered` 状态，也不替代本地交付 manifest。

运行时检查有三种状态：`passed`、`skipped`、`failed`。真实运行失败会阻止交付；缺少浏览器依赖时可保留静态结果，但必须在 manifest 中记录 `skipped` 和原因。

## 真源与兼容层

`.claude/skills/` 是项目 Skill 唯一真源；`.agents/skills/` 仅放指向真源的兼容软链接。`CLAUDE.md` 是根规则真源，`AGENTS.md` 必须链接到它。人类使用说明放在 README 和 `docs/`，不塞进 Skill 或根规则文件。
