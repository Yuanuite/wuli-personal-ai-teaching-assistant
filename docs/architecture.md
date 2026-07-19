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

`teacher-console/` 是生命周期总控的本地交互外壳，不复制 OCR、校验、导出或仿真逻辑。后端直接调用 `process_uploads.py` 与 `kb.py`，页面负责上传、展示原图、收集题干与答案确认，并对所有条目保留按需可视化入口；只有已生成物理模型的条目才进入动态产物复核。工作台还可触发已配置的 Agent、展示结果和提供必要成品下载。默认只绑定 `127.0.0.1`，因此不是公网发布站点。

来源批准绑定题干摘要；答案批准绑定题干、学生版、教师版、同步答案、共享物理模型和答案所引用本地图像的联合摘要。解析意见可交给当前条目范围内的本地 Agent 修改 Markdown 与解释 SVG/PNG，但 Agent 不能自行批准。任一受保护文件发生变化，旧批准失效，`finish` 必须拒绝交付。

可视化页面始终保留，但标准解析不主动创建交互模型。没有 `physics-model.json` 时显示“尚未生成”，教师明确请求后才由 Agent 调用 `build-physics-simulator` 创建模型；这不是对题目适宜性的自动判断。模型创建会使答案摘要失效，因此先回到答案复核。可视化批准只适用于已经生成的动态交互仿真，并独立绑定当前模型、预审 HTML/ZIP、运行时证据和构建报告。动态仿真先在条目 `visualization/` 中构建并由教师通过 sandbox iframe 查看；`finish` 只复制这份已批准产物，不重新渲染。静态 SVG/PNG 始终留在答案复核。

`entries/` 是条目唯一真源。`folders/` 是按上传日期生成、可从真源重建的教师视图，网页文件夹改名只调整该视图和分组元数据，不移动 canonical entry。

工作台的路由、写操作请求头、门禁状态码和本地集成顺序见 [`teacher-console-api.md`](teacher-console-api.md)。该接口只服务本地教师端，不构成学生端的数据通道。

### 学生端公开边界

`student-site/` 是独立、只读、纯静态的发布目标，不是教师工作台的公开模式。数据只能单向流动：原始题图先生成不覆盖原文件的 WebP 公开副本，裁剪、遮挡、源文件摘要和教师确认记录在 `publication-images.json`；源图或副本变化后确认自动失效。已交付条目再生成 `publication-draft/`，安全扫描通过且教师查看预览、勾选隐私确认后，才把白名单产物复制到公开站。公开 ID 使用不可逆摘要，不暴露内部条目 ID；原始上传、教师版解析、流程记录、复核记录、模型 JSON、交付清单和绝对路径一律禁止进入公开目录。公开页面只读取相对路径的 `catalog.json`、Markdown、PDF、公开题图、答案图片及已批准仿真，不调用教师端 API。GitHub 推送是明确的人工后续操作。

教师工作台允许在解析复核阶段直接编辑学生版或教师版 Markdown。保存教师版时同步 `solution.md`，保存任一答案都会撤销旧批准并重建检索。如果条目包含 `physics-model.json`，保存会把 `source.answer_render_mode` 标记为 `manual`；之后 `finish` 尊重教师手工版本，不再静默用模型重新覆盖 Markdown。需要恢复模型生成时，应显式重新运行答案渲染并把该字段改回 `model`。

## 组件职责

| 能力 | 生命周期总控 | 仿真专家 |
|---|---|---|
| 上传发现、哈希、去重、OCR、原图核对 | 负责 | 不参与 |
| 知识点、错误类型、历史检索 | 负责 | 不参与 |
| 学生版/教师版 Markdown、PDF、学生包 | 负责 | 不参与 |
| 物理区域、事实、事件、轨迹、交互参数 | 协调并保存 | 负责语义与验证 |
| HTML/ZIP、静态检查、浏览器运行检查 | 调用并接收结果 | 负责 |
| 知识索引和复习计划 | 负责 | 不参与 |

## `physics-model.json` 集成契约

同一道题只保留一个模型文件，避免答案、事件标签、轨迹和 HTML 常量分叉。字段所有权如下：

| 字段 | 所有者 |
|---|---|
| `schema_version`、`entry_id`、`title` | 生命周期总控初始化；双方只做一致性检查 |
| `source`、`technique_ids`、`student_solution`、`teacher_audit` | 生命周期总控 |
| `model_type`、`regions`、`facts`、`event_model`、`trajectory`、`simulation` | 仿真专家 |

仿真专家可以验证教学字段与物理事件是否一致，但不得把教学答案作为自己的第二份真源。生命周期总控不得在答案或导出脚本中另写一套轨迹/事件常量。

机器结构由 `.claude/skills/build-physics-simulator/references/physics-model.schema.json` 校验；跨字段物理关系由 `validate_physics_model.py` 校验。两层都通过后才能构建仿真。

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

若还要发布学生端，则在以上交付完成后执行公开草稿生成、安全扫描、教师预览与隐私确认；它不改变 `delivered` 状态，也不替代本地交付 manifest。

运行时检查有三种状态：`passed`、`skipped`、`failed`。真实运行失败会阻止交付；缺少浏览器依赖时可保留静态结果，但必须在 manifest 中记录 `skipped` 和原因。

## 真源与兼容层

`.claude/skills/` 是项目 Skill 唯一真源；`.agents/skills/` 仅放指向真源的兼容软链接。`CLAUDE.md` 是根规则真源，`AGENTS.md` 必须链接到它。人类使用说明放在 README 和 `docs/`，不塞进 Skill 或根规则文件。
