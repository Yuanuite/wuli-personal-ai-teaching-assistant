# 模板 2:把 Python 库 / CLI 工具包成 MCP server

**适用**:你有一个 Python 库(`pip install my-tool` 那种),或一个 CLI 工具
(命令行调用 `my-tool search --query foo`),想把它暴露成 AI Agent 能调用的工具。

**为什么不用 MiXer AI wizard**:MiXer AI 是 LLM 看代码抽 HTTP 工具;库/CLI 没有 HTTP 路由,
wizard 抽不出东西来。

---

## 提示词(整段复制给 agent)

```
我有一个 Python [库 / CLI 工具],包名是 `[package-name]`,在 `[./my_tool/]` 目录下。
它的公开 API / CLI 命令如下:

```python
# [如果是库]
# my_tool/__init__.py 暴露的函数:
def search(query: str, limit: int = 10) -> list[dict]: ...
def fetch(url: str, timeout: float = 5.0) -> str: ...
def summarize(text: str, max_words: int = 100) -> str: ...
```

```bash
# [如果是 CLI]
$ my-tool search --query "foo" --limit 10
$ my-tool fetch --url "https://..." --timeout 5
$ my-tool summarize --text "..." --max-words 100
```

请用 FastMCP 把它包成 MCP server,工具直接 import / subprocess 调用我的库或 CLI,
**不要重写业务逻辑**。

## 技术约束

1. **SDK**:FastMCP(`pip install "mcp[cli]"`),不要自己造协议
2. **Transport**:默认 `stdio`(适合 Claude Desktop 这种本地客户端),
   加一个 `--http` 命令行参数切到 `streamable-http`(`uvicorn.run(mcp.streamable_http_app(), ...)`)
3. **inputSchema 自动生成**:
   - 如果是库,用 `inspect` 读函数签名 + `inspect.getdoc` 读 docstring,自动生成 JSON Schema
   - 如果是 CLI,从 argparse / click / typer 的参数定义生成
4. **异步**:库里的 `def` 同步函数,用 `asyncio.to_thread(func, *args)` 包成异步;
   库里已有的 `async def` 直接 await。**不要**用阻塞的 subprocess 跑库函数
5. **错误契约**:`ValueError` / `FileNotFoundError` 等业务异常 → 返回
   `{"error": "invalid_input" / "not_found", "msg": str(e), "trace_id": "<uuid>"}`。
   不要 raise(Agent 端拿 traceback 会懵)。
6. **CLI 子进程**(如果是 CLI):用 `asyncio.create_subprocess_exec` 异步跑,
   通过 `sys.stdin` 喂参数、`stdout` 拿结果(JSON 行格式或 plain text)。
   设 `timeout=30s`,超时返回 `{"error": "cli_timeout"}`。

## 产物

  - `mcp_server.py`           # 入口,支持 `--stdio` / `--http` 两种模式
  - `requirements.txt`        # `mcp[cli]>=1.0`, `httpx>=0.27`, `uvicorn>=0.30`, `[你的包名]`
  - `tests/test_mcp.py`       # 用 `mcp.client` 测每个工具 happy path + 1 个错误 path
  - `README.md`               # 含:pip install 步骤、Claude Desktop mcp.json 配置
                              # (command 字段要写虚拟环境里的 python 绝对路径)

## 关键提示(给 agent 看的)

  - inputSchema 的 `description` 字段要用 docstring 里的内容,**不是函数名**
  - 工具的 `name` 用 snake_case,跟原函数名一致
  - 如果库有副作用(写文件、发请求),工具的 description 里要写明"会修改 X"
  - 如果工具返回的是 dataclass / 自定义类,要 `dataclasses.asdict()` 一下再返回

## 验证

  - 跑 `tests/test_mcp.py`,5 个工具都要通
  - 手动用 `mcp-cli` 或 `Claude Desktop` 连一下,确认工具出现在列表里
  - 调一个工具,看返回结构是 dict(不是 str repr)
```

---

## 替换占位符清单

| 占位符 | 替换为 |
|--------|--------|
| `[库 / CLI 工具]` | 二选一 |
| `[package-name]` | 你的包名(用于 `pip install` 和 `import`) |
| `[./my_tool/]` | 你的包目录 |
| 函数 / CLI 命令列表 | 替换为你的实际 API |

## 常见坑

1. **库函数是同步的,agent 默认会 `await` 报错** — 必须 `asyncio.to_thread` 包一下
2. **CLI 输出是 ANSI 颜色码** — 解析前先 strip ANSI
3. **CLI 退出码非零** — 当成功处理,不要 raise(用户 CLI 设计可能就这样)
4. **库依赖全局状态**(配置文件、单例 client)— 工具里每次新建,别共享
