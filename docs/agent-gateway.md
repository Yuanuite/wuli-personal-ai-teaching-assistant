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

标准解析和答案返修可以由结构化输出完成；可视化建模通常还需要读取 Skill、运行本地验证器和理解文件结构。统一 Gateway 让两种运行方式共享同一个任务契约：API 是结构化 provider，Codex/Claude 可按任务进入无工具结构化模式或文件 Agent 模式，人工请求文件是最终兜底。

升级某个 CLI 或替换模型时，只改对应 adapter，不改教师工作台的生命周期代码。

## 执行与安全边界

每次任务按以下顺序执行：

1. 页面提交任务并立即获得 `202` 与 job ID；后台作业写入 `student-error-library/.cache/agent-jobs/`。
2. Gateway 按任务 `input_paths` 白名单，把必需的普通文件复制到系统临时目录中的隔离候选区；不会整目录复制条目，也不会跟随符号链接。
3. 原始题图、流程/批准记录、发布草稿和无关内部文件不会放入 Agent 可见候选区；只提供教师已经复核的题干、当前答案/模型、必要元数据和只读规则副本。答案返修和可视化建模还可获得一份经过隐私裁剪和字符预算限制的 `.agent-context/knowledge-evidence.json`；缺失时任务正常继续。
4. provider 只能生成任务声明的白名单文件。批准记录、流程记录、原始题图和其他条目均不在写集合中。
5. 候选内容通过答案结构或物理模型验证，并确认 canonical 条目在任务期间未变化后，才在单题事务锁内执行带回滚的白名单批量提升；这不宣称文件系统支持目录级原子事务。
6. 生命周期总控重建索引或确定性仿真，并把结果送回教师复核；Agent 不能自行批准。

`analysis.generate` 采用专门的 `wuli.analysis.v1` 契约：模型只返回一份学生版正文、教师审计增量、五类教学元数据和 2–6 个图示节点。Gateway 在无文件工具模式下取得该对象；`analysis_artifacts.py` 再确定性合成教师版与兼容版、只合并允许的记录字段，并生成安全的 `assets/explanatory.svg`。模型不再重复生成三份 Markdown，也不再负责文件写入或 SVG 语法。

结构化结果在确定性落盘前写入本地 `.cache/analysis-checkpoints/`。检查点绑定题干、记录、教师指令、模型和档位摘要；上述任一输入变化都会使旧检查点失效。若 provider 已成功而后续落盘、校验或服务中断，下一次提交优先从检查点恢复，不再次调用模型；正式提升成功后立即删除检查点。`analysis-request.json.stages` 保存生成、答案落盘、图示落盘、校验和提升各阶段状态，以及可用的耗时和 token 用量。

provider 在没有产生候选修改前快速失败时，Gateway 才会尝试下一个 provider。一旦出现候选修改、越权写入或领域校验失败，本次 Gateway 调用立即停止，防止两个模型叠加修改。Provider 超时、已报告 token 用量或运行超过成本阈值的失败也会停止后续降级，避免一次失败叠加多次完整推理；整条 Gateway 调用同时受任务总时间预算约束，而不是让每个 provider 分别用满总预算。默认成本阈值为 30 秒，可通过 `TEACHER_CONSOLE_AGENT_COSTLY_FAILOVER_SECONDS` 调整。

上层失败排障层只对 `candidate_validation_failed`、`output_truncated`、`candidate_no_change` 考虑创建一个全新的隔离候选区，并携带脱敏校验证据重试一次；如果首轮已报告 token 用量或单次 provider 运行达到 30 秒，则只保留诊断、不再自动执行第二次完整推理。越权、canonical 冲突、provider 故障和构建失败也不自动重试。服务器重启时，未完成作业会被标记为失败并提示重新提交，不猜测任务已经成功。

失败作业同时记录稳定的 `failure_type`，供批量基准、Candidate Archive、Knowledge Store 和后续 Evolve 策略使用；教师可见的中文详情仍保留在 `message`、`validation_errors` 和 `unauthorized_changes` 中。当前低基数分类包括：

| failure_type | 含义 |
|---|---|
| `provider_unavailable` | 没有可用 provider 或隐私门禁排除了全部候选 |
| `provider_timeout` / `provider_rate_limited` / `provider_budget_exceeded` / `provider_execution_failed` | provider 超时、限流、超过单次费用上限或非零退出 |
| `adapter_protocol_error` | JSON adapter 输出无法解析或不符合协议 |
| `candidate_no_change` | 任务要求修改，但 provider 未形成候选文件变化 |
| `output_truncated` | 输出截断导致候选或校验不完整 |
| `candidate_validation_failed` | 候选未通过答案/物理模型领域校验 |
| `unauthorized_change` / `canonical_changed` | 候选越权，或任务期间 canonical 条目发生变化 |
| `simulation_build_failed` | 可视化模型已生成，但确定性 HTML 构建或运行检查失败 |
| `worker_interrupted` / `task_exception` | 服务重启中断，或生命周期回调异常 |

Benchmark 对旧作业仍保留文本推断兼容；新作业优先使用 Gateway/调度器在失败发生时写入的结构化分类。`failure_type` 只描述失败阶段；是否执行一次性纠正由 `failure_intelligence.py` 的固定策略决定，结果写入 `failure_repair`。完整边界见 [`failure-intelligence.md`](failure-intelligence.md)。

同一个知识库只允许一个教师工作台服务持有 OS 文件锁；同题事务锁覆盖同步页面写入、canonical 摘要复查、候选提升和生命周期后处理。服务停止时会等待已经运行的 Agent 作业安全结束后再释放实例锁，不让旧 worker 与新服务同时提升。

后台作业由 Agent Scheduler 按优先级和任务类型限流，而不是全局串行：`source.clean` 是多题上传后的低风险、单条目任务，默认最多 4 道题并发；`analysis.generate`、`answer.revise` 和 `visualization.model` 默认也各自最多 4 个并发，但全局默认只同时运行 6 个 Agent 作业。同一条目始终只能有一个 Agent 作业处于 queued/running。worker 只领取当前可运行任务，因此等待同类并发额度的作业不会占住 worker 阻塞其他类型任务。默认配置写在 `student-error-library/config/agent-scheduler.json`；也可用环境变量临时覆盖：

```bash
export TEACHER_CONSOLE_AGENT_MAX_WORKERS=6
export TEACHER_CONSOLE_SOURCE_CLEAN_CONCURRENCY=4
export TEACHER_CONSOLE_ANALYSIS_CONCURRENCY=4
export TEACHER_CONSOLE_ANSWER_REVISE_CONCURRENCY=4
export TEACHER_CONSOLE_VISUALIZATION_MODEL_CONCURRENCY=4
export TEACHER_CONSOLE_SOURCE_CLEAN_INDEX_DEBOUNCE_SECONDS=2
```

`source.clean` 未显式选择档位时默认走 `economy`，因为它只清理 OCR 题干草稿，不做深度解题或物理建模。该任务成功后不立即对每题抢占全库索引锁，而是防抖触发一次 `kb.rebuild_index()`；Evaluator 和 Candidate Archive 仍在每题作业结束时记录本题结果。跨题推理可以并行，知识索引写入仍由库级锁串行化。

调度器详细配置见 [`agent-scheduler.md`](agent-scheduler.md)。

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

Gateway 会探测 CLI 版本及所需参数。普通文件任务中，`codex` 使用 `exec --sandbox workspace-write --ephemeral --ignore-user-config --ignore-rules`，`claude` 使用 `--safe-mode --strict-mcp-config` 并只开放 `Read/Write/Edit/Glob/Grep`。结构化解析中，Codex 改用只读沙箱和 `--output-schema`，Claude 改用 `--json-schema --output-format json --tools "" --bare`，授权文本由 Gateway 按 60,000 字符总上限内联，不给模型文件循环。Claude 仍以 `--max-budget-usd` 设置单次调用费用上限，默认 0.50 美元，可通过 `TEACHER_CONSOLE_CLAUDE_MAX_BUDGET_USD` 调整。连通探测不开放工具，文件能力探测只开放 `Write`。Gateway 不再使用已经删除的 Codex `--ask-for-approval` 参数，并始终关闭子进程 stdin，避免 CLI 等待终端追加输入。CLI 或适配器在修改前快速失败时，`auto` 可以安全降级。

版本/help 探测只证明命令兼容，不证明认证、模型、网络和工具能力可用。`POST /api/agent/providers/probe` 默认执行不含学生数据、不可读写文件的主动连通探测；传入 `require_file_tools=true` 时，会在一次性空目录内要求 CLI 只写一个固定探测文件，并拒绝任何额外文件变化。模型注册表的 Codex/Claude 测试默认使用这一文件能力探测，避免把“能回复文本”误判为“能完成 Agent 文件任务”。探测结果在 health 端点中报告，但不再将 provider 整体标记为不可用。冷却已从 provider 级下移到任务级（`AgentJobManager`）：任务失败后，同一 (题目, 操作类型) 组合在默认 300 秒内不可重试，可通过 `TEACHER_CONSOLE_AGENT_FAILURE_COOLDOWN_SECONDS` 调整；但同一 provider 可继续服务其他题目。

## 运行时、模型与工具不是同一层

`provider` 是执行请求的运行时；`model` 是该运行时连接的上游模型；`kind` 决定本次任务是否开放工具。三者不能互相推断：

| 任务 `kind` | 输出方式 | Claude/Codex 本地工具 | OpenAI-compatible / LiteLLM |
|---|---|---|---|
| `source.clean` | 文件候选 | 受限文件工具；不自动加载完整 Skill | 返回允许文件的候选内容；不能运行本地工具 |
| `analysis.generate` | `wuli.analysis.v1` 结构化对象 | **禁用工具**；规则与题干按预算内联 | 一次结构化请求 |
| `answer.revise` | 文件候选 | 受限文件工具；expert 档可读总控 Skill | 返回允许文件的候选内容；不能运行本地工具 |
| `visualization.model` | `physics-model.json` 候选 | 受限文件工具，可读仿真 Skill 与 schema | 可生成 JSON 候选；不能自行运行构建器或浏览器 |

确定性落盘、领域校验、仿真构建和浏览器检查始终由悟理后端负责。因此，API-only 模型可以参与所有声明支持的任务，但“支持任务”不等于“能调用本地 Skill”；它依赖 Gateway 注入的最小上下文和后续确定性工具链。设置页中的模型能力声明只是路由白名单，不会给模型新增工具能力。

Claude/Codex 的文件能力探测只证明该 CLI 在一次性目录能按约束写入固定文件，不证明特定模型适合所有任务，也不证明上游厂商身份。完整解析会故意关闭工具，因此“文件能力测试通过”与 `analysis.generate` 的无工具执行并不矛盾。

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

动态检索证据通过任务内 `context_payloads` 在 Gateway 内存中传入，并且只允许物化到隔离候选区的 `.agent-context/` 下。Gateway 在启动 provider 前写入上下文文件，并从 JSON adapter 的任务 stdin 元数据中移除原始 payload，避免重复传输；provider 仍通过候选区上下文读取授权后的证据。作业公开结果只保留 `evidence_context` 的状态和引用数量，不回传具体片段。

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

页面提供 `auto`、`economy`、`expert` 三种任务档位。`auto` 对解析与答案返修使用标准模型，对 `visualization.model` 在配置存在时使用深度模型；显式档位缺少对应模型时使用标准模型，并在作业结果记录 `routing_notice`。档位只影响 provider 模型与最小上下文，不改变范围校验、教师批准或生命周期门禁。使用更强模型重跑必须创建新作业；系统的一次性内容纠正也会创建新的隔离候选，不在失败候选目录上继续叠加修改。

## 本地模型注册表

教师端右上角“设置”界面读写 `student-error-library/config/model-registry.json`。该文件是本地私有配置，可从 `docs/model-registry.example.json` 复制：

```json
{
  "schema_version": 1,
  "models": [
    {
      "id": "wuli-expert",
      "display_name": "悟理深度模型（LiteLLM）",
      "provider": "openai-compatible",
      "base_url": "http://127.0.0.1:4000/v1",
      "model": "wuli-expert",
      "api_key_env": "LITELLM_API_KEY",
      "remote": false,
      "model_tier": "expert",
      "capabilities": ["analysis.generate", "answer.revise", "visualization.model"],
      "tags": ["LiteLLM", "深度", "网关"]
    }
  ]
}
```

顶部“模式”包含“自动 / 经济 / 深度 / 自定义”。自动、经济和深度使用注册表 `defaults` 中的默认模型映射；自动模式按任务类型读取 `analysis.generate`、`answer.revise` 或 `visualization.model`，经济/深度模式读取 `economy` 或 `expert`。只有选择“自定义”时，页面才展开真实模型下拉框并在任务请求中携带具体 `model_id`。`model_id=auto` 保持原有 provider 顺序和档位路由；指定注册模型时，Gateway 会把该模型配置作为本次任务的 provider/model 覆盖，并继续执行输入白名单、远程隐私门禁、候选范围校验和教师批准流程。

`student-error-library/config/model-registry.json` 是本机私有文件，已被 `.gitignore` 排除。教师端设置页允许为 OpenAI-compatible API 与 Claude Code Agent 模型分别填写地址、真实模型名和 API Key；后端保存到该私有注册表，但 `GET /api/agent/model-registry` 只返回 `api_key_saved/api_key_configured` 状态，不回显明文。OpenAI-compatible 任务把本地 key 注入 `TEACHER_CONSOLE_AGENT_API_KEY`。Claude Code Agent 任务则在本次隔离子进程中覆盖 `ANTHROPIC_BASE_URL`，并把选中条目的 key 同时注入 `ANTHROPIC_AUTH_TOKEN` 与 `ANTHROPIC_API_KEY`，防止本机全局 Claude 配置中的旧后端或旧令牌抢占；模型名通过 `claude --model` 传入。是否读取 Gateway 注入的 Skill、使用文件工具，仍由上表中的任务契约决定。所有明文 key 都会从传给 Agent/adapter 的任务 JSON 中剔除。若不想保存 key，也可继续使用 `api_key_env` 引用当前服务进程环境变量。

这里的 `provider` 表示执行运行时，不等于上游模型厂商：`provider=claude` 表示使用 Claude Code 运行时，它可以连接教师明确配置的兼容后端；在 `analysis.generate` 中它只承载一次无工具的结构化请求，在可视化等任务中才作为文件 Agent。`provider=openai-compatible` 表示一次结构化 `/chat/completions` 请求，不能自行运行本地文件工具。

低成本模型不应只因连通测试通过就声明 `analysis.generate`。先用固定结构化解析样本验证输出完整性、耗时和预算，再把该能力加入模型注册表；未声明能力时，显式选择会在 provider 调用前失败。可把经济模型只用于 `answer.revise`，把完整解析默认指向经过验证的标准或深度模型。

每个注册模型还有独立连通测试状态。设置页的“测试”按钮会先保存当前模型配置，再运行一次不含学生数据的 `gateway.probe`；Claude Code/Codex 会额外验证隔离目录中的受限文件写入能力。通过后写入本地 `probe.status=passed` 与配置摘要。摘要覆盖 provider、地址、模型名、Key 摘要和远程属性；任何一项变化都会使旧通过状态失效。只有当前配置与上次通过测试的摘要一致时，模型才会出现在可用路由里。

### 接入另一种 Agent 工具

优先顺序如下：

1. 只有 OpenAI-compatible 对话接口：直接注册为 `openai-compatible`，接受“无本地工具”的限制。
2. 已有可无头运行、能读写隔离目录的 Agent CLI：为它编写 JSON stdin/stdout adapter，并让 adapter 处理 CLI 方言、认证和结构化结果。
3. 只在人工终端里可用、会等待确认或依赖交互登录的工具：不能直接作为后台 provider；先提供无头模式、确定退出码、stdout JSON 和超时行为。

新 adapter 必须能接收上文任务 JSON，只修改 `allowed_paths`，把诊断写 stderr，把唯一结果 JSON 写 stdout；不得调用批准、交付或发布接口。若工具需要额外环境变量，将变量名加入 `TEACHER_CONSOLE_AGENT_ENV_ALLOWLIST`，不要把值写进命令模板。增加 provider 后同步补充 Gateway 单元测试、设置页显示、`docs/agent-gateway.md`、`docs/operator-runbook.md` 与根 README。

OpenCode、OpenClaw 或其他 Agent 工具不会因为“支持 Skill”就被教师工作台自动发现；自然语言入口可在这些工具中直接使用项目 Skill，但要成为网页后台 provider，仍必须完成上述 adapter 接入。

### 网页运行环境诊断

“添加 Codex 可视化预设”旁的紧凑 Codex 状态框负责 CLI 与网络，不与通用模型注册表混用；只有教师点击时才展开连接设置。配置写入被 Git 忽略的 `student-error-library/config/agent-runtime.json`，可选择自动发现的 Codex、沿用启动环境、直接连接或教师明确确认的本机回环代理。网页拒绝远程代理和带用户名/密码的代理 URL；检测到常见本机端口只显示建议，不会自动启用。

Gateway 在每次任务和主动探测前重新解析该文件，因此保存后无需重启服务。`POST /api/agent/runtime/diagnose` 使用空临时目录执行一次不含学生数据的 Codex 真实推理，并把与当前配置摘要绑定的结果保存到本地；修改 Codex 路径或代理后旧通过状态自动失效。子进程环境允许传递 `HTTP_PROXY`、`HTTPS_PROXY` 与 `ALL_PROXY` 的大小写形式，但不会继承其他无关环境变量。

高级部署可以把多供应商、回退、限流和成本统计交给本机 LiteLLM Proxy，再把 `wuli-economy`、`wuli-standard`、`wuli-expert` 三个稳定别名暴露给悟理。教师端不内置启动 LiteLLM，也不默认创建这些别名；需要时在设置页用“新增模型”手动添加，或复制 `docs/model-registry.example.json`。详见 [litellm-gateway.md](litellm-gateway.md)。

本机回环服务不需要远程授权。非回环服务需要通过项目隐私门禁：`student-error-library/config.json` 中 `privacy.allow_remote_agent=true`。

远程密钥可以放在 `TEACHER_CONSOLE_AGENT_API_KEY` 环境变量中，也可以由教师端设置页写入被忽略的本地注册表；禁止写入示例配置、任务记录、日志、公开站或可提交文件。`TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS` 控制该 HTTP 请求的超时，默认 300 秒；它与 Gateway 对整个 provider 尝试设置的 `TEACHER_CONSOLE_AGENT_ATTEMPT_TIMEOUT_SECONDS` 是两层限制。该 provider 只接收已复核文本和规则上下文，不接收原始题图。

## 作业状态与恢复

作业状态为：

```text
queued → running → completed
                 ↘ failed
```

页面可离开当前 Tab；任务完成后自动刷新。刷新浏览器时，条目详情会返回最新作业并恢复轮询。相同条目同一时刻只允许一个 Agent 修改任务，不同条目可以并行。作业记录显示请求档位、实际模型档位、模型名和 provider 返回的 token 用量；CLI 无法提供这些数据时字段为空，不估算。作业目录权限收紧为 `0700`、记录文件为 `0600`；公开作业 API 不返回 provider 的原始 stdout/stderr。Gateway 作业状态与题目 `pipeline.json` 状态相互独立。

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
| provider 显示不可用 | 查看版本、缺失参数、主动探测或上次熔断原因；修复后重试 probe |
| 页面配置的 Claude/DeepSeek 与终端 `claude` 使用不同 Key | 这是预期隔离：页面只覆盖该次 Gateway 子进程，不改 `~/.claude/settings.json`；分别检查设置页模型行和终端环境 |
| 测试通过，但任务提示模型不支持 | probe 只验证连通/CLI 文件能力；再检查模型 `capabilities` 是否包含当前 `kind`，以及默认模式是否指向该模型 |
| 测试通过，但怀疑走错上游模型 | 检查模型行的 provider、API 地址和真实模型名；probe 不能证明厂商身份，最终以作业记录的 provider/model 和上游服务日志为准 |
| API 模型无法运行 Skill 或本地脚本 | 属于 OpenAI-compatible 路线的设计限制；改用 Claude/Codex 文件 Agent，或为目标工具实现 JSON adapter |
| 第一 provider 失败后切到第二个 | 启动失败发生在任何候选修改之前，属于安全降级 |
| “候选未通过范围或内容校验” | canonical 未提升；查看 `unauthorized_changes` 或 `validation_errors` |
| 作业因服务重启失败 | 重新提交；系统不会复用状态不明的旧进程 |
| 远程 API 被隐私门禁拒绝 | 在 `config.json` 中设置 `privacy.allow_remote_agent=true`，或改用本机 provider |
| 页面任务一直运行 | 查看 job JSON 和 provider stderr；单个 provider 默认最多 5 分钟，必要时调整专用超时变量 |
