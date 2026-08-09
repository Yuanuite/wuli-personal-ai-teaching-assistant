---
name: mcp-vibe-coding
description: 帮用户给 AI 编码 agent(Claude Code / Cursor / Copilot 等)写一份"用自然语言实现 MCP server"的提示词。覆盖 4 种场景:已有 HTTP API、Python 库/CLI、从零写新 server、修改 MiXer AI 生成代码。Use when user wants to build an MCP server by giving an AI coding agent a natural-language prompt, or when user mentions "MCP", "Model Context Protocol", "vibe coding MCP", "把 X 变成 MCP", "MiXer AI 生成的代码改一下".
---

# MCP Vibe Coding

给 AI 编码 agent 一段自然语言提示词,让 agent 自己实现一个 MCP server。
本 skill 提供**场景选择 → 模板套用 → 6 条必守原则**三步走的指引。

## 快速开始

1. **选场景** — 4 种里挑一种(看下面)
2. **打开对应模板** — `templates/0X-*.md`,把 `[占位符]` 换成用户的实际信息
3. **把模板整段贴给 agent** — Claude Code / Cursor / Copilot 都行
4. **agent 实现完后,用本项目 MiXer AI 的 `/export` 端点验证** — 不通过就改提示词重跑

## 4 种场景

| # | 场景 | 模板 | 何时用 |
|---|------|------|--------|
| 1 | 已有 HTTP API(FastAPI / Flask / Django / Express ...) | [templates/01-existing-http-api.md](templates/01-existing-http-api.md) | 用户项目里已有 REST API,想暴露给 Agent |
| 2 | Python 库 / CLI | [templates/02-python-library-cli.md](templates/02-python-library-cli.md) | 用户有可 import 的函数或 CLI 工具,想包成 MCP |
| 3 | 从零写新 MCP server | [templates/03-from-scratch.md](templates/03-from-scratch.md) | 用户没有现成代码,直接描述功能让 agent 写 |
| 4 | 修改 MiXer AI 生成的代码 | [templates/04-modify-mixer-generated.md](templates/04-modify-mixer-generated.md) | 用户已经在 MiXer AI 控制台生成过 MCP server,要加新工具/改逻辑 |

## 6 条必守原则(违反任意一条都会出大问题)

1. **明确 SDK** — 必须用官方 Python SDK 的 `FastMCP`(`pip install "mcp[cli]"`),不要自己造协议层
2. **明确 transport** — `stdio`(本地 Claude Desktop)/ `streamable-http`(远程客户端)/ `sse`(老协议),三选一
3. **明确鉴权** — `X-API-Key` / `Bearer` / `OAuth`,提前说怎么传
4. **薄包装约束** — 工具函数**直接调用**现有业务逻辑,不要让 agent "优化" 一遍
5. **错误契约** — 工具出错时返回**结构化 dict**(`{"error": ..., "trace_id": ...}`),不要 `raise`(Agent 端拿到 traceback 会懵)
6. **明确产物清单** — 列出要交付的文件(mcp_server.py / requirements.txt / Dockerfile / README.md 等),agent 不会自作主张

## 模板使用方式

模板文件是**自包含**的,直接复制整段到 agent 对话框。
文件里的 `[方括号占位符]` 必须替换成用户实际信息,否则 agent 会瞎猜。

## 验证产物

agent 写完后,**用本仓库的 MiXer AI 平台验证**:

```bash
# 1. 把生成的 mcp_server.py + tools.json + config.yaml + README.md 打包
zip my-server.zip mcp_server.py tools.json config.yaml README.md

# 2. 上传到 MiXer AI 控制台 → 用 export 端点下载回来比对
#    (用 MiXer AI 自带的"下载源码"按钮,验证 zip 完整性)
```

或者更简单:把 mcp_server.py 丢进 MiXer AI 的"输入代码"步骤,让 wizard 帮你生成对应的 tools.json,看结构对不对。

## 进阶参考

- 详细的 MCP 协议入门:https://modelcontextprotocol.io/
- FastMCP 文档:https://github.com/modelcontextprotocol/python-sdk
- 本项目的自部署文档:`/docs/self-deployment`(讲清怎么把生成的 server 跑起来)
