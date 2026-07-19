# 悟理 Agent Gateway

## 目的

教师工作台不再直接拼接 Codex、Claude 或模型 API 参数。所有“生成解析、按意见返修、生成/修正交互可视化”任务先进入本机 Agent Gateway，再由 Gateway 选择 provider。

```text
教师工作台 → 持久化后台作业 → Agent Gateway → provider adapter
                                      ├─ JSON command adapter
                                      ├─ Codex CLI
                                      ├─ Claude Code CLI
                                      └─ OpenAI-compatible API
```

Gateway 只负责 provider 探测、隔离执行、候选文件运输、失败降级和任务状态，不拥有题干、答案、物理模型的教学语义，也不能执行任何教师批准、交付或公开发布动作。

## 为什么不是直接调用某一家 API

答案返修可以由只返回结构化文件的 API 完成；可视化建模通常还需要读取 Skill、运行本地验证器和理解文件结构。统一 Gateway 让两种运行方式共享同一个任务契约：API 是结构化 provider，Codex/Claude 是具备文件工具的 provider，人工请求文件是最终兜底。

升级某个 CLI 或替换模型时，只改对应 adapter，不改教师工作台的生命周期代码。

## 执行与安全边界

每次任务按以下顺序执行：

1. 页面提交任务并立即获得 `202` 与 job ID；后台作业写入 `student-error-library/.cache/agent-jobs/`。
2. Gateway 按任务 `input_paths` 白名单，把必需的普通文件复制到系统临时目录中的隔离候选区；不会整目录复制条目，也不会跟随符号链接。
3. 原始题图、流程/批准记录、发布草稿和无关内部文件不会放入 Agent 可见候选区；只提供教师已经复核的题干、当前答案/模型、必要元数据和只读规则副本。
4. provider 只能生成任务声明的白名单文件。批准记录、流程记录、原始题图和其他条目均不在写集合中。
5. 候选内容通过答案结构或物理模型验证，并确认 canonical 条目在任务期间未变化后，才在单题事务锁内执行带回滚的白名单批量提升；这不宣称文件系统支持目录级原子事务。
6. 生命周期总控重建索引或确定性仿真，并把结果送回教师复核；Agent 不能自行批准。

provider 在没有产生候选修改前失败时，Gateway 才会尝试下一个 provider。一旦出现候选修改、越权写入或领域校验失败，任务立即停止，防止两个模型叠加修改。服务器重启时，未完成作业会被标记为失败并提示重新提交，不猜测任务已经成功。

同一个知识库只允许一个教师工作台服务持有 OS 文件锁；同题事务锁覆盖同步页面写入、canonical 摘要复查、候选提升和生命周期后处理。服务停止时会等待已经运行的 Agent 作业安全结束后再释放实例锁，不让旧 worker 与新服务同时提升。跨题推理仍可并行，知识索引写入由库级锁串行化。

本机 CLI 只表示“本地启动进程”，不代表推理数据一定留在本机。`GET /api/health` 会分别报告 `execution_locality` 与 `data_locality`；实际数据边界仍由所选模型服务决定。

## Provider 选择

默认 `auto` 顺序为：

```text
JSON adapter → OpenAI-compatible API → 兼容旧命令 → Codex CLI → Claude Code CLI
```

结构化 provider 只有在显式配置且通过隐私门禁时才会出现在前列；未配置 API 不影响 CLI 回退。

可显式选择：

```bash
export TEACHER_CONSOLE_AGENT_PROVIDER=codex
# auto | adapter | legacy-command | codex | claude | openai-compatible
```

Gateway 会探测 CLI 版本及所需参数。`codex` 当前使用 `exec --sandbox workspace-write --ephemeral --ignore-user-config --ignore-rules`，避免个人模型配置、插件或旧规则污染后台任务；`claude` 使用 `--safe-mode --strict-mcp-config`。Gateway 不再使用已经删除的 Codex `--ask-for-approval` 参数，并始终关闭子进程 stdin，避免 CLI 等待终端追加输入。CLI 或适配器在修改前启动失败时，`auto` 可以安全降级。

版本/help 探测只证明命令兼容，不证明认证、模型和网络可用。`POST /api/agent/providers/probe` 会对一个 provider 执行不含学生数据、不可读写文件的主动连通探测；失败 provider 进入默认 5 分钟的内存熔断期，后续任务跳过它。实际任务的非零退出、超时或适配器协议错误也会触发熔断，成功调用会清除。单个 provider 默认最多运行 300 秒，可用 `TEACHER_CONSOLE_AGENT_ATTEMPT_TIMEOUT_SECONDS` 调整；熔断时间可用 `TEACHER_CONSOLE_AGENT_FAILURE_COOLDOWN_SECONDS` 调整。

## 推荐：JSON command adapter

长期接入自有模型、DeepSeek 服务或另一种 Agent Runtime 时，优先实现 JSON adapter，而不是把 prompt 放进 shell 参数：

```bash
export TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND="/absolute/path/to/agent-adapter"
export TEACHER_CONSOLE_AGENT_PROVIDER=adapter
```

Gateway 通过 stdin 发送一个 JSON 对象，核心字段包括：

```json
{
  "schema_version": 1,
  "id": "task-id",
  "kind": "answer.revise",
  "routing_tier": "economy",
  "entry_id": "entry-id",
  "entry_dir": "/temporary/staging/entry-id",
  "prompt": "教师意见和任务约束",
  "input_paths": ["problem.md", "record.json", "student-solution.md", "teacher-solution.md"],
  "allowed_paths": ["student-solution.md", "teacher-solution.md", "solution.md", "assets/**"],
  "denied_paths": ["assets/original.png"],
  "requires_change": true,
  "allow_remote": false
}
```

adapter 的 stdout 只能返回一个 JSON 对象，诊断写 stderr：

```json
{
  "status": "completed",
  "message": "已完成候选解析",
  "model": "provider-model-name",
  "model_tier": "economy",
  "requested_tier": "economy",
  "usage": {"prompt_tokens": 1200, "completion_tokens": 600, "total_tokens": 1800},
  "files": [
    {"path": "student-solution.md", "content": "完整 Markdown"},
    {"path": "teacher-solution.md", "content": "完整 Markdown"},
    {"path": "solution.md", "content": "与教师版一致的完整 Markdown"}
  ]
}
```

不具备任务所需能力时返回 `{"status":"unsupported","message":"原因","files":[]}`。不得返回 diff、绝对路径、批准记录或交付命令。

`TEACHER_CONSOLE_AGENT_COMMAND` 旧模板仍兼容，但 prompt 可能出现在进程参数中，且无法提供结构化能力声明；新接入不要继续采用它。

provider 子进程不会继承教师服务的完整环境。Gateway 只传基础运行变量及当前 provider 的认证变量；自定义 adapter 如确需额外变量，使用逗号分隔的 `TEACHER_CONSOLE_AGENT_ENV_ALLOWLIST` 显式加入，禁止把密钥写进命令模板或任务 JSON。

输入白名单描述 Gateway 主动提供的内容。结构化 JSON/API provider 没有本地文件工具，因此形成最强的数据披露边界；Codex/Claude CLI 的额外只读能力仍取决于各自运行时沙箱，页面据此把数据位置标为 `provider-dependent`。处理高敏材料时优先使用结构化 adapter，不把“临时工作目录”误当作操作系统级密封容器。

## OpenAI-compatible provider

项目提供标准库实现的结构化 `/chat/completions` adapter：

```bash
export TEACHER_CONSOLE_AGENT_API_BASE_URL="http://127.0.0.1:PORT/v1"
export TEACHER_CONSOLE_AGENT_API_MODEL="MODEL_NAME"
export TEACHER_CONSOLE_AGENT_API_ECONOMY_MODEL="CHEAPER_MODEL_NAME"   # 可选
export TEACHER_CONSOLE_AGENT_API_EXPERT_MODEL="STRONGER_MODEL_NAME"   # 可选
export TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS=300
export TEACHER_CONSOLE_AGENT_PROVIDER=openai-compatible
```

页面提供 `auto`、`economy`、`expert` 三种任务档位。`auto` 对解析与答案返修使用标准模型，对 `visualization.model` 在配置存在时使用深度模型；显式档位缺少对应模型时使用标准模型，并在作业结果记录 `routing_notice`。档位只影响 provider 模型与最小上下文，不改变范围校验、教师批准或生命周期门禁。使用更强模型重跑必须创建新作业，不在已经产生候选修改的任务上叠加重试。

本机回环服务不需要远程授权。非回环服务必须同时满足：

1. `student-error-library/config.json` 中 `privacy.allow_remote_agent=true`；
2. 进程环境中 `TEACHER_CONSOLE_AGENT_ALLOW_REMOTE=true`。

远程密钥只放在 `TEACHER_CONSOLE_AGENT_API_KEY` 环境变量中，禁止写进项目、任务记录或日志。`TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS` 控制该 HTTP 请求的超时，默认 300 秒；它与 Gateway 对整个 provider 尝试设置的 `TEACHER_CONSOLE_AGENT_ATTEMPT_TIMEOUT_SECONDS` 是两层限制。该 provider 只接收已复核文本和规则上下文，不接收原始题图。

## 作业状态与恢复

作业状态为：

```text
queued → running → completed
                 ↘ failed
```

页面可离开当前 Tab；任务完成后自动刷新。刷新浏览器时，条目详情会返回最近作业并恢复轮询。相同条目同一时刻只允许一个 Agent 修改任务，不同条目可以并行。作业记录显示请求档位、实际模型档位、模型名和 provider 返回的 token 用量；CLI 无法提供这些数据时字段为空，不估算。作业目录权限收紧为 `0700`、记录文件为 `0600`；公开作业 API 不返回 provider 的原始 stdout/stderr。Gateway 作业状态与题目 `pipeline.json` 状态相互独立。

常用检查：

```bash
curl -fsS http://127.0.0.1:8787/api/health
curl -fsS http://127.0.0.1:8787/api/agent/providers
curl -fsS -H 'X-Teacher-Console: 1' -H 'Content-Type: application/json' \
  -d '{"provider":"codex","timeout_seconds":120}' \
  http://127.0.0.1:8787/api/agent/providers/probe
curl -fsS "http://127.0.0.1:8787/api/jobs?entry_id=ENTRY_ID"
```

## 故障判断

| 现象 | 含义与处理 |
|---|---|
| provider 显示不可用 | 查看版本、缺失参数、主动探测或最近一次熔断原因；修复后重试 probe |
| 第一 provider 失败后切到第二个 | 启动失败发生在任何候选修改之前，属于安全降级 |
| “候选未通过范围或内容校验” | canonical 未提升；查看 `unauthorized_changes` 或 `validation_errors` |
| 作业因服务重启失败 | 重新提交；系统不会复用状态不明的旧进程 |
| 远程 API 被隐私门禁拒绝 | 获得明确授权后同时开启项目与环境门禁，或改用本机 provider |
| 页面任务一直运行 | 查看 job JSON 和 provider stderr；单个 provider 默认最多 5 分钟，必要时调整专用超时变量 |
