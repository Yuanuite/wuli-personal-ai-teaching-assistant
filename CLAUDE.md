# 悟理 · AI 全流程教学助教平台

面向乡村课堂的端侧可信教学闭环；当前以高中物理为完整 MVP，提供 OCR 入库、解析生成、变式题、交互仿真和薄弱点分析。

## 目录约定

```
error-collection/     ← 放入待处理的错题图片/PDF
student-error-library/ ← 错题知识库（entries + indexes）
output/               ← 导出成品（Markdown/PDF/仿真/学生包/交付清单）
teacher-console/      ← 本地教师工作台（日期文件夹、题干/答案复核、按需动态仿真复核、交付下载、模型注册）
student-site/         ← 独立只读公开站；只接收教师确认后的白名单产物，不访问本地 API
```

## Skills

| Skill | 职责 |
|-------|------|
| `manage-student-error-library` | 总控：上传、OCR、分析、分层答案、入库、PDF、学生包、复习；按需调用仿真 Skill |
| `build-physics-simulator` | 专家插件：只负责 `physics-model.json` 的物理事件/轨迹与离线 HTML/ZIP，不负责 OCR、答案、PDF、知识库 |
| `scout` | 项目质量侦查，识别高风险与投入产出比最高的下一步 |
| `grill-me` | 方案/设计逐层追问，达成共识 |
| `neat-freak` | 会话收尾：文档同步、记忆整理、规范审计 |
| `darwin-skill` | Skill 自动优化评分（SkillLens 9 维 + SkillOpt） |

## 一句话全流程

用户说“处理现在新上传的题目”时，由 `manage-student-error-library` 完成：

```text
error-collection → OCR/去重 → Agent整理题干(source.clean) → 原图核对 → 分析/检索 → 学生版+教师版
→ 答案复核 → [教师按需请求交互可视化+复核] → 校验/入库 → PDF → student-package.zip
```

最终以每题输出目录中的 `delivery-manifest.json` 为准，不从散落文件推测是否完成。

E2E 只允许在独立临时知识库和输出目录中运行。教师真实处理题目不得触发或录制 E2E，
测试产物不得进入正式 `student-error-library/`、`output/` 或 `student-site/`。

公开发布是交付后的独立门禁：原始题图先通过自动建议裁剪、教师手动调整/遮挡和 `publication-images.json` 摘要确认，生成 `publication-assets/` 公开副本；再生成条目内 `publication-draft/`，教师预览并确认隐私后，才复制学生版 Markdown、公开题图、重新生成的公开版 `带答案错题.pdf`、答案引用的安全图片和已批准仿真到 `student-site/`。禁止直接复制原始上传、教师版解析、条目内部 JSON、交付 manifest、本地绝对路径或私有交付 PDF；禁止自动推送 GitHub。

无视觉能力的主模型不得自行解除原图复核门禁；使用独立视觉适配器，或等待实际查看原图的人执行 `approve-source`。

新条目必须由教师批准当前答案摘要；题干、答案、`physics-model.json` 或答案引用的解释图修改后批准自动失效。标准解析默认不创建交互仿真，但网页始终保留“可视化（可选）”入口；没有模型表示“尚未生成”，不能推断为不适合。教师明确说“我想为这道题生成一个可视化结果”或点击生成后，才调用 `build-physics-simulator`。生成模型后先重新复核答案，再批准可视化产物。最终交付复制教师实际预览并批准的仿真字节，不在 `finish` 时临时重建。

教师端 Agent 任务统一经过 `teacher-console/agent_gateway.py`：provider 只能在输入文件白名单构造的系统临时候选区工作，候选通过允许路径、受保护记录字段、领域验证和 canonical 摘要检查后，才在单题事务锁内提升。`provider` 表示执行运行时而非上游模型厂商；是否开放文件工具由任务契约决定，`analysis.generate` 必须保持无工具结构化输出，不能因使用 Claude/Codex CLI 而放宽。CLI/API 参数不得重新散落进 `server.py`；Agent 永远不能调用 `approve-*`、`finish` 或发布。新 provider 优先实现 JSON stdin/stdout adapter，额外环境变量必须显式加入 allowlist。

Agent 任务 prompt 只负责内容质量，Gateway/Validator 负责合规——不要在 prompt 中重复已被 `allowed_paths`、`denied_paths` 和领域 validator 结构性兜底的约束。`answer.revise` 和 `visualization.model` 会自动注入经过裁剪的 Knowledge Store 历史证据（`.agent-context/knowledge-evidence.json`），当前题干和教师意见始终优先。

OCR 之后可先运行 `source.clean`（默认 economy 档）让 Agent 修正 OCR 错误并从题干提取内容相关标题，再进入人工 source review。网页上点击标题文字可直接改名。

## 深入文档

- `docs/ai-editing-map.md`：AI 修改入口地图；不确定该读哪些文件时先看它，按任务类型选择最小上下文。
- `docs/architecture.md`：生命周期、职责边界、共享模型与验证门禁。
- `docs/operator-runbook.md`：手动命令、依赖降级、排障与交付检查。
- `docs/visual-review-integration.md`：视觉边车协议、环境变量、隐私门禁和接入测试。
- `docs/high-school-physics-techniques.md`：高中物理解题策略与二级结论速查；自动检索以带条件的 JSON 结论库为准。
- `docs/CHANGES.md`：对人类维护者可见的阶段性能力变化。
- `docs/competition-submission.md`：竞赛申报精简稿；事实与数字必须从代码和状态命令核验。
- `docs/competition-project-description.md`：竞赛完整说明、价值定位、落地计划与演示脚本。
- `docs/teacher-console-api.md`：本地教师端 HTTP 路由、动作协议和安全边界。
- `docs/agent-gateway.md`：后台 Agent 作业、provider adapter、隔离候选、降级和远程隐私门禁。
- `docs/agent-scheduler.md`：后台 Agent 作业调度、优先级、并发配置和后续 Evolve 接口。
- `docs/evaluator.md`、`docs/candidate-archive.md`、`docs/knowledge-store.md`：评价报告、候选事件档案与本地 RAG evidence pack。
- `docs/failure-intelligence.md`：Agent 失败排障策略、自动重试边界与一次性纠正机制。
- `docs/evolve-roadmap.md`：检索评测、RAG 效果观察与慢循环策略更新的分阶段路线和样本门槛。
- `docs/architecture-governance.md`：基于 graphify 的项目治理协议；功能归位、复杂度删减、变更影响分析时必须先读。

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- When the user asks about feature placement, module responsibility, complexity reduction, architecture drift, change impact, whole-project optimization, competition narrative, or whether an entry/Skill/UI is redundant, first read `docs/architecture-governance.md`, then use graphify for structural evidence before proposing or editing.
- When unsure which files to inspect, first read `docs/ai-editing-map.md` and load only the task-specific docs/scripts it names.
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
