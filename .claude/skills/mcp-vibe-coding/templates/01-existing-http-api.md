# 模板 1:把已有 HTTP API 暴露成 MCP server

**适用**:你有一个 FastAPI / Flask / Django / Express / Spring Boot 之类的项目,
所有业务接口已经在跑,你想让外部 AI Agent 能调用这些 API。

**为什么不用 MiXer AI wizard**:MiXer AI 主要面向"贴代码 → 自动抽取工具"的轻场景。
如果你的项目复杂(几百个路由、鉴权链长、工具之间有依赖),wizard 抽出来的
工具定义会很糙,这种场景 vibe coding 出来质量更高。

---

## 提示词(整段复制给 agent,占位符替换为你的实际信息)

```
我有一个 [Python FastAPI / Python Flask / Node Express / ...] 项目在 `./[项目根目录]` 下,
业务接口在 `[相对路径,如 ./app/routers/]` 目录。

请新增一个 MCP server 把现有 API 暴露成可被 AI Agent 调用的工具。

## 技术约束(违反任意一条 = 推翻重写)

1. **SDK**:用官方 Python SDK 的 FastMCP,`pip install "mcp[cli]"`。
   不要自己实现 MCP 协议层(不要写 JSON-RPC 解析、不要自己管理 session)。
2. **Transport**:`streamable-http`,默认监听 `0.0.0.0:8080`,通过环境变量 `HOST` / `PORT` 可覆盖。
   额外生成一份 `mcp_server_stdio.py` 给 Claude Desktop 用(同一份代码,只换入口)。
3. **薄包装**:工具函数直接调现有路由的 handler 函数,**不要重写业务逻辑**。
   如果某条 API 有依赖(比如要先调 A 拿 token 再调 B),工具里把 A、B 调一遍再返回最终结果。
4. **inputSchema** 严格匹配原 API 的请求体(Pydantic model / JSON Schema / OpenAPI),
   `description` 写清楚每个字段的语义(给 LLM 看的,要自然语言)。
5. **鉴权**:从环境变量 `[API_KEY / BEARER_TOKEN / 其他]` 读,作为
   `[X-API-Key / Authorization: Bearer / 其他]` 头转发给上游。
6. **错误契约**:上游 4xx / 5xx 时,工具**返回结构化 dict**,
   形如 `{"error": "upstream_error", "trace_id": "<uuid>", "status": 404}`,
   不要 `raise` 异常(Agent 端拿到 traceback 会懵)。
7. **可观测性**:每个工具调用打一条 `logger.info`(trace_id / 工具名 / 耗时 / 状态码),
   方便事后排查。

## 产物(必须全部生成,缺一不可)

  - `mcp_server.py`           # 入口,streamable-http 版本
  - `mcp_server_stdio.py`     # Claude Desktop 用的 stdio 版本
  - `requirements.txt`        # `mcp[cli]>=1.0`, `httpx>=0.27`, `uvicorn>=0.30`
  - `Dockerfile`              # `FROM python:3.11-slim`, `EXPOSE 8080`
  - `README.md`               # 含:启动命令、env 变量表、curl 测试用例、
                              # Claude Desktop mcp.json 配置示例、
                              # Cherry Studio / Cursor 接入示例
  - `tools.json`              # 工具定义(MiXer AI 兼容格式,供平台二次编辑)

## 绝对不要

  - 改我现有的业务代码
  - 用 subprocess / 拉新进程跑 mcp_server(全 in-process importlib 加载)
  - 自己实现 MCP 协议(走 FastMCP)
  - 引入大型框架(Django / Flask-MCP 之类),FastMCP 就够
  - 在生成的代码里写死我的 API key 或其他秘密(全部走 env var)

## 启动验证

写完后请按 README 里的命令启动,然后用 `curl http://localhost:8080/mcp`
打一个 ping 请求(POST + JSON-RPC `initialize` 方法)确认能起来。
```

---

## 替换占位符清单

| 占位符 | 替换为 |
|--------|--------|
| `[Python FastAPI / ...]` | 你的后端技术栈 |
| `[项目根目录]` | 你的项目目录(agent 用来定位) |
| `[相对路径]` | 路由文件所在路径 |
| `[API_KEY / ...]` | 鉴权用的环境变量名 |
| `[X-API-Key / ...]` | 对应的 HTTP 头名 |

## 完成后,推荐用 MiXer AI 验证

1. 启动你生成的服务,确认 `curl http://localhost:8080/mcp` 正常
2. 把 `mcp_server.py` 贴进 MiXer AI 控制台的 Step 1"粘贴代码"
3. 让 wizard 重新抽 tools.json,看跟你 vibe coding 写出来的差异
4. 如果有差异(通常是 description 不够详细),调提示词再跑一次
