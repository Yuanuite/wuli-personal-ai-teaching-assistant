# DeepTutor 项目评估与悟理借鉴报告

日期：2026-07-31
对象：[HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor)
目标：对比 DeepTutor 与悟理两个 AI 教学系统的定位、架构与能力，识别悟理可借鉴的设计点，并给出分级、可审计的借鉴建议。

## 结论摘要

DeepTutor 是一个 **agent-native 的终身个性化辅导平台**：约 20 万行 Python + Next.js，以"一个 agent loop 承载所有能力"为核心，把聊天、解题、测验、研究、可视化、动画、掌握度路径全部收敛到统一引擎，再通过可插拔 Tools/Capabilities、多引擎 RAG、三层记忆、Skills 市场和多用户隔离扩展边界。它的价值主张是**广度与个性化**——平台要什么都能做，且记得住每个学习者。

悟理是一个 **端侧可信的错题生命周期闭环**：以高中物理为完整 MVP，把"上传→OCR→题干复核→分层解析→按需仿真→入库→交付→复习"当成一个有状态的生命周期，用 Agent Gateway 隔离候选区、四类教师复核门禁、Claim Ledger 语义证书和成对评测保证**正确性与可审计性**。它的价值主张是**深度与可信**——只做一件事，但把这件事做对、做可追溯。

两套系统不是竞争关系，而是互补的两个极：

| 维度 | DeepTutor 强 | 悟理强 |
|---|---|---|
| 平台广度 | ✅ 30+ 表面、多引擎、多用户 | — |
| 教学正确性门禁 | — | ✅ Claim 证书、方法策略、风险审计 |
| 个性化记忆 | ✅ L1/L2/L3 三层可溯源记忆 | ⚠️ Knowledge Store 偏题库检索 |
| 评测纪律 | — | ✅ 30+ 基准脚本、独立 holdout |
| 端侧可信/隐私 | ⚠️ 沙箱 + 多用户隔离，仍默认联网 | ✅ 回环监听、白名单发布 |

悟理可借鉴 DeepTutor 的六个最有价值设计：

1. **统一能力信封与 stage 契约**——把分散的 Agent 任务归一为"能力 + 阶段 + 统一结果信封"；
2. **L1/L2/L3 三层记忆 + 证据回链**——把当前"题库检索"升级为"学生画像 + 学习证据可溯源"；
3. **ask_user 暂停/恢复机制**——教师澄清从"一次性提问"升级为"回合内结构化澄清"；
4. **版本化检索索引 + 引擎可插拔**——给 RAG 升级保留无破坏的回滚路径；
5. **Skills 按需加载 + 导入安全门禁**——给 Skills 生态复制一套可审计的安装门禁；
6. **mastery 掌握度门禁**——把固定周期复习升级为"掌握度判定 + 已掌握题回流"，为 FSRS 自适应打地基。

明确不建议照搬：统一 agent loop 重写、Partner IM 平台、多用户 grant 体系、通用透明代理路线。

## 调研范围与证据边界

本次只读检查了 DeepTutor 当前 `main` 分支源码（`git clone` 于 2026-07-31）、README、AGENTS.md、SKILL.md、CITATION.cff、核心 orchestrator/memory/RAG/capability/skill/multi-user 代码、测试目录与 CI 配置，并用悟理 graphify 与第一手源码做静态结构分析。没有安装依赖、运行 DeepTutor 项目测试或复现其公开基准。

因此：

- DeepTutor 的仓库结构、代码机制和当前公开元数据（star、release、版本）属于已核实事实；
- DeepTutor 的个性化效果和社区指标属于项目方公布结果，不是本次独立复现结论；
- 对悟理的收益判断属于基于两边代码结构的工程推断，建议按"测量先行、局部借鉴"逐步验证。

DeepTutor 规模参照：约 2,000+ 源文件、325+ 测试文件、30+ API 路由表面、自 2025-12 发布以来约 20k stars、Apache-2.0 许可。悟理规模参照：56 个测试文件、385 项通过、30 个评测/基准脚本、单教师端侧闭环、MIT 许可。

## 第一部分：DeepTutor 是什么

### 1. 定位与目标用户

DeepTutor 自称 "Lifelong Personalized Tutoring"，由 HKUDS（香港大学数据智能实验室）发布，arXiv 2604.26962。README 的核心句是 "one runtime for every mode"——Chat、Quiz、Research、Visualize、Solve、Mastery Path 跑在同一个 agent loop 上，切换的是目标而不是引擎，上下文随学习者流转。

- 目标用户：**学习者 + 教育者 + 高级用户**，从个人终身学习工作台到可自托管的多用户部署。
- 价值主张：个性化（记忆）+ 终身（跨会话保持上下文）+ 可扩展（Skills/工具/MCP/CLI 应用生态）。
- 产品形态：本地 Web 应用（Next.js + FastAPI）+ CLI（REPL + JSON 机器协议）+ Python SDK + Docker 单容器/三服务编排。

【观察】DeepTutor 的定位是"一个人的终身学习操作系统"，覆盖面极广，代价是每个教学环节的深度由 Skill/插件弥补，而非内核保证。

### 2. 统一 agent loop：核心架构

AGENTS.md 明确定义两层插件模型：

```
Entry Points: CLI (Typer) | WebSocket /api/v1/ws | Python SDK
                        ↓
              ChatOrchestrator
                routes UnifiedContext → Capability (default chat)
                        ↓
        ToolRegistry (Level 1)      CapabilityRegistry (Level 2)
```

- `ChatOrchestrator`（`deeptutor/runtime/orchestrator.py`）：统一入口，`context.active_capability or "chat"` 路由到 capability，所有事件经 `StreamBus` 扇出，capability 完成后发布 `CAPABILITY_COMPLETE` 到全局 EventBus。
- **Level 1 Tools**：单次函数，LLM 按需选择。4 个用户可切换工具（brainstorm/web_search/paper_search/reason）；其余上下文门控自动挂载（rag/read_source/read_memory/write_memory/read_skill/exec/code_execution/web_fetch/github/cron/ask_user…）。
- **Level 2 Capabilities**：多阶段管线，拥有整轮。每个 capability 有 stage 表（如 `deep_solve`: planning→reasoning→writing；`deep_question`: ideation→generation；`visualize`: analyzing→generating→reviewing；`math_animator`: concept_analysis→…→render_output）。
- 所有 capability 收敛到 `emit_capability_result()`（`deeptutor/capabilities/_shared.py`），保证每轮发出统一信封（响应载荷 + `cost_summary`）。prompt 与状态文案按 `capabilities/prompts/{en,zh}/<name>.yaml` i18n。

**`LoopCapability` 协议**（`deeptutor/capabilities/protocol.py`）是可借鉴的关键抽象：

- 普通 capability **增强**聊天工具面（`owned_tools` 附加其上，不压制），满足"augment-don't-suppress"不变量；
- `KnowledgeCapability` 则**独占**工具面（`exclusive_tools=True`），用 KB 工具替代聊天内置；
- 可选 `pre_loop` 钩子在首轮 LLM 调用前注入一次有界预检上下文；`augment_kwargs` 允许注入服务端私有参数；`system_block` 贡献系统提示块。

【观察】这套"能力即插件、结果即信封"的协议让平台可以任意增删能力而不破坏主循环——悟理目前的 Agent 任务契约（`wuli.analysis.v2` 等）是逐任务定义的，尚未归一为"能力注册 + 阶段表 + 统一信封"。

### 3. ask_user：回合内暂停/恢复

`ask_user` 工具返回 `ToolResult.pause_for_user`；loop 暂停、发出 `pending_user_input` 事件、等待用户回复队列，用户回答后**恢复同一轮迭代**（把 "User answered:" 替换进工具消息）。约束：每条消息最多一个 ask_user、最多 4 个结构化问题。

【观察】悟理目前教师澄清发生在 Gateway 任务**外**（页面收集意见→提交任务）；DeepTutor 的回合内暂停机制如果引入，可以让教师意见作为结构化澄清**参与**同一轮 Agent 任务，减少往返。

### 4. 三层记忆：L1/L2/L3 + 证据回链

`deeptutor/services/memory/` 实现文件型三层记忆：

- **L1**：`trace/<surface>/<date>.jsonl` 追加式事件轨迹（`trace.py` `TraceEvent`），按表面+日期分片；
- **L2**：`L2/<surface>.md` 每表面精炼事实摘要；
- **L3**：`L3/<recent|profile|scope|preferences>.md` 跨表面综合（`paths.py` `L3Slot`）。

关键设计：

- **可溯源**：`consolidator/references.py` 的 Update 模式让 L2 引用当前存在的实体，Audit 模式给每个条目附原始 trace 证据；`meta.py` 用 `*.meta.json` 边车记录 `seen_entity_refs`，使综合层可按 id 差分追溯。
- **Update/Audit/Dedup 三模式**：`consolidator/modes/{update,audit,dedup,merge}.py`，Dedup 是迭代行级去重，带 `auto_after_update` 设置。
- **行级编辑引擎**：`line_doc.py` 用 ReplaceLineOp/DeleteLinesOp/InsertAfterOp 应用编辑，返回 EditReport——避免整文件重写。
- **预算守卫**：`guards.py` ToolBudgets 限制每轮合并的 token 预算和敏感词。
- **Memory Graph**：`snapshot/` 从记忆构建实体图，README 称"每条综合 claim 都可追溯到原始事件"。

【观察】悟理的 Knowledge Store（`wuli-memory.db`，SQLite/FTS5）是"题库检索 + 候选事件"的派生层，服务于 RAG；DeepTutor 的记忆是"学习者画像 + 证据链"，服务于个性化。悟理若要把"薄弱点分析"从统计升级为画像，可借鉴 L2/L3 的摘要-综合分层和 evidence 回链。

### 5. 多引擎 RAG + 版本化索引

`deeptutor/services/rag/factory.py` 用 provider 名选择检索管线，六种引擎可插拔：

- `llamaindex`（默认，本地向量 + BM25 混合）、`pageindex`（托管、无向量推理检索）、`graphrag`、`lightrag`、`lightrag-server`（外接服务）、`ima`（腾讯 IMA）。
- 每个 KB 在创建时绑定一个 provider，增删与检索都走同一管线（`knowledge/router` 强制）。
- 解析引擎独立可插拔：`services/parsing` 注册表（text-only/MinerU/Docling/markitdown），统一 `ParsedDocument` 让消费者不分叉。

**版本化索引**（`services/rag/index_versioning.py`）是悟理最可借鉴的运维设计：

```
data/knowledge_bases/<kb>/
    raw/                      # 源文件（不可变）
    version-1/                # 平铺索引版本
    version-2/
    metadata.json
```

- 每次重建写新 `version-N` 目录，旧版本保留，重建中途失败可诊断、不会破坏可用的旧索引；
- `EmbeddingSignature`（binding/model/dimension/base_url/api_version）哈希为稳定指纹，embedding 配置变化时自动匹配/重建对应版本；
- 单文档可从 error 状态 KB 删除，无需整体删建。

【观察】悟理当前 `kb.py rebuild` 是整体重建（防抖触发）；DeepTutor 的 `version-N + embedding signature` 方案为 RAG 升级提供了"旧索引不毁、失败可回滚"的路径。悟理 roadmap Issue 1/8（混合检索+概念本体）落地时可同步引入版本化索引。

### 6. 内容生成管线：Book / Co-Writer / Quiz

- **Book（living book）**：`book/engine.py` 把知识库/笔记/聊天历史编译成 18 种 typed blocks（text/callout/quiz/flashcards/timeline/code/figure/interactive/animation/concept_graph/deep_dive/user_note…）；生成前先给章节大纲供用户审阅；`book health` / `refresh-fingerprints` 检测源知识漂移。
- **Co-Writer**：`co_writer/edit_agent.py` 选区级编辑——选中一段，让模型重写/扩写/缩写，保留编辑历史与工具调用痕迹，前端以 accept/reject diff 呈现，未批准不落地。
- **Quiz / Question Bank**：`agents/question/` 生成选择题等，mastery 路径中已判分的题目流入 Question Bank。

【观察】"区块化编译 + 编辑留痕 + 未批准不落地"与悟理"教师版 Markdown 编辑 + 答案批准摘要失效"理念相通。DeepTutor 的 accept/reject diff 交互值得悟理工作台借鉴——目前悟理的教师编辑是直接改 Markdown 再复核，没有显式的 diff 审阅层。

### 7. 可视化：Visualize / Math Animator / Vision Solver

- `VisualizeCapability`：SVG / Chart.js / Mermaid / HTML 四类文本渲染，`validate_visualization` 做本地校验（XML 良构 / JSON 严格 / mermaid lint / HTML），SVG 可能触发 JSON-mode 转义回退。
- `math_animator`：Manim 多 agent 管线（concept_analysis→design→code_generation→code_retry→summary→render_output）。
- `vision_solver`：**单次视觉调用**从题图直接生成 GeoGebra 命令（几何可视化重建）。

【观察】悟理用视觉边车做"图形语义复核"，DeepTutor 让视觉模型直接产出可视化代码——两者互补。悟理的可视化（`build-physics-simulator`）强调物理语义与答案联动；DeepTutor 强调"图→可交互命令"的单步生成。悟理可借鉴的是"可视化产物同样过本地校验门禁"（XML/JSON/mermaid lint）这一思想。

### 8. Skills 生态与导入安全门禁

- 格式：Agent-Skills（`SKILL.md` YAML frontmatter + Markdown + `references/`），**从不整包注入系统提示**，manifest 每技能一行，`read_skill` 按需加载，`always:true` 才急切注入。
- 生态：EduHub（默认 hub）+ ClawHub 兼容，`deeptutor skill search/install/login/publish/update`。
- **导入安全门禁**（`services/skill/hub.py`）：
  - zip 解压防御：`_ZIP_MAX_ENTRIES=600`、单文件 4MB、总量 40MB、压缩比 200；
  - 后缀白名单，二进制永不落盘；
  - frontmatter 归一化并**剥离 `always:`**，防止下载技能强制注入每次系统提示；
  - 来源 registry 的 security verdict，未验证包需 `--allow-unverified`；
  - `.hub-lock.json` 记录 hub/版本/verdict/安装时间，供审计与更新。

【观察】悟理已有本地 Skill 体系但无"市场"；若未来接入社区 Skill，DeepTutor 这套门禁（zip-bomb 上限 + 后缀白名单 + always 剥离 + provenance lock）可以直接照搬——它与悟理 publication 门禁是同一套"不可信输入→白名单→审计"哲学。

### 9. 多用户与安全

- `auth.json` 可开多用户；`data/users/<uid>/` 每用户独立 workspace；第一个注册用户为 admin，拥有模型目录与授权。
- `grants.py`：**grant 缺失即无权限**，MCP 工具默认 deny-by-default，admin 显式授权才可用。
- `user-secrets/`：OAuth token 存 `data/system/user-secrets/<owner>/private/`，chmod 700，不挂在 exec sandbox 可达路径。
- 沙箱三后端：`RunnerSidecarBackend`（HTTP 到独立 runner 容器）、`BwrapBackend`（bubblewrap）、`RestrictedSubprocessBackend`。`Dockerfile.runner` 只带 stdlib HTTP server、uid 1000 非特权、无应用代码，容器逃逸也到不了宿主/应用。
- `UserExecQuota`：每用户并发与每分钟执行配额。

【观察】悟理是单教师端侧，无需多用户；但"grant 缺失即无权限"、"密钥与沙箱路径隔离"、"runner sidecar 最小化"这三个安全原则值得在端侧可信边界里继续强化（尤其未来接入本地 SLM 时）。

### 10. 可观测性与调试

- `StreamEvent` 协议 + `StreamBus` 扇出；`turn_runtime.py` 重启安全回合运行时，`events.jsonl` 供回合后回放。
- thinking-block 过滤：`InlineThinkFilter` + `clean_thinking_tags`（剥离 `<think>`/`<thinking>`）。
- LLM JSON 解析加固：`_shared/json_output.py` 提取第一个 JSON 对象，SVG 的 JSON-mode 转义回退。
- `AgentRequestOutcome` 统一记录请求结果（悟理 2026-07-24 已从 Headroom 借鉴同一概念并落地）。

【观察】两系统都重视回合可观测；DeepTutor 的"回合回放"（turn runtime + events.jsonl）值得悟理在失败排障层（`failure_intelligence.py`）参考——目前悟理有 checkpoint 重放，但缺少回合级事件流回放。

### 11. 评测与质量门禁

DeepTutor 有 325+ 测试文件与 CI（ruff lint + pytest + web Node tests），**但没有独立的 benchmark/eval 编排**：检索评测、答案质量成对评测、消融实验、闭卷评测均未在仓库中出现。质量保障靠单元测试 + 社区反馈 + 快速迭代（每周多个 release）。

【观察】这是 DeepTutor 相对悟理最明显的空白，也是悟理最不该学的"薄弱"。

### 12. 技术栈与部署

- Python 3.11+（FastAPI / uvicorn / pydantic 2 / llama-index / pocketbase / mcp），Next.js 16 / React 19 / TypeScript 5。
- CLI：Typer，`deeptutor` 入口；`deeptutor run <capability>` + `--format json` 输出 NDJSON（每行一个 StreamEvent，带 session_id），headless 安全（无 TTY 时 ask_user 自动空答）。
- 部署：单容器（`ghcr.io/hkuds/deeptutor`）或 docker-compose 三服务（pocketbase / deeptutor / sandbox-runner 不发布端口）。
- 根 `SKILL.md` 约 150 行，教会任何 tool-using LLM 用 CLI 驱动整个系统——DeepTutor 自身就是一个 Skill。

【观察】"NDJSON 机器协议 + 根 SKILL.md"使 DeepTutor 可以被另一个 Agent 完全驱动。悟理的自然语言入口已支持 Claude Code/Codex 等；若提供类似 `--format json` 的机器协议，可让更广的 Agent 生态接入。

### 13. 社区与治理

- 单维护者主导（@pancacake），分支策略 dev 为默认、PR 永不直接进 main，ruff/lint 检查；
- Roadmap 是 issue #498，社区投票；release 节奏每周多次；
- 交流渠道：Discord / WeChat / Feishu；贡献指南、行为证明、回归测试要求。

【观察】DeepTutor 的开源运营（roadmap 投票、发布节奏、社区激励）是悟理（单作者、竞赛导向）未来可借鉴的社区化路径，但不影响当前功能优先级。

---

## 第二部分：悟理是什么（与 DeepTutor 对照视角）

### 1. 定位与生命周期

悟理把"一道学生错题转化为经过教师确认、可课堂讲解、可课后学习、持续沉淀的教学资产"。目标用户是乡村学校教师：资源紧张、弱网、需本地部署。

完整生命周期状态机：

```text
uploaded → ingested → source-reviewed → analyzed → answered → answer-reviewed
         → [model-created → answer-re-reviewed → visualization-built → visualization-reviewed]
         → validated → delivered → reviewed
```

核心哲学：把"一次回答"升级为"有状态的教学生态位"——上一步未通过，下一步不能伪装完成。教师端 / 学生端 / 仿真 Skill 三方职责分离写死为信任域。

### 2. 总控与工作树

- `manage-student-error-library` 是唯一生命周期总控；`complex-process-decomposer` 把复杂过程拆成原子任务 DAG + 验证义务 + 有界反馈 + Work-Tree，输出后跑 `validate_decomposition.py` 校验。
- `problem_decomposition.py` 用 `complexity_screen()` 的 `STRONG_PATTERNS`（多目标/分类讨论/图像推断/复合场域）确定性初筛，决定是否走 W3 拆解。
- 拆解不是 prompt 工程：双层蓝图（物理过程 vs 求解操作）与验证义务都是结构化 schema，外部调度器只看到一个 `analysis.generate` 作业。

### 3. Agent Gateway：隔离候选与事务提升

- `AgentGateway.run()` 校验必填 `allowed_paths/input_paths`，自相矛盾契约直接拒绝（不耗 token）；按白名单复制到系统临时候选区，**不整目录复制、不跟符号链接、不放原始题图和批准记录**。
- 候选通过领域 validator 且 canonical 摘要未变后，在单题事务锁内 `_promote` 批量提升；带 checkpoint 恢复。
- provider 顺序：JSON adapter → openai-compatible → legacy-command → Codex CLI → Claude Code CLI；远程后端须回环地址或显式 `allow_remote`。成本阈值 30s、任务级冷却 300s、Claude 单次预算上限 0.50 美元。
- `analysis.generate` 走无工具结构化契约 `wuli.analysis.v2`，只返回 student_solution/teacher_audit/method_check/metadata/diagram。
- 知识证据注入 `.agent-context/knowledge-evidence.json`：经济档 2 条≈3500 字符、其他 4 条≈9000 字符，排除当前条目；当前题干、当前答案、教师意见永远优先且不可裁剪。

【观察】安全核心是"Agent 只写白名单候选区、确定性工具链负责落盘/校验/构建"——与 DeepTutor 的沙箱 sidecar 同一哲学，但悟理在"写操作"层面更严格（候选区而非运行时隔离）。

### 4. 知识库与 RAG

- `entries/` 是条目真源（record.json/problem.md/solution.md/student-solution.md/teacher-solution.md/source-review.md/physics-model.json）；`indexes/wuli-memory.db` 是派生检索层。
- `manage-student-error-library/scripts/knowledge_store.py`：仅标准库 `sqlite3`+FTS5（BM25，WAL，无 FTS5 降级本地扫描）；三路路由（metadata/problem/solution）条目层 RRF 融合；`precision-gated-v1` 剔除跨领域/几何/目标冲突候选；`secondary-conclusions.json` 为带 `conditions/forbidden` 的条件二级结论库。
- 检索基准：30 条教师确认集 Hit@5 1.0000、Recall@5 0.8292、MRR 0.8633，**Recall@5 低于 85% 门槛故不宣称提升**。
- 检索增强顺序锁定：JSON 标签 → FTS 扩展 → 混合排序 → 向量，每步有指标门禁。

### 5. 正确性门禁（悟理相对 DeepTutor 的最强差异点）

- `teaching_method_policy.py`：`high_school_standard`（主线≤5 步，禁积分/导数/矩阵/复数/大学力学）vs `olympiad_official`（允许微积分），`method_errors()` 确定性扫描。
- Claim 影子层：`claim_ledger.py` 中 Claim 状态 `candidate→verified/disputed/unresolved/superseded`，**Agent 只能提交 candidate，晋升仅由 orchestrator 依据确定性证书/独立 verifier 决定**；`correctness_policy.py` 冻结证书类型（source/deterministic/independent-agent/teacher）。
- `solution_verification.py`（`wuli.claim-verify.v2`）输出 `pass/conflict/insufficient` + decisive_checks；仲裁禁止多数投票，必须给决定性关系。
- 14 类故障注入全部检出、错误晋升 0；`cognitive_loop.py` 受控假设搜索（固定种子、隔离 Hypothesis Pool、硬上限输出 PROVISIONAL/UNRESOLVED）。
- 客观难度六维确定性量规（知识深度/知识整合/题型距离/过程复杂度/运算负荷/条件完备性），正式 0–5 步长 0.1，A–O 标杆锚定。
- `docs/evaluator.md` 只输出 `process_compliance`，**刻意不输出易误解的 `correctness`**。

【观察】正确性证据链把"物理真值"判定下放到断言级——Agent 多票一致 ≠ 晋升。这是 DeepTutor 完全没有的。

### 6. 分层答案与教学渲染

- 学生版由模型直出，教师版由 `analysis_artifacts.py` 从 `teacher_audit` 增量**确定性合成**，并生成安全 `assets/explanatory.svg`。
- `w3_rendering.py` 独立确定性 renderer，**只消费整体状态 `VERIFIED` 的 Proof Package**；Render Gate 查最终答案签名/目标覆盖/条件保持/LaTeX 配对/公式来源/方法 profile。
- "渲染不是再求解"：W3R 与求解解耦，表达失败最多同 Brief 重试一次，Claim 漂移/条件遗漏/无来源公式立即拒绝。

### 7. 可视化/仿真

- `build-physics-simulator` 只拥有 `physics-model.json` 的模型字段与离线 HTML/ZIP；明确教师请求才生成；无法表达必须返回 `unsupported`。
- 模型-答案联动：重新解析时模型作为只读上下文进候选区，事件分支与候选答案一致性校验，已有物理过程 SVG 不可被覆盖。
- 审批链：build-visualization → 重新 approve-answer → approve-visualization → `finish` **复制已审批字节而非重建**。

### 8. 隐私与发布门禁

- `student-site/` 只读静态站，公开 ID 用不可逆摘要；禁止原始上传、教师版解析、内部 JSON、绝对路径、本地 API 引用。
- `publication-images.json` 原图先做裁剪/遮挡 WebP 公开副本，源图变化即失效；`approve-source` 视觉复核门禁，无视觉能力模型不得自行解除。
- 发布为交付后独立门禁：`publication-draft/` 教师预览隐私确认 → 复制白名单产物；禁止自动 push GitHub。
- E2E 隔离：临时根目录 + 真实 HTTP + 确定性假 Agent，4 场景（lifecycle/visualization/publication/claim-evidence），只替换 OCR 与 Agent 两个不确定边界。

### 9. 薄弱点分析与个性化

- 排行用 `错误数×平均难度` 而非原始频率；`mastered` 需两次连续正确复习且间隔≥7 天。
- `knowledge_points/error_types/difficulty/grade` 是 Agent 建议、教师可改、最终批准才作为稳定观测。
- 变式题：检索原题 → 识别可迁移错因 → 改两个维度生成新题+完整答案。

【观察】悟理目前按"题目"聚合薄弱点，DeepTutor 按"学习者"画像——这是 B1 借鉴点的直接需求来源。

### 10. 评测纪律（悟理相对 DeepTutor 的另一最强差异点）

- 56 个 `test_*.py`、385 项通过；`teacher-console/scripts/` 30 个评测/基准脚本。
- W3 评测纪律：`w3_shadow_benchmark.py` 四步不可倒置（查未见题 → 冻结真值 → `truth-lock.json` → 影子重放）；新鲜 holdout 5 题/15 目标 W2/W3 均 15/15。
- `docs/reports/` 30+ 份报告：correctness 系列消融、w3r-baseline、evidence-agent MVP 系列、IPhO/CPhO 闭卷。
- "不达标就是未达标"：Recall@5<85% 不宣称提升、Evaluator 不冒充正确性、观察报告不替代因果 A/B。

### 11. 技术栈与部署

- 纯 Python（3.11+）+ 标准库优先（sqlite3/FTS5、pathlib、argparse）；ruff+mypy；端侧、`server.py` 只监听回环、无外部服务依赖。
- HTTP 写操作要求 `X-Teacher-Console: 1`；单题动作 approve-source/analyze/save-answer/approve-answer/request-revision/build-visualization/approve-visualization/finish/save-publication-images/prepare-publication/publish-publication。
- 一切派生缓存可重建、可降级（PDF pandoc→reportlab，仿真浏览器检查 passed→skipped）。

### 12. 治理与协作

- `docs/architecture-governance.md`：先定治理对象 → graphify 结构证据 → 悟理专用边界检查 → 用户心智审 UI。
- `darwin-skill`：SkillLens 9 维评分 + hill-climbing + 独立 judge 盲评 + 成果卡片。
- git log：单人（Yuanuite）高频同步型提交，能力提交后必带 tests+E2E+文档三件套同步。

### 悟理最独特的 8 个设计点

1. 候选区白名单 + canonical 摘要校验 + 单题事务锁提升：Agent 永远不写真源。
2. `wuli.analysis.v2` + `method_check` 最短高中方法：教学策略确定性编码，纯文本模型也可产出合规解析。
3. 统一 `physics-model.json` 消除答案-动画分叉：模型既是教学产物又是答案一致性 verifier。
4. 四类教师复核门禁 + digest 失效机制：任何修改自动撤销批准，"谁确认了哪个版本"可追溯。
5. Claim Ledger 断言级正确性证据链：晋升由确定性证书 + 双模型独立 verifier 决定，禁止多数投票。
6. W3 自适应路由 + fresh holdout 真值锁：先冻结教师真值再评测，禁止"先跑后补标签"。
7. 端侧优先 + 隐私分级门禁：evidence pack 隐私裁剪、publication-images 二次脱敏、公开 ID 不可逆摘要。
8. 评测纪律与诚实性："不达标就是未达标"是贯穿所有报告的叙事。

---

## 第三部分：异同点对比

### 相同点（理念相通）

| 维度 | 共同理念 | 悟理体现 | DeepTutor 体现 |
|---|---|---|---|
| Agent 统一编排 | 一个入口路由所有任务 | `manage-student-error-library` 生命周期总控 + Gateway 任务契约 | `ChatOrchestrator` + CapabilityRegistry |
| 输入隔离 | 不可信输入不能直接写真源 | Gateway 候选区 + 白名单 + 单题事务锁 | 沙箱 sidecar + grant 门禁 + MCP deny-by-default |
| 产物门禁 | 未批准不落地 | 四类教师复核门禁 + 摘要失效 | Co-Writer accept/reject diff、Book 大纲预审 |
| 可溯源 | 每个结论可追溯证据 | Claim Ledger 证书 + Knowledge Store evidence pack | L2/L3 引用 L1 trace + Memory Graph |
| 可视化校验 | 产物必须过本地校验 | physics-model 双层校验 + 浏览器运行时检查 | validate_visualization（XML/JSON/mermaid lint） |
| 失败可回退 | 失败不吞没、可恢复 | 检查点重放 + 一次性纠正 + cooldown | turn runtime 回放 + 索引版本化 |
| Skill 化 | 能力作为可加载指令 | `.claude/skills/` 真源 + 兼容软链接 | SKILL.md 按需加载 + EduHub 市场 |

### 关键差异（六个结构性分歧）

1. **目标与边界**：DeepTutor 是"什么都做的学习平台"（广度）；悟理是"错题闭环做到可信"（深度）。DeepTutor 有 30+ API 表面、悟理只有工作台+学生站两表面。
2. **编排模型**：DeepTutor 用统一 agent loop + capability 插件（能力注册表 + stage 契约 + 统一信封）；悟理用生命周期状态机 + 任务契约 + 确定性校验器。DeepTutor 的灵活性高，悟理的确定性高。
3. **记忆/个性化**：DeepTutor 有三层可溯源记忆（画像，L1 trace → L2 摘要 → L3 综合）；悟理有题库检索 Knowledge Store（语料）。DeepTutor 记"人"，悟理记"题"。
4. **正确性保证**：悟理有 Claim Ledger 语义证书、方法策略门禁、独立双模型验证器、14 类故障注入；DeepTutor 依赖模型自身 + 可视化本地校验（XML/JSON/mermaid lint）。悟理在这一维度显著领先。
5. **评测纪律**：悟理有 30+ 评测脚本、固定检索集、独立 holdout、真值锁、闭卷评测；DeepTutor 只有 325 个单元测试，**仓库内无 eval/benchmark 编排**。悟理在这一维度显著领先。
6. **个性化闭环深度**：DeepTutor 有 mastery_path（间隔复习 + 掌握度门禁）与 Question Bank 回流；悟理有基础复习调度（到期复习、两次连续正确）但无自适应（FSRS/BKT 在 roadmap）。DeepTutor 领先，但悟理已明确规划。

---

## 第四部分：悟理可借鉴的具体设计（分级）

### A. 高价值、低风险、可立即评估

#### A1. 统一能力信封与 stage 契约

- **借鉴**：把 `wuli.analysis.v2` / `wuli.problem-decompose.v1` / `wuli.claim-verify.v2` 等任务契约的**公共外皮**归一为"能力注册 + stage 表 + 统一结果信封"（类似 `emit_capability_result` 的 `AgentRequestOutcome` 已有雏形）。
- **价值**：新增 provider/任务时不再重复实现调用、归一化、失败分类；符合悟理 CLAUDE.md"任务决定工具形态，但执行结构统一"的已有方向。
- **门槛**：现有契约保持兼容；新能力走统一信封；unit + E2E 两个 fake adapter 同步。

#### A2. 版本化检索索引

- **借鉴**：`kb.py rebuild` 改为写 `version-N` 新目录 + 记录 embedding signature，保留旧索引直到新索引验证通过。
- **价值**：悟理 roadmap Issue 1/8（混合检索+概念本体）升级时有回滚路径；重建失败不毁坏可用的旧索引。
- **门槛**：索引路径契约不变；Knowledge Store 派生层可重建；测试覆盖"失败重建不破坏旧索引"。

#### A3. ask_user 回合内澄清

- **借鉴**：Gateway 任务内支持"结构化暂停→教师回答→同回合恢复"，让教师意见参与同一轮 Agent 推理而非预提交。
- **价值**：减少"教师先答错/漏答→Agent 跑完→再返修"的往返；把 `answer.revise` 的部分手动反馈改为结构化澄清。
- **门槛**：只适用于明确支持 pause 的 provider；`analysis.generate` 保持无工具结构化契约不变。

#### A4. Skills 按需加载 + 导入门禁

- **借鉴**：若悟理未来接入社区 Skill 或开放安装，直接采用 DeepTutor 门禁：zip-bomb 上限、后缀白名单、`always:` 剥离、`.hub-lock.json` provenance。
- **价值**：与悟理 publication 门禁同一哲学，可审计、可回滚。
- **门槛**：当前为远期储备（无实际需求时不立项），与 github-issues Issue 10（MCP 工具接口）并列评估。

### B. 中价值、需设计评审

#### B1. 三层记忆与学生画像

- **借鉴**：在现有 Knowledge Store（题库 RAG）之上，按学生（或薄弱点主题）建立 L2 摘要 + L3 综合的可溯源画像层，L1 用现有 candidate-archive/行为事件作为证据底。
- **价值**：把"薄弱点统计"（当前按知识点/错因聚合）升级为"可解释画像"；为 roadmap Issue 13（BKT 知识追踪）打地基。
- **门槛**：需定义"学生维度"（目前是单教师多学生？还是按题目聚合？）；Evidence Usage Ledger 已可复用。

#### B2. Co-Writer 式 diff 审阅

- **借鉴**：教师版答案复核页增加"编辑 diff 审阅层"——Agent 修改前后对比、逐条 accept/reject，替代当前的直接编辑+整体复核。
- **价值**：降低教师逐字核对成本；配合"摘要失效"门禁更精准。
- **门槛**：需要 diff 数据模型与前端交互；与现有 approval digest 摘要兼容。

#### B3. 回合级事件流回放

- **借鉴**：在 failure_intelligence 的 checkpoint 重放之上，增加回合级事件流（agent 的每轮 tool_call/tool_result/thinking）存档与回放。
- **价值**：排障从"只看失败快照"升级为"回放整轮推理"，对齐 headroom 报告"超时债务隔离"的思路。
- **门槛**：新增事件序列化格式；只存脱敏元数据，不存原始输入。

#### B4. mastery_path 间隔复习与掌握度门禁（对应 roadmap Issue 4/11）

- **借鉴**：DeepTutor `capabilities/mastery/` 用 chat loop + mastery 工具实现按主题类型的掌握度门禁，已评分题回流 Question Bank。
- **价值**：直接对标悟理 roadmap 的 FSRS v6 自适应间隔复习（Issue 11）；先做"掌握度门禁 + 题目回流"，再做自适应调度。
- **门槛**：需定义掌握度判定与题目-知识点映射；样本量门槛沿用 evolve-roadmap 纪律。

### C. 远期/不建议照搬

| 方向 | 判断 | 理由 |
|---|---|---|
| 统一 agent loop 重写 | 不建议 | 悟理的生命周期状态机+确定性校验器是正确性护城河，重写会丢确定性 |
| Partner IM 平台（15 渠道） | 不建议 | 单教师端侧无此需求；悟理学生站是只读公开站 |
| 多用户 grant 体系 | 不建议 | 单教师场景用不上；但"grant 缺失即无权限"原则保留 |
| 通用透明代理 | 不建议 | headroom 报告已论证同结论 |
| EduHub 生态运营 | 远期 | 悟理是竞赛/教学闭环，不是开源平台 |
| Book/living book 编译 | 可选 | 悟理已有学生包/PDF 交付，living book 是增强而非必需 |
| MCP 标准化工具接口 | 远期 | 与 github-issues Issue 10 并列；出现第三类外部工具且重复适配成为实际成本后再评估 |

### D. 顺带借鉴（低成本高收益）

1. **LLM JSON 解析加固**：DeepTutor 的 `extract_json_object`（提取首个 JSON 对象）+ SVG JSON 转义回退。悟理结构化契约输出已严格 schema 校验，可再加一层"首个对象提取"防御。
2. **thinking-block 分流展示**：DeepTutor 用 `InlineThinkFilter` 把 `<think>` 从流里拆出展示，`clean_thinking_tags` 剥离。悟理 `analysis.generate` 输出不含 thinking，但如未来接入会思考的模型，可展示而不仅是丢弃。
3. **SKILL.md manifest 一行 + 按需读取**：悟理 `.agent-context/` 已按任务档位裁剪 Skill 规则；可进一步确认只留 manifest 行、由任务按需读取全文，控制每轮 prompt 体积。
4. **test_prompt_parity（提示词一致性测试）**：DeepTutor 有协议级 + 提示词一致性测试。悟理已有两个 fake adapter 同步契约，可扩展为"提示词模板变更不破坏契约"的回归测试。

---

## 第五部分：与悟理现有管道的对照表

| DeepTutor 机制 | 悟理现状 | 判断 |
|---|---|---|
| 统一 agent loop + capability 协议 | Gateway 任务契约 + 确定性校验器 | 借鉴外皮（统一信封），保留确定性内核 |
| L1/L2/L3 记忆 + 证据回链 | Knowledge Store 题库 RAG + candidate-archive | B1 方向，先做画像摘要层 |
| ask_user 暂停/恢复 | Gateway 预提交教师意见 | A3，低风险试点 |
| 版本化 RAG 索引 | kb.py 整体重建（防抖） | A2，升级时直接引入 |
| 多引擎 RAG | 单 SQLite FTS5 + 影子策略 | 不急着多引擎；保留影子评测纪律 |
| Skills 市场 + 导入门禁 | 本地 Skill 无市场 | A4 远期储备，门禁可复用 |
| 多用户 grant | 单教师端侧 | 不照搬，原则保留 |
| 可视化本地校验 | physics-model 双层校验 + 浏览器检查 | 已具备，可借鉴 XML/JSON lint 细节 |
| 回合回放 | checkpoint 重放 | B3 增强 |
| mastery_path 掌握度门禁 | 固定周期复习（roadmap Issue 4） | B4，对应 FSRS 规划 |
| 评测/benchmark | 30+ 脚本、固定集、holdout | **悟理领先**，DeepTutor 可借鉴悟理 |
| 闭卷评测 | IPhO/CPhO 闭卷 | **悟理领先** |

## 悟理 roadmap 与 DeepTutor 能力的直接映射

悟理自己的 `docs/github-issues.md` 已列出待办；其中多项恰好是 DeepTutor 已实现或擅长的：

| 悟理 roadmap 待办 | DeepTutor 已有能力 | 借鉴方式 |
|---|---|---|
| Issue 1/8：混合语义搜索 + 物理概念本体 | 多引擎 RAG（llamaindex/pageindex/graphrag/lightrag）+ 版本化索引 | 借鉴版本化索引与引擎抽象，不抄引擎清单 |
| Issue 4/11：FSRS 自适应间隔复习 | mastery_path 掌握度门禁 + Question Bank 回流 | 先做门禁+回流，再做自适应（B4） |
| Issue 6/9：Generator-Verifier 复合 AI 模式 | visualize 确定性校验门禁 | 悟理 Claim Ledger 已更强，无需外借 |
| Issue 5/10：MCP 标准化工具接口 | MCP 服务 + CLI Apps 目录 | 远期，出现第三类工具再评估 |
| Issue 13：BKT 知识追踪 + 薄弱点诊断 | L1/L2/L3 记忆 + Memory Graph | 借鉴画像分层与证据回链（B1） |
| Issue 3/12：本地 SLM 离线推理 | provider 抽象 + 沙箱 | 悟理已有 provider 抽象；借鉴 runner sidecar 隔离 |

这组映射说明：悟理 roadmap 与 DeepTutor 的能力版图高度互补——悟理要补的，DeepTutor 恰好有成熟实现可参考；DeepTutor 缺的（正确性/评测），悟理已领先。

---

## 第六部分：借鉴优先级与决策卡

| 顺位 | 动作 | 类型 | 估计 | 结论 |
|---:|---|---|---|---|
| 1 | A1 统一能力信封 + stage 契约 | 流程执行 | 约 2 天 | 与现有 AgentRequestOutcome 合并推进 |
| 2 | A2 版本化检索索引 | 功能执行 | 约 1 天 | 随 Issue 1/8 检索升级一并落地 |
| 3 | A3 ask_user 回合内澄清 | 功能执行 | 约 1-2 天 | 先在 answer.revise 试点 |
| 4 | B1 学生画像三层记忆 | 功能执行 | 约 3-5 天 | 依赖 A1；为 Issue 13 打地基 |
| 5 | B2 Co-Writer 式 diff 审阅 | 功能执行 | 约 2-3 天 | 需前端交互设计 |
| 6 | B4 mastery 掌握度门禁 | 功能执行 | 约 2-3 天 | 对应 roadmap Issue 4/11，先门禁后自适应 |
| 7 | A4/B3 门禁与回放 | 功能执行 | 约 1-2 天 | 远期储备，条件触发 |

### 决策卡：统一能力信封

- 目标：让新增任务类型时不再重写调用/归一化/失败分类；所有 Gateway 终态经统一出口。
- 成功：新增 provider 或任务类型只需注册能力描述 + stage 表 + schema；现有契约测试全绿。
- 停止：若要求改变 `analysis.generate` 无工具结构化契约，或引入额外抽象层级，缩回为只聚合现有元数据。
- 回退：保留现有逐任务契约，仅把公共外皮抽成只读 adapter。

### 决策卡：学生画像三层记忆

- 目标：把薄弱点统计升级为可溯源画像，支持未来 BKT。
- 成功：L1 行为事件（candidate-archive 投影）+ L2 每主题摘要 + L3 综合画像；每条综合可追溯。
- 停止：若画像覆盖"教师真实学习数据"或引入学生隐私边界外数据，冻结为仅薄弱点主题画像。
- 回退：只保留 L2 摘要层，不建 L3。

### 决策卡：mastery 掌握度门禁

- 目标：让复习从固定周期升级为"掌握度判定 + 已掌握题回流"，为 FSRS 自适应调度打地基。
- 成功：掌握度判定确定性（两次连续正确 + 间隔≥7 天现有规则复用）；已掌握/未掌握题回流统计；样本门槛沿用 evolve-roadmap 纪律。
- 停止：若引入自适应调度但样本不足，保持固定周期；只有独立 holdout 达标才切换。
- 回退：维持现有 `kb.py due` 固定周期复习。

---

## DeepTutor 最值得借鉴的 10 个设计点（汇总）

| # | 设计点 | DeepTutor 实现 | 悟理借鉴分级 |
|---:|---|---|---|
| 1 | 能力即阶段契约（统一信封） | `BaseCapability` + stage 表 + `emit_capability_result()` | A1 |
| 2 | ask_user 回合内暂停/恢复 | `ToolResult.pause_for_user` → 同轮 resume | A3 |
| 3 | L1/L2/L3 记忆 + 证据回链 | trace jsonl → 摘要 md → 综合 md，Update/Audit 模式 | B1 |
| 4 | Update/Audit/Dedup 三模式合并器 | `consolidator/modes/` + meta seen-ref 差集 | B1 |
| 5 | 版本化多引擎 RAG | `version-N` 目录 + 嵌入签名 + provider 工厂 | A2 |
| 6 | 技能导入安全门禁 | zip 四上限 + 后缀白名单 + always 剥离 + .hub-lock | A4 |
| 7 | 按需技能加载 | manifest 一行 + `read_skill` 取全文 | D3 |
| 8 | 解析与检索解耦 | 统一 `ParsedDocument` + 内容寻址缓存 | 远期 |
| 9 | 代码执行三后端沙箱 + stdlib runner sidecar | 非特权 uid + 不装应用代码 | 原则保留 |
| 10 | 生成物确定性校验门禁 | `validate_visualization`（XML/JSON/mermaid lint） | 已具备，借鉴细节 |

顺带：LLM JSON 解析加固（D1）、thinking-block 分流展示（D2）、test_prompt_parity 一致性测试（D4）。

---

## 最终判断

DeepTutor 证明了"agent-native 学习平台"的工程可行性与社区吸引力：统一 loop、可溯源记忆、多引擎 RAG、Skills 生态、可被 Agent 驱动的机器协议，都是值得学习的成熟工程。但它最大的短板——**缺少教学正确性与评测纪律**——恰恰是悟理最擅长的领域。

对悟理而言，正确的借鉴姿态不是"成为 DeepTutor"，而是：

1. **把悟理已有的正确性护城河固化为可复用能力信封**（A1），让平台化扩展不稀释确定性；
2. **把题库检索升级为可溯源画像、把固定复习升级为掌握度门禁**（A2/B1/B4），服务"薄弱点→个性化复习"的长期目标；
3. **借用 DeepTutor 的运维与门禁细节**（版本化索引、Skills 导入门禁、ask_user、JSON 解析加固），在保持端侧可信的前提下渐进增强；
4. **继续在评测纪律上领先**——DeepTutor 没有的成对评测、独立 holdout、闭卷评测，是悟理未来如果开源/竞赛申报时最具说服力的差异点。

一句话：**悟理应该向 DeepTutor 学"广度工程"的成熟做法，但用自己已有的"深度可信"来定义它的用途。**

## 附：合规说明

- 悟理（MIT，2026）可借鉴 DeepTutor（Apache-2.0，HKUDS）的代码，只要保留 Apache-2.0 版权/许可声明并标注来源；借鉴**设计理念**（本文推荐方式）无需任何许可义务。
- DeepTutor 的 arXiv 论文 2604.26962 与 CITATION.cff 列出了引用方式；若悟理竞赛申报或文档中引用其方法，建议按 Citation File Format 标注。
