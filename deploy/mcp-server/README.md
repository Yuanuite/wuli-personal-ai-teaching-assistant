# 悟理 MCP 服务

把悟理错题知识库的检索能力开放为 MCP（Model Context Protocol）接口。
薄包装实现：工具直接调用项目既有检索层（`knowledge_store` / `kb`），
只读、不重建索引、不触碰批准与发布状态。

## 工具清单（7 个只读工具）

| 工具 | 作用 |
|---|---|
| `list_entries` | 浏览错题库：id、标题、状态、知识点、错因 |
| `get_entry` | 单题学生可读信息：题干、复核与交付状态（默认不含教师版答案） |
| `retrieve` | RAG 检索：相似题召回 + 条件审计 + 覆盖度 |
| `build_evidence_pack` | Agent 用隐私最小化证据包（方法/易错点/既往教训） |
| `entry_events` | 单题近期教师/Agent 事件 |
| `evaluator_summary` | 单题六维质量评分与失败项 |
| `library_stats` | 库规模与索引健康度（freshness / FTS5 / 计数） |

## 本地使用（stdio，Claude Desktop / Qwen Code 等）

```bash
pip install "mcp[cli]" uvicorn
python deploy/mcp-server/mcp_server.py --stdio
```

Claude Desktop `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "wuli": {
      "command": "python3",
      "args": ["/绝对路径/zhangxinqi/deploy/mcp-server/mcp_server.py", "--stdio"]
    }
  }
}
```

默认库为 `student-error-library/`；可用 `--library <path>` 或环境变量
`WULI_LIBRARY` 覆盖。

## 公网部署（Streamable HTTP，赛事投稿形态）

```bash
# 1. 准备知识库（先重建索引，否则工具返回 knowledge-store-missing）
python3 .claude/skills/manage-student-error-library/scripts/knowledge_store.py \
  --library student-error-library rebuild

# 2. 启动 HTTP 服务（默认 0.0.0.0:8000，路径 /mcp）
WULI_LIBRARY=/path/to/student-error-library \
  python deploy/mcp-server/mcp_server.py --http
```

Docker：

```bash
docker build -f deploy/mcp-server/Dockerfile -t wuli-mcp .
docker run -p 8000:8000 \
  -v /path/to/student-error-library:/data/library \
  -e WULI_LIBRARY=/data/library wuli-mcp
```

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `WULI_LIBRARY` | stdio: `student-error-library`；http: 无 | 知识库目录。**HTTP 模式必须显式设置**，未设置时启动失败并提示 |
| `WULI_HOST` | `0.0.0.0` | HTTP 监听地址（部署平台要求） |
| `WULI_PORT` | `8000` | HTTP 监听端口 |
| `WULI_MCP_API_KEY` | 空（不启用鉴权） | 设置后要求 `Authorization: Bearer <key>`。赛事投稿链接**不要**设置（平台要求无需额外请求头） |

### 隐私边界（不可绕过）

- HTTP 公网模式未指定 `WULI_LIBRARY` 时**拒绝启动**，绝不暴露真实错题库；
- `get_entry` 默认不返回教师版/学生版解析全文（`include_teacher_answer` 默认
  `false`）；
- `retrieve` 的匹配片段默认只含标签与题干（`metadata`/`problem`/`source_review`），
  不暴露解析正文；需要解析片段时显式 `include_solution_snippets=true`；
- 所有响应不含 API Key、内部绝对路径、原图与数据库文件。

## 投稿前验证清单（mcp-link-guide）

部署到支持长期运行 HTTP 的平台（ModelScope / 云主机等），绑定公网域名与有效
HTTPS 证书，把路由暴露为稳定路径（默认 `/mcp`）。确认地址不是
`localhost`、内网 IP、临时隧道或带账号密码的 URL，评测期间保持在线。

逐个确认：

1. **initialize 握手成功**
   ```bash
   curl -fsS -X POST https://<your-domain>/mcp \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"0.1"}}}'
   ```
   应返回 200 且含 `serverInfo.name = "wuli"`。
2. **`tools/list` 在 15 秒内返回至少一个工具**
   ```bash
   curl -fsS -X POST https://<your-domain>/mcp \
     -H 'Content-Type: application/json' \
     -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
   ```
   应返回 7 个工具，每个工具都有合法 JSON Schema（FastMCP 自动生成）。
3. **调用一次核心工具并得到非错误响应**（先本地确认，再对公网地址执行）：
   用任意 MCP Inspector 或客户端连接最终 URL，调用 `library_stats` 或
   `list_entries`，确认返回 `status=ok` 或条目列表。
4. **从外部网络访问仍可用**：不要只在开发机验证。

验证失败时按顺序排查：HTTPS 证书 → 公开 DNS → 路由路径（`/mcp`）→
Streamable HTTP transport → 服务日志 → `tools/list` 返回值。

> 注意：若本机配置了系统代理（macOS 系统设置 / Clash 等），`httpx` 客户端默认
> （`trust_env=True`）会把本机回环请求也代理出去，表现为 `502 Bad Gateway` 且
> 服务端无访问日志。这是客户端代理问题，不是服务问题——用 `curl`（不读系统代理）
> 或给客户端设置 `NO_PROXY=127.0.0.1,localhost` 即可。

## 测试

```bash
python -m pytest deploy/mcp-server/tests/test_mcp_server.py -q
```

测试通过 `mcp.client` 以 stdio 方式拉起真实服务，覆盖：工具清单与 Schema、
7 个工具 happy path、未找到条目的 `not_found` 错误、缺索引库的
`knowledge-store-missing` 结构化错误、`get_entry` 默认隐藏教师答案。

## 与既有接口的关系

- 读路径复用 `knowledge_store.query()` / `build_agent_evidence()`（与工作台
  Agent 完全相同的检索逻辑）；
- 写路径（上传、解析、返修、批准、发布）**不在本服务内**——批准与隐私门禁
  必须来自教师工作台的人工操作；
- 本服务是 `docs/mcp-interface.md` 设计稿的 Phase 1 只读实现。
