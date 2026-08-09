# 悟理 MCP 接口设计（草案）

> 状态：设计稿，尚未实施。本文只定义工具清单、schema、安全边界与实现架构，
> 供评审确认后再进入编码。

## 定位与原则

MCP（Model Context Protocol）把悟理已有的本地能力开放给其他 AI 客户端
（Claude Code、Cursor、Qwen Code 等）和本地脚本。

三条不可动摇的原则：

1. **MCP 是外壳，不是第二套逻辑。** 读操作直连现有 Python 模块
   （`knowledge_store` / `kb`），写操作转发 `teacher-console/server.py` 的既有
   HTTP 路由并携带 `X-Teacher-Console: 1`。门禁只保留一份，MCP 不复制、不绕过。
2. **Agent 语义沿用现有治理。** MCP 的写工具只返回 `queued` / `failed`，
   从不返回"内容已成功/已批准"；`blocked`、`failed`、`已入队` 均不算成功，
   调用方必须轮询作业后再走人工复核。
3. **教师批准与隐私门禁必须来自真实人工操作。** MCP 工具集不包含
   `approve-*`、`finish`、`publish-*`；任何调用 MCP 的 AI 都不能代替教师批准。

## 接入形态

| 形态 | 传输 | 用途 | 监听 |
|---|---|---|---|
| stdio MCP 服务器 | stdio + JSON-RPC | 其他 AI 客户端通过 `.mcp.json` 注册 | 无端口 |
| HTTP 形态（可选） | 回环 HTTP | 本地脚本/工具 | 仅 `127.0.0.1` |

**不做**：局域网/公网监听、远程用户接入、任何写 `student-site/` 的通道。
与现有工作台一致，服务只允许回环地址。

## 工具清单：Phase 1 只读（先行交付）

全部只读，直连 `knowledge_store` / `kb`（SQLite `mode=ro`），不触发重建、
不写任何文件、不改任何批准状态。

### `list_entries`

浏览错题库目录。底层：`kb.entry_dirs` + `record.json` 摘要。

- 参数：`folder`（可选，按本地文件夹过滤）、`status`（可选）、`limit`（默认 50）
- 返回：条目 id、标题、科目、年级、状态、文件夹、知识点、错因、updated_at
- 边界：不含题干全文、教师解析、原图路径

### `get_entry`

单题完整只读视图。底层：`server.py` `GET /api/entries/<entry-id>` 同源逻辑
（仅取只读字段，不调用任何写路由）。

- 参数：`entry_id`
- 返回：题干、分层答案（学生版/教师版）、复核状态（source/answer/visualization/
  publication）、评价摘要、可视化与发布状态、`delivery-manifest.json` 是否存在
- 边界：`w3_shadow` 私有快照默认不返回；教师版解析全文只在明确标记
  `include_teacher_answer: true` 时返回（供教师本人工具链使用）

### `retrieve`

**RAG 检索**，与工作台 Agent 拿到完全相同的检索结果。
底层：`knowledge_store.query(root, text, mode, top_k, ranking_policy)`。

- 参数：`text`（必填）、`mode`（`auto`/`teaching`/`raw`，默认 `auto`）、
  `top_k`（默认 5）、`ranking_policy`（`baseline`/`multi-route`/`intent-augmented`，
  默认 `baseline`）
- 返回：`status`、`freshness`（stale 时明确标注）、`query_plan`、
  `results[]`（标题、score、evidence_audit、evidence_coverage、matched_documents、
  knowledge_points、error_types、teaching_memory、recent_events）、`evidence_set`
- 边界：库缺失/旧 schema/stale 时返回 `unavailable` + 原因，不自动重建

### `build_evidence_pack`

给 Agent 用的裁剪证据包。底层：`build_agent_evidence()`；
W3 拆题蓝图场景走 `build_blueprint_evidence()`。

- 参数：`entry_id`（当前题，会被排除）、`text`、`task_type`
  （`analysis.generate`/`answer.revise`/`visualization.model`）、`top_k`、
  `char_budget`、`selection_policy`（`baseline`/`precision-gated-v1`/`evidence-set-v2`）
- 返回：`status`、`references[]`（title/知识点/方法/二级结论/匹配片段/审计/
  content_hash）、`evidence_set`（选择痕迹与拒绝计数）、`context_budget`
- 边界：stale 索引返回 `unavailable`；引用不含内部 ID、路径、原图

### `entry_events`

单题近期事件。底层：`knowledge_store` 的 `candidate_event` 只读查询。

- 参数：`entry_id`、`limit`（默认 5）
- 返回：事件类型、actor、状态、摘要、失败原因、反馈摘要
- 用途：外部 Agent 避免重复犯同类错误，与工作台 `recent_events` 一致

### `evaluator_summary`

单题质量评价。底层：`evaluation.json` 只读读取。

- 参数：`entry_id`
- 返回：状态、六维评分、失败项、警告项、`teacher_review_required`

### `library_stats`

库规模与索引健康度。底层：`knowledge_store` 的 meta/表计数只读查询 +
dirty marker 检查。

- 参数：无
- 返回：条目数、文档数、evidence_unit 数、schema 版本、索引 `generated_at`、
  freshness（`current`/`stale`）、FTS5 可用性

## 工具清单：Phase 2 写操作（异步作业模式，评审通过后实施）

写操作一律转发 `server.py` 路由（带 `X-Teacher-Console: 1`），沿用现有
`queued → running → completed/failed` 语义。

| 工具 | 转发路由 | 返回 | 说明 |
|---|---|---|---|
| `upload_entry` | `POST /api/upload` + `/api/run-upload` | `entry_id` + `source_clean` 排队状态 | 新条目自动排队 economy 档 `source.clean`；Agent 不可用返回 `degraded/manual-review-required` |
| `submit_analysis` | `POST /api/entries/<id>/analyze` | `queued` + `job_id` | 不自动生成交互仿真；检查点命中时零 Token 恢复 |
| `submit_revision` | `POST /api/entries/<id>/request-revision` | `queued` + `job_id` | 携带修改意见 |
| `build_diagram` | `POST /api/entries/<id>/build-diagram` | `queued`/`blocked` + 原因 | 仅在前置条件满足时执行 |
| `build_visualization` | `POST /api/entries/<id>/build-visualization` | `queued`/`unchanged`/`blocked` | 答案复核未通过返回 `blocked` |
| `source_clean` | `POST /api/entries/<id>/source-clean` | `queued`/`degraded` | 人工修正 OCR |
| `get_job` | `GET /api/jobs/<job-id>` | 作业状态 + `outcome` | MCP 无长任务，调用方必须轮询 |

**轮询契约**：写工具返回后，调用方轮询 `get_job` 直到 `completed`；
只有 `completed` 且 `result.status=completed` 才表示候选通过 Gateway 提升，
之后仍必须回到教师复核。失败结果可带 `failure_type` 与 `outcome.error_category`。

## 禁区（明确不交付）

与治理协议和 AGENTS.md 一致，MCP 工具集永不包含：

- `approve-source` / `approve-answer` / `approve-visualization`——批准必须来自
  实际页面使用者或明确的人工操作
- `finish`——治理明文规定 Agent 永远不能调用
- `save-publication-images` / `prepare-publication` / `publish-publication`——
  隐私门禁必须人工确认
- 任何暴露 API Key、模型凭据、学生原图、内部绝对路径、教师版解析全文（除非
  `get_entry` 显式 `include_teacher_answer` 且由教师本人工具链调用）的读取
- 任何写 `student-site/`、改 `model-registry.json` 之外配置的通道

## 实现架构

> 状态更新：Phase 1 只读接口已实施（2026-08-10），实现位于
> `deploy/mcp-server/`（自包含可部署目录，便于 Docker 构建与赛事投稿）；
> 本设计稿其余部分（Phase 2 写操作）仍待评审后实施。

```
deploy/mcp-server/
├── mcp_server.py            # MCP 服务器（FastMCP，stdio + streamable-http 双模式）
│   ├── 只读工具 → import knowledge_store / kb（connect_readonly）
│   └── 可选 Bearer 鉴权（WULI_MCP_API_KEY，投稿链接不启用）
├── requirements.txt         # mcp[cli] + uvicorn
├── Dockerfile               # python:3.11-slim，监听 0.0.0.0:8000，路径 /mcp
├── README.md                # 使用说明 + 投稿前验证清单（mcp-link-guide）
└── tests/test_mcp_server.py # mcp.client 端到端（9 项）
docs/mcp-interface.md        # 本文档
```

- 环境依赖：`mcp`（官方 SDK，FastMCP）；只读工具通过
  `knowledge_store.connect_readonly` 打开库，缺库/旧 schema/stale 返回
  `unavailable`，不触发重建。
- 双传输：`--stdio`（本地客户端）与 `--http`（Streamable HTTP，默认
  `0.0.0.0:8000`，路径 `/mcp`）；公网部署必须显式指定 `WULI_LIBRARY`，
  未指定时拒绝启动，绝不暴露真实错题库。
- 错误契约：业务异常统一返回 `{"error", "msg", "trace_id"}` 结构化 dict，
  不向 Agent 端抛 traceback；每条工具调用记录 `logger.info`（工具名/trace_id/耗时）。
- 隐私默认：`get_entry` 不返回教师版解析全文；`retrieve` 匹配片段默认只含
  标签与题干类文档，需显式 `include_solution_snippets=true` 才含解析正文。
- 写操作（上传、解析、返修、批准、发布）不在本服务内——批准与隐私门禁
  必须来自教师工作台的人工操作。

## 注册方法

其他 AI 客户端通过 MCP 配置注册 stdio 服务器：

```json
{
  "mcpServers": {
    "wuli": {
      "command": "python3",
      "args": ["/Users/qingyuan/Documents/zhangxinqi/deploy/mcp-server/mcp_server.py", "--stdio"],
      "cwd": "/Users/qingyuan/Documents/zhangxinqi"
    }
  }
}
```

HTTP 形态（本地脚本）注册到 `http://127.0.0.1:8787/mcp`（与工作台同进程挂载）。

## 测试与验收

### 测试（`tests/test_mcp_server.py`）

1. 每个只读工具在临时库上可调用，返回 schema 合规；
2. 库缺失/旧 schema/stale 时返回 `unavailable` + 原因，且**不触发重建**
   （断言 `wuli-memory.db` mtime 不变）；
3. 写工具在无 `X-Teacher-Console` 语义下（模拟未启动服务）返回明确错误；
4. 工具清单中不存在任何 `approve-*` / `finish` / `publish-*`；
5. `retrieve` 与 `build_agent_evidence` 的返回字段与
   `test_knowledge_store.py` 现有断言对齐；
6. 只读工具全程无文件写入（对临时库目录做前后快照比对）。

### 验收

- Phase 1 上线后，外部 AI 客户端能完成：浏览题库 → 检索相似题 → 取证据包 →
  读取单题状态，全程不触发任何写操作；
- 现有 42 项 knowledge_store/evidence 测试不受影响；
- 真实库 stale 时行为与工作台一致（返回不可用而非旧证据）。

## 后续演进（不在本期）

- W3 蓝图证据：`build_blueprint_evidence` 作为独立工具或 `build_evidence_pack`
  的 `mode=blueprint` 分支；
- MCP Resources：把条目暴露为 `wuli://entry/<id>` 资源供客户端引用；
- 教师审批的"人工确认"工具：仅当 MCP 客户端明确声明由人工驱动（如
  `confirm_human: true` + reviewer 参数）时才考虑，且必须与页面审批走同一
  校验路径——本期明确不做。
