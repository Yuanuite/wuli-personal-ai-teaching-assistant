# mcp-vibe-coding skill

一个给 AI 编码 agent(Claude Code / Cursor / Copilot 等)用的 skill,
帮用户用**自然语言提示词**实现 MCP server。

不是替代 MiXer AI 控制台的 4 步 wizard,而是补充:
- 已有 Python 库 / CLI,要包成 MCP → vibe coding
- 从零写新 MCP server → vibe coding
- 修改 MiXer AI 已生成的代码 → vibe coding
- (已有 HTTP API 也能 vibe coding,但通常 MiXer AI wizard 更高效)

## 安装

把整个目录复制到你的 Claude Code skills 路径下:

```bash
# 一次性安装
cp -r skills/mcp-vibe-coding ~/.claude/skills/

# 或者用软链(改了项目代码即时生效)
ln -s "$(pwd)/skills/mcp-vibe-coding" ~/.claude/skills/mcp-vibe-coding
```

重启 Claude Code,这个 skill 就会被自动发现。触发方式:

- 直接描述需求:"帮我用 FastMCP 写一个 GitHub 仓库查询的 MCP server"
- 或者输入 `/mcp-vibe-coding` 显式调用

## 使用流程

1. **agent 加载 skill 后**,会显示 4 种场景让你选
2. **选好后,agent 打开对应模板**(`templates/0X-*.md`)
3. **你把模板里的 `[占位符]` 替换成你的实际信息**
4. **把模板整段复制给 agent** —— agent 按模板里的约束实现
5. **改完后**,agent 跑自带的验证(语法检查 / 启动 / curl)
6. **推荐再用 MiXer AI 验证** —— 把生成的 `mcp_server.py` 贴进 MiXer AI 控制台,
   wizard 会重抽 tools.json,对比你的版本,差异大就改提示词重跑

## 目录结构

```
mcp-vibe-coding/
├── SKILL.md                              # 主入口
├── README.md                             # 本文件
└── templates/
    ├── 01-existing-http-api.md           # 场景 1:已有 HTTP API
    ├── 02-python-library-cli.md          # 场景 2:Python 库 / CLI
    ├── 03-from-scratch.md                # 场景 3:从零写新 server
    └── 04-modify-mixer-generated.md      # 场景 4:改 MiXer AI 生成的代码
```

## 适用场景速查

| 我想... | 用哪个模板 |
|---------|----------|
| 把我的 FastAPI 项目暴露成 MCP | 01 |
| 把我的 Python 库包成 MCP | 02 |
| 把我的 CLI 工具(`my-tool xxx`)包成 MCP | 02 |
| 做一个全新的 MCP server,功能是 X | 03 |
| 我已经在 MiXer AI 生成过代码,想加新工具 | 04 |
| 我在 MiXer AI 生成过代码,想改鉴权方式 | 04 |

## 自定义

模板文件是 Markdown,你可以直接编辑加自己常用的"片段":

- 项目特定的鉴权模式(比如公司 OAuth)
- 内部的命名规范
- 部署目标(K8s / Cloud Run / Lambda)

改完 commit 回这个仓库,其他人也能复用。

## 反馈

模板写得不清楚 / 缺了某个常见场景 → 在项目 issue 里提。
