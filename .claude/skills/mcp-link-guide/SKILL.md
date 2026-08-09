---
name: mcp-link-guide
description: MCP 链接生成指南：帮助参赛选手把 Agent 部署为公网 HTTPS Streamable HTTP MCP，生成可提交的 MCP 链接并在投稿前验证握手与 tools/list。Use when a user asks how to obtain, deploy, inspect, troubleshoot, or submit a self-hosted MCP URL for competition evaluation.
---

# MCP 链接生成指南

## 目标

生成一个赛事平台可直接访问的链接，例如：

```text
https://agent.example.com/mcp
```

该地址必须能够完成 MCP 初始化并通过 `tools/list` 返回至少一个工具。普通网页、OpenAPI 地址、仅本机可访问的地址或 stdio 命令都不能作为投稿链接。

## 实现服务

优先使用官方 MCP SDK 或 FastMCP 创建 Streamable HTTP 服务。至少提供一个具有明确名称、描述和 JSON Schema 输入参数的工具，并让服务监听部署平台要求的 `0.0.0.0:$PORT`。

不要把长期 Token、云密钥或数据库密码写入源码。需要密钥时使用部署平台的环境变量；赛事投稿字段目前只接受无需额外请求头即可连接的 MCP 地址。

## 部署并得到链接

1. 将服务部署到支持长期运行 HTTP 服务的平台、容器或云主机。
2. 绑定公网域名并启用有效 HTTPS 证书。
3. 将 MCP 路由暴露为稳定路径，例如 `/mcp`。
4. 确认地址不是 `localhost`、内网 IP、临时开发隧道或带账号密码的 URL。
5. 保证评测期间服务持续在线，不要在提交后更换工具名称或输入 Schema。

## 投稿前验证

使用任意支持 Streamable HTTP 的 MCP Inspector 或 MCP 客户端连接最终 URL，并确认：

- MCP `initialize` 成功；
- `tools/list` 在 15 秒内返回至少一个工具；
- 每个工具输入 Schema 是合法 JSON Schema；
- 至少调用一次核心工具并得到非错误响应；
- 从外部网络访问时仍可用，而不只是开发机本地可用。

## 赛事计分规则

在作品提交页填写该 URL 后，平台会执行真实 MCP 握手和工具解析。验证成功后，该地址用于 MCP 动态测评，最终得分额外增加 5 分并封顶 100；仅填写 URL 不会获得加分。验证失败时不加分，平台可回退到源码 ZIP 自动部署链路。

遇到失败时，依次检查 HTTPS 证书、公开 DNS、路由路径、Streamable HTTP transport、服务日志和 `tools/list` 返回值。
