# 模板 4:修改 MiXer AI 已经生成的 MCP server

**适用**:你已经在 MiXer AI 控制台跑过 4 步 wizard,得到一个 `mcp_server.py`,
但现在想加新工具、改工具的 description、调整鉴权逻辑、或修某个 bug。

**为什么用 vibe coding 而不是回 wizard**:wizard 是"重新生成一份",会**覆盖**你之前的修改。
vibe coding 改的是"在已有代码上加东西"。

---

## 提示词(整段复制给 agent)

```
我在 `[项目目录]` 下有一份 MiXer AI 平台生成的 MCP server,文件包括:
  - mcp_server.py     # FastMCP 入口,含所有工具函数(我之前用 MiXer AI 生成的)
  - tools.json        # 工具定义数据副本
  - config.yaml       # 配置参考
  - README.md         # MiXer AI 生成的说明

现在我需要对它做如下修改(下面列的是示例,按你的实际情况改):

## 修改 1:[一句话描述,如"加一个新工具"]

  - 工具名:`[new_tool_name]`
  - 作用:[用自然语言描述功能,例"按时间范围查仓库的 commit 数"]
  - 参数:
    - `param1: str` — 含义
    - `param2: int = 30` — 含义,默认 30
  - 上游 API:`[POST /xxx 或 GET /xxx]`
  - 鉴权:复用现有的 `API_KEY` 环境变量

## 修改 2:[再列一个,或写"无"]

  - ...

## 约束(关键)

1. **不要重新生成整个 mcp_server.py** — 我要的是**增量修改**,保留现有工具函数和注释
2. **保持现有代码风格**:
   - 每个工具函数都是 `async def`,带 `@mcp.tool()` 装饰器
   - 错误统一返回 `{"error": ..., "trace_id": ..., ...}` 格式
   - 用 `httpx.AsyncClient(timeout=_tool_timeout())` 调上游
3. **路径参数 / 查询参数 / body 参数** 的分类跟现有工具保持一致:
   - 路径参数:替换 URL 里的 `{name}` 占位符
   - 查询参数:`_query_params` dict,过滤掉 None
   - body 参数:`_body_params` dict,过滤掉 None
4. **inputSchema** 在 docstring 里写清楚(`Args:` 段),让 MiXer AI 后续能再抽一次

## 改完后请做

  1. 用 `python -c "import ast; ast.parse(open('mcp_server.py').read())"` 验语法
  2. 用 `python mcp_server.py` 跑起来(设好 `USER_API_ENDPOINT` 和 `API_KEY`)
  3. `curl http://localhost:8080/mcp` 打一个 initialize 确认没崩
  4. 调一下你新加的工具,确认返回结构对
  5. 列一下**改了哪些行 / 加了多少行**,方便我 review

## 不要

  - 重命名现有工具(URL 稳定性 > 一切)
  - 改 transport(streamable-http 改 stdio 是另一个 PR 的事)
  - 引入新依赖(除非确实需要,先问我)
  - 把现有 `_query_params` / `_body_params` / `path_template` 那套结构重构成"更优雅的版本"
    (看着不爽可以提,但不要在这次改动里顺手做)
```

---

## 替换占位符清单

| 占位符 | 替换为 |
|--------|--------|
| `[项目目录]` | mcp_server.py 所在目录 |
| 修改 1 / 2 | 你具体要加的工具或要改的地方 |
| `[new_tool_name]` | 你的新工具名 |
| `[POST /xxx / GET /xxx]` | 上游 API |
| 路径/查询/body 分类 | 看现有工具怎么分的,跟齐 |

## 常见修改场景速查

| 你想做的事 | 提示词关键句 |
|-----------|-------------|
| 加一个新工具 | "修改 1:加 `[name]` 工具,参数 ... 上游 ..." |
| 改现有工具的 description | "修改 1:把 `[tool_name]` 函数的 docstring 第三行改写,说明 X" |
| 加一个鉴权头(比如 OAuth) | "修改 1:在 `headers` 构造里加 `Authorization: Bearer ${OAUTH_TOKEN}`,env var 读 `OAUTH_TOKEN`" |
| 调超时时间 | "修改 1:把 `TOOL_TIMEOUT` 默认从 30 改成 60" |
| 工具调用加重试(3 次) | "修改 1:用 `tenacity` 给上游调用加 `@retry(stop=stop_after_attempt(3), wait=wait_exponential())`" |
| 加分页参数 | "修改 1:给 `list_xxx` 工具加 `page: int = 1, page_size: int = 20` 参数,作为 query param 转发" |

## 改完后怎么回到 MiXer AI

1. 本地测好(`pytest` / `curl`)
2. 重新上传 `mcp_server.py` 到 MiXer AI 控制台(用 Step 1 的"粘贴代码"或"上传代码压缩包")
3. wizard 会重新抽 tools.json,跟旧的对比,如果新工具被正确识别,说明成功
4. 点"启动" → 用新的 server_id 接管线上服务(URL 会变,要通知客户端切换)
