# 模板 3:从零写一个新的 MCP server

**适用**:你没有任何现成代码,直接告诉 agent "我想要个能 X 的 MCP server",
让 agent 给你从零写出来。

**为什么不用 MiXer AI wizard**:没有代码可喂,自然走 vibe coding。

---

## 提示词(整段复制给 agent)

```
我要从零做一个 MCP server,功能:[用 1-2 句话清晰描述核心价值,例:"GitHub 仓库洞察查询"]

## 工具列表(预期 N 个,先用自然语言列出来,后面会细化)

  1. `[tool_name_1]` — [一句话说明干什么],参数:[param1: type, param2: type = default]
  2. `[tool_name_2]` — ...
  3. ...

(例:)
  1. `get_repo_info(repo: str)` — 拿仓库基本信息(stars / forks / language)
  2. `list_user_repos(user: str, sort: str = "updated")` — 列出用户仓库
  3. `get_commit_activity(repo: str, days: int = 30)` — 提交热力(每天 commit 数)
  4. `get_top_contributors(repo: str, n: int = 10)` — Top 贡献者
  5. `search_repos(query: str, language: str | None = None)` — 按关键词搜

## 技术约束

1. **SDK**:FastMCP(`pip install "mcp[cli]"`),不要自己造协议
2. **Transport**:`streamable-http`,监听 `0.0.0.0:8080`
3. **上游**:`[上游 API URL,如 https://api.github.com]` 或 `[本地资源,如 SQLite 文件 ./data.db]`
4. **鉴权**:从环境变量读 `[GITHUB_TOKEN / API_KEY / 其他]`,作为
   `[Authorization: Bearer / X-API-Key / 其他]` 头带上
5. **限速处理**(如果上游有 rate limit,如 GitHub):返回 403 + `x-ratelimit-remaining: 0` 时,
   工具返回 `{"error": "rate_limited", "reset_at": <unix_ts>}`,
   Agent 端可以基于这个 sleep 后重试
6. **缓存**(可选,推荐):[同一仓库 5 分钟内的元数据] 用 `functools.lru_cache(maxsize=128)` 缓存
7. **可观测性**:每个工具调用打 `logger.info`,格式固定
   `{"event": "tool_call", "trace_id": ..., "tool": ..., "duration_ms": ..., "status": ...}`
8. **错误契约**:任何异常都返回结构化 dict,不要 raise

## 产物

  - `mcp_server.py`           # 入口
  - `requirements.txt`        # 依赖,版本号要写
  - `Dockerfile`              # `FROM python:3.11-slim`
  - `docker-compose.yml`      # 挂 `.env` 文件、映射端口
  - `.env.example`            # 所有环境变量名(值留空,提示用户填)
  - `tests/test_mcp.py`       # 用 `respx`(httpx mock)或 `aioresponses` mock 上游,
                              # 测每个工具的 happy path + 限速降级
  - `.github/workflows/ci.yml` # ruff + pytest
  - `README.md`               # 含:功能介绍、env 变量表、启动命令、
                              # curl 测试用例、Claude Desktop / Cherry Studio 接入示例

## 代码风格

  - 用 `async def` 全程,不要混 sync
  - 类型注解完整(`from __future__ import annotations` + Python 3.10+ 语法)
  - 每个工具函数单独抽一个 `_impl_xxx()` 内部函数,工具函数本身只做参数清洗 + 调 `_impl_xxx` + 错误捕获
  - 错误用自定义异常类分层:`UpstreamError(status, body)` / `RateLimited(reset_at)` / `InvalidInput(field)`
  - 集中处理:写一个 `_call_tool(tool_name, fn, *args)` 装饰器,统一打 log / 加 trace_id / 异常 → dict

## 验证

  1. `pytest tests/` 全过
  2. `docker compose up` 起来,`curl http://localhost:8080/mcp` 通
  3. 跑一遍每个工具,确认返回结构稳定(连续调 3 次,schema 一致)
  4. 关掉上游(用 `respx` mock 4xx),确认工具返回结构化错误,不崩
```

---

## 替换占位符清单

| 占位符 | 替换为 |
|--------|--------|
| 核心功能一句话描述 | 你的 MCP server 干什么用 |
| 工具列表 | 你的具体工具(每个的 name / 一句话 / 参数) |
| `[上游 API URL]` 或 `[本地资源]` | 数据源 |
| `[GITHUB_TOKEN / ...]` | 鉴权环境变量名 |
| `[Authorization: Bearer / ...]` | 对应的 HTTP 头 |
| 缓存策略 | 你的具体缓存规则 |

## 完成后,用 MiXer AI 跑一遍反向验证

1. 把 vibe coding 写出来的 `mcp_server.py` 贴进 MiXer AI 控制台 Step 1
2. wizard 会自动抽 tools.json 出来
3. **对比**:wizard 抽的 vs 你 vibe coding 写的,如果差异大(尤其是 description),
   说明你的工具描述还不够自然语言化,改提示词再跑

## 进阶:混合方式

如果你不擅长写 Python,但需求清晰,可以用"vibe coding 写骨架 + MiXer AI 抽细节":
- vibe coding:让 agent 写出 mcp_server.py 主体
- MiXer AI:用 wizard 抽出"标准格式"的 tools.json,确保 schema 跟 MCP 协议对齐
- 手动合并:wizard 抽的 tools.json 替换 vibe coding 写的那份
