# 悟理运行与排障手册

## 正常入口

把图片或 PDF 放入 `error-collection/`，然后对 Agent 说“处理现在新上传的题目”。除原图确实歧义、远程上传需要授权或必要依赖没有安全降级外，流程不应在中间要求例行确认。

也可以启动教师工作台：

```bash
python3 teacher-console/server.py
```

浏览器打开 <http://127.0.0.1:8787/>。它是本地服务而不是公网网站；终端关闭后服务也会停止。若 8787 被占用，可使用 `--port 8788` 并打开对应端口。服务硬性拒绝非回环地址；学生端公开访问必须走独立静态站，不能把教师端开放到局域网或公网。

如需将控制台日志保存到文件，可指定 `--log-file`：

```bash
python3 teacher-console/server.py --log-file /tmp/teacher-console.log
```

日志格式为 `时间 [级别] [trace_id] 消息`，其中 `trace_id` 在每个管道阶段入口自动生成（12 位 hex），可用于串联一次"上传→交付"全流程中的所有相关日志行。所有核心模块（server、agent_gateway、agent_jobs、failure_intelligence）已接入同一日志系统。

需要用其他本地页面或脚本调用工作台时，接口清单、`X-Teacher-Console` 写操作请求头和流程顺序见 [`teacher-console-api.md`](teacher-console-api.md)。学生静态站不调用这些接口。

“生成解析”、答案返修和可视化建模统一进入本机 Agent Gateway 后台队列。Gateway 优先使用 JSON adapter 和经授权的 OpenAI-compatible API，其次兼容旧命令、Codex 和 Claude Code。页面右上角可选“自动 / 经济 / 深度 / 自定义”：简单返修可主动选经济档，自动档按设置页的默认模型策略匹配任务，自定义档才展开具体模型下拉框。页面会显示请求档位、实际模型、token 用量、provider、排队/运行/完成状态和失败原因。按钮不会绕过来源复核，也不会替教师批准答案；候选未通过范围或内容校验时 canonical 条目保持原样。

多题上传后的 `source.clean` 是后端批处理优化点：未显式选择时默认走 `economy`，并允许不同题目有限并发；解析、返修和可视化也可跨题有限并发，但同一题永远只能有一个 Agent 作业运行。默认调度配置在 `student-error-library/config/agent-scheduler.json`，服务启动时读取；环境变量可临时覆盖：

```bash
export TEACHER_CONSOLE_AGENT_MAX_WORKERS=6
export TEACHER_CONSOLE_SOURCE_CLEAN_CONCURRENCY=4
export TEACHER_CONSOLE_ANALYSIS_CONCURRENCY=4
export TEACHER_CONSOLE_ANSWER_REVISE_CONCURRENCY=4
export TEACHER_CONSOLE_VISUALIZATION_MODEL_CONCURRENCY=4
export TEACHER_CONSOLE_SOURCE_CLEAN_INDEX_DEBOUNCE_SECONDS=2
```

Scheduler 会按优先级领取当前可运行任务，等待同类并发额度的作业不会占住 worker 阻塞其他类型任务。`source.clean` 成功后会防抖刷新索引，避免 8 道题同时完成时每题都重建全库；本题 Evaluator 与 Candidate Archive 记录不等待批量索引完成。详细策略见 [`agent-scheduler.md`](agent-scheduler.md)。

复盘批量录入耗时：

```bash
python3 teacher-console/scripts/agent_batch_benchmark.py \
  --library student-error-library --kind source.clean --format markdown
```

输出包含排队等待、运行耗时、P50/P90、最大并发、provider/model 分布和失败类型；这是调度器、模型路由与后续 Evolve 调参的基准。

确认一次基准有代表性后，可显式沉淀到本地记忆：

```bash
python3 teacher-console/scripts/agent_batch_benchmark.py \
  --library student-error-library --kind source.clean --format markdown --record
```

该命令追加全库级 `scheduler.benchmark` 事件并刷新 Knowledge Store；不写入单题目录，不进入学生端公开内容。

观察 RAG 是否与更好的 Agent/教师闭环结果相关：

```bash
python3 teacher-console/scripts/rag_effectiveness_report.py \
  --library student-error-library --format markdown
```

默认只读。仅在样本有代表性时加 `--record`，将报告保存为全库级 `evolve.observation.rag`。`comparison_ready=false` 表示同一任务类型的检索组和历史无检索组尚未分别达到最小样本量，不能据此调整策略。具体门槛见 [`evolve-roadmap.md`](evolve-roadmap.md)。

统一核心求解是当前生产默认。活动配置为
`student-error-library/config/analysis-production-routing.json`：

- `mode: core-first`：一次核心求解 + 确定性 Core Gate/教学渲染，当前 90 秒硬超时；
- `mode: legacy-adaptive`：应急回滚到旧 W2/W3 自适应路由。

核心失败不会自动再跑 W2，也不会因静态图未生成而把正确答案整体回滚。竞赛题调用方
必须传 `method_profile=olympiad_official`；普通课堂默认 `high_school_standard`。

旧 W3 上线验收记录与不可倒置顺序见
[`rag-completion-work-tree.md`](rag-completion-work-tree.md)。W4 新鲜 holdout、生产
灰度与补充官方竞赛题复核均已完成；`wuli-analysis-adaptive-v1` 现仅为回滚解析路由。
复杂题进入 W3，低结构风险题保持 W2，W3 门禁失败时自动回退 W2。旧题 replay 仍
只能诊断，不能作为今后的策略变更证据。

旧路由的二级开关位于 `student-error-library/config/w3-production-routing.json`：

- `mode: default`：复杂题 W3、低风险题 W2，并保留自动回退；
- `mode: off`：紧急回滚，全部由 W2 接管；
- `mode: gray`：只允许 `gray_entry_ids` 内的复杂题进入 W3，用于新策略再次灰度。

服务在每次解析任务开始时重新读取配置。回滚后应用同一复杂题探针应记录
`route=w2`、`reason=w3-production-disabled`；恢复后应记录 `route=w3`、
`reason=deterministic-complexity-screen`。切换不改变 provider 配置、候选隔离或答案
复核门禁。

W3R 是 W3 成功后的独立渲染选择器，活动配置路径为
`student-error-library/config/w3r-production-routing.json`；文件不存在或配置非法时
等价于 `mode: off`。字段模板见 `docs/w3r-routing.example.json`。`shadow` 只记录结果，
`gray` 还要求条目进入白名单、至少两道同 Brief 配对题完成教师盲审且六项忠实性硬门禁
全过；`default` 额外要求至少 5 道、12 个目标的新鲜 holdout、教师可读性偏好占优且
修改率不劣化。紧急回滚只把 W3R 配置切为 `off`；W3 结果、Proof Package、trace 和失败
用量均保留，下一次候选物化使用当前确定性 renderer。

Claim Evidence 多批验证默认最大并发为 2。若观察到 Claude 限流、机器资源紧张或需
做串行对照，可在启动教师工作台前设置：

```bash
export TEACHER_CONSOLE_W3_CLAIM_VERIFY_CONCURRENCY=1
```

删除该变量恢复默认 2。允许值只有 1 或 2，其他值在 provider 调用前失败。排障时查看
`w3-shadow-report.json.stages` 的 `batch_index`、`duration_seconds`、
`provider_seconds` 与 `overhead_seconds`；并发总墙钟时间以
`report.claim_evidence_shadow.semantic_audit.duration_seconds` 为准，不能把各批耗时
直接相加当作整题耗时。

盲审包与证据可用以下只读命令重建；私钥应写到临时目录并在 A/B 偏好锁定后才交给审核人：

```bash
python3 -B teacher-console/scripts/w3r_shadow_benchmark.py \
  teacher-console/tests/fixtures/w3r/verified-multi-target.json \
  teacher-console/tests/fixtures/w3r/verified-condition-matrix.json \
  --blind-packet-out /tmp/w3r-packet.json \
  --blind-key-out /tmp/w3r-private-key.json \
  --evidence-out /tmp/w3r-evidence.json
```

断言级正确性证据链的只读诊断命令：

解题 loop 的原子任务、Challenge 回跳、有限联想和熔断边界见
[`解题loop.md`](解题loop.md)；下面命令只读，不会修改 canonical 答案、审批或学生端产物。

```bash
# 同题、同模型、同证据、固定种子的认知环 off/on 消融；不调用外部 provider
python3 -B teacher-console/scripts/correctness_cognitive_loop_ablation.py --markdown

# 旧 W3 实验只读投影；不写条目，不复用旧题宣称生产泛化
python3 -B teacher-console/scripts/correctness_replay_diagnostic.py \
  --experiment student-error-library/evals/w3-shadow-w4-replay-1 --markdown

# 新鲜样本门禁；当前不足时只报告状态，不创建实验目录
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-fresh-1 \
  status --holdout-count 5
```

`correctness_evidence_benchmark.py` 还需要由隔离 claim-evidence E2E 生成的
`claim-evidence-summary.json`，用于联合计算证书覆盖、故障检出、回跳和重复率。完整命令与固定结果
见 [`技术执行计划书.md`](技术执行计划书.md) 和
[`reports/correctness-evidence-metrics-v1.md`](reports/correctness-evidence-metrics-v1.md)。
开发时可为单独进程设置 `TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW=1` 查看私有账本，
但该变量默认关闭、不是生产资格开关，也不会替代教师答案批准。

建立并运行固定检索评测集：

```bash
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library seed --limit 30
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library validate
python3 teacher-console/scripts/retrieval_benchmark.py \
  --library student-error-library run --include-draft --format markdown
```

草稿位于 `student-error-library/evals/retrieval-cases.jsonl`。启动教师端后，点击顶栏“检索评测”：左侧逐条选择查询，右侧会显示带原题图、题干摘要、知识点和错因标签的可勾选题目卡；核对 `query`，勾选所有真正相关的题，再“批准并看下一条”。网页保存和命令行工具共用同一 JSONL，卡片不会展示教师版解析，也不会向学生站发布评测数据。不要批量直接改状态；少于 30 条 approved 时，工具拒绝 `--record` 和检索后端升级结论。格式示例见 [`retrieval-eval.example.jsonl`](retrieval-eval.example.jsonl)。

答案生成或检索逻辑变更后，按三层分别验收，不能用一项指标代替另一项：

1. **生成契约**：验证 `wuli.analysis.v2` 的私有方法自检、学生版固定结构、最短主线、五步上限和超纲方法拒绝；同步运行单元测试与 4 条 E2E。
2. **检索能力**：在同一批 `approved` 固定集上比较改前/改后的 Hit@k、Recall@k、MRR、空结果率和 `teacher_phrase` 分类。Hit@5 达到 1 只表示每个查询至少命中一个相关条目；多相关条目仍可能漏召回，必须继续看 Recall@5。
3. **教学质量**：检索指标提升只证明“更容易找到证据”，不能证明答案因此更短或更正确。需要固定模型、档位、prompt 和题目做成对生成，再比较确定性校验、Evaluator、教师修改量、首轮采用率与最终批准。

每次回归都保留固定集版本、改动说明和完整指标；不要挑选单题或只报告最有利的 k 值。当前题干、当前答案和教师意见始终高于历史 evidence，Knowledge Store 不可用时主任务应 fail-soft。

Evidence 上下文预算预检（只读，不调用 provider，也不修改线上策略）：

```bash
python3 teacher-console/scripts/evidence_budget_benchmark.py \
  --cases teacher-console/tests/fixtures/evidence-budget-eval.example.jsonl
```

预检复用生产环境的确定性裁剪逻辑，报告原始/最终字符数、估算 token、截断状态和内容哈希。示例文件只验证工具链；是否启用更激进的语义压缩，必须使用教师批准的真实样本并满足质量门槛后另行决策。样本不足或门槛未通过时保持当前策略。

查看慢循环证据是否达到策略建议门槛：

```bash
python3 teacher-console/scripts/slow_loop_report.py \
  --library student-error-library --format markdown
```

该命令只读，不调用模型也不修改路由、并发、检索参数或审批。达到 20 个已完成 RAG 任务和 10 个明确教师闭环后，才可显式加 `--record` 保存为 `evolve.observation.slow-loop`；记录仍不等于应用策略。

若当前最新的已记录周报包含策略建议，教师可明确确认其进入离线试验：

```bash
python3 teacher-console/scripts/slow_loop_report.py \
  --library student-error-library --confirm-strategy \
  --reviewer "李老师" --note "仅确认离线验证"
```

确认与该次周报 event ID 绑定；新周报生成后旧确认自动失效。此动作不会修改线上策略。

模型设置位于右上角“设置”。“添加 Codex 可视化预设”旁的 Codex 状态框可自动发现 CLI 和常见本地代理；它默认保持收起，点击后才显示连接设置。选择检测到的代理并点击“检测”后，系统会保存本机配置并执行一次不含学生数据的真实推理；通过后立即生效且无需重启。检测结果与当前配置摘要绑定，修改 Codex 路径或网络方式后必须重测。运行配置只写入被忽略的 `student-error-library/config/agent-runtime.json`，网页仅允许本机回环代理，不会自动启用探测到的端口。

每个 OpenAI-compatible API 或 Claude Code Agent 模型都可填写 API 地址、真实模型名和 API Key；Key 只保存到已被 `.gitignore` 排除的 `student-error-library/config/model-registry.json`，再次打开页面只显示“已保存/已配置”，不回显明文。选择 Claude Code Agent 时，本条目的地址与 Key 会在任务子进程中覆盖本机全局 Claude 配置；文件任务可使用受限 Skill/文件工具，`analysis.generate` 则刻意关闭工具并走结构化输出。选择 OpenAI-compatible API 时始终走结构化请求。每个可选模型必须点击该行“测试”并通过不含学生数据的连通探测后，才会被自动/默认路由调用；修改地址、模型名或 Key 后旧测试自动失效。提交 GitHub 前不要使用 `git add -f student-error-library/config/model-registry.json` 或 `agent-runtime.json`。

> 安全提醒（2026-08-02 审计）：本地 model registry 的历史条目可能含内联 `api_key`
> 字段。运行报告脚本、Agent job 与 Candidate Archive 永不读取/输出这些值；作为预防，
> 维护者应轮换历史密钥，并把新凭据仅保留在环境变量 allowlist
> （`MIMO_API_KEY` / `DEEPSEEK_API_KEY` / `TEACHER_CONSOLE_AGENT_API_KEY` 等）中。

provider 配置、JSON 契约、隔离候选和远程隐私门禁见 [`agent-gateway.md`](agent-gateway.md)。快速检查：

```bash
curl -fsS http://127.0.0.1:8787/api/health
curl -fsS http://127.0.0.1:8787/api/agent/providers
curl -fsS -H 'X-Teacher-Console: 1' -H 'Content-Type: application/json' \
  -d '{"provider":"codex","timeout_seconds":120}' \
  http://127.0.0.1:8787/api/agent/providers/probe
```

测试单个注册模型时优先使用网页设置页的“测试”按钮；脚本调用可用：

```bash
curl -fsS -H 'X-Teacher-Console: 1' -H 'Content-Type: application/json' \
  -d '{"model_id":"<model-id>","timeout_seconds":120}' \
  http://127.0.0.1:8787/api/agent/model-registry/test
```

题干和答案使用本地 Markdown + KaTeX 实时预览。答案编辑后按“保存当前 Markdown”或 `Cmd/Ctrl+S`；保存会撤销旧答案批准并自动重建检索。“交给大模型修改”会把意见限定在当前条目的分层答案和引用解释图内，完成后仍需教师重新批准。标准解析默认不生成交互仿真；答案批准后进入始终保留的“可视化（可选）”页面。没有模型时可直接输入“我想为这道题生成一个可视化结果”或点击“调用 Skill 生成”，之后 Agent 才调用仿真 Skill。新模型会使答案摘要失效，需要先重新复核答案，再批准 iframe 中的动态结果。静态 SVG 仍在解析复核中查看和修改。

左栏来自 `student-error-library/folders/` 的本地同步视图。首次启动会按 `created_at` 建立日期文件夹；网页改名会原子同步本地目录。真正的条目仍固定保存在 `entries/<entry-id>/`，文件夹中使用相对软链接（不支持软链接的平台使用指针），因此改名不会破坏图片、检索或旧 URL。

## 手动命令

以下命令均从项目根目录执行：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library \
  start --input error-collection --subject 高中物理
```

查看全部或单题状态：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library status

python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library status <entry-id>
```

### 质量检查

```bash
# Lint 检查
ruff check teacher-console/ .claude/skills/manage-student-error-library/scripts/

# 自动格式化
ruff format teacher-console/ .claude/skills/manage-student-error-library/scripts/

# 类型检查
python3 -m mypy teacher-console/server.py teacher-console/model_registry.py \
  teacher-console/agent_gateway.py teacher-console/agent_jobs.py

# 运行全部测试
python3 -m pytest teacher-console/tests/ -v --tb=short

# 测试加覆盖率
python3 -m pytest teacher-console/tests/ --cov=teacher-console
```

教师工作台的浏览器/API E2E 使用临时知识库和确定性假 Agent，不接触真实学生数据。教师在工作台真实处理题目时
不会触发、录制或追加 E2E；只有维护者运行以下命令，或 CI 启动作业时才执行测试：

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

默认运行 4 条隔离场景：基础交付、交互可视化生成/复核/交付、公开题图脱敏/预览/本地发布，以及断言级正确性证据影子。
可视化场景实际操作仿真控件，并要求构建、运行时检查和 `interactive_visualization` 评价通过；
公开发布场景实际绘制遮挡、确认原图字节未变，并扫描公开树中的私有引用。基础交付和可视化场景还会核对
`delivery-manifest.json`、`evaluator.py` 领域评价与 `pipeline_quality_eval.py` 的质量、Token 和耗时诊断。报告位于
`test-results/e2e/`。CI 会在 `main` 和 Pull Request 上执行 E2E，但仅允许
`main` 的 push 进入学生端部署任务。`evaluator.py` 和 `pipeline_quality_eval.py` 只负责断言与诊断，
不负责操作 UI/API，也不会把测试数据或教师操作写入正式题库。更细的隔离边界见
[`../teacher-console/e2e/README.md`](../teacher-console/e2e/README.md)。

修改 Agent 输出契约后不能只跑 Gateway 单元测试。`analysis.generate` 的确定性替身分别位于
`teacher-console/tests/fixtures/fake_agent_adapter.py` 和
`teacher-console/e2e/fake_agent_adapter.py`，两者都必须与
`teacher-console/analysis_artifacts.py` 的 `wuli.analysis.v2` 同步。推荐验证顺序：

```bash
python3 -m pytest teacher-console/tests/ -q
npm run test:e2e
```

若 CI 的单元测试通过而 4 条 E2E 同时停在解析阶段，优先查看作业的 `failure_type`、`message`
和保存的临时工作区。`adapter_protocol_error`（例如 `student_solution must be a string`）
通常表示 E2E adapter 仍在返回旧式 `files`，并非生命周期或浏览器本身发生三处独立故障。

在完成原图核对、记录分类、分层答案和解释图后先批准答案：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library approve-answer <entry-id> \
  --reviewer teacher --note "已核对结论、步骤和图像"

# 教师明确请求并由 Agent 创建 physics-model.json 后，执行下面两步。
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library prepare-visualization <entry-id> \
  --runtime-check auto

python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library approve-visualization <entry-id> \
  --reviewer teacher --note "已核对物理阶段、轨迹、文字与交互控件"

python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library finish <entry-id> --simulator auto
```

`--simulator required` 要求必须存在并成功构建仿真；`--simulator skip` 明确不构建仿真。

关键生命周期动作会自动刷新 `evaluation.json`：来源批准、答案保存/批准/返修请求、可视化构建/批准、Agent 解析/返修/可视化任务结束、公开预览/发布和最终 `finish`。交付完成后还会把摘要写入 `delivery-manifest.json`。也可在任意阶段手动生成或只读查看：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library evaluate <entry-id>

python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library evaluate <entry-id> --no-write
```

同一批动作还会追加写入候选档案：

```text
student-error-library/entries/<entry-id>/candidate-archive.jsonl
student-error-library/indexes/candidate-archive.jsonl
```

档案只保存摘要、状态、变更文件、失败原因和 Evaluator 摘要；密钥字段会脱敏，原图和完整候选内容不会被复制。

同一套条目、评价和候选档案会派生到本地 SQLite Knowledge Store：

```text
student-error-library/indexes/wuli-memory.db
```

它会在 `kb.py rebuild`、校验/完成等索引刷新点自动重建；也可手动执行：

```bash
python3 .claude/skills/manage-student-error-library/scripts/knowledge_store.py \
  --library student-error-library rebuild
```

需要给 AI/RAG 提供证据包时：

```bash
python3 .claude/skills/manage-student-error-library/scripts/knowledge_store.py \
  --library student-error-library query "动量守恒 非弹性碰撞" --mode teaching --top-k 5
```

返回结果包含命中文档片段、知识点/错因标签、Evaluator 摘要和近期候选事件。它只辅助检索与审计，不代表教师审批，也不替代 `record.json`、Markdown、`evaluation.json` 和 `candidate-archive.jsonl` 真源。

## 发布只读学生端

网页方式：题目完成“生成最终文件”后，在“交付下载”底部先检查自动建议裁剪；用四边滑杆调整范围，在图上拖拽遮挡姓名、学校、二维码或不应公开的笔迹，并逐页选择是否加入。确认公开题图后再点击“生成公开预览”，逐页检查，最后填写复核人、勾选隐私确认并发布到本地学生站。原图不会被修改，该按钮也不会推送 GitHub。

对应手动命令：

```bash
python3 .claude/skills/manage-student-error-library/scripts/public_site.py init
python3 .claude/skills/manage-student-error-library/scripts/public_site.py prepare <entry-id>
python3 .claude/skills/manage-student-error-library/scripts/public_site.py publish <entry-id> \
  --reviewer teacher --note "已检查公开内容与隐私"
```

若教师已校准客观难度，并且题目此前已经通过隐私复核发布到本地学生端，只同步公开安全的难度摘要：

```bash
python3 .claude/skills/manage-student-error-library/scripts/public_site.py sync-difficulty
```

该命令只更新既有 `catalog.json` 条目的难度总分、等级、公开结论与六维摘要；不会重建或复制题目正文、答案、PDF、题图和仿真，也不会改变原发布时间。未发布、发布记录异常或公开内容缺失的条目不会被静默补发。

本地检查可从项目根目录运行 `python3 -m http.server 8000 --directory student-site`，打开 <http://127.0.0.1:8000/>。确认后，把 `student-site/` 单独初始化为公开 GitHub 仓库并在仓库 Settings → Pages 选择从主分支根目录部署。不要把项目根目录、`student-error-library/` 或 `output/` 一起提交。每次新增公开题目后仍需人工执行 Git 提交与推送。

## DeepSeek 等无视觉模型

显式声明主模型不能识图：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library start --input error-collection \
  --vision-capability unavailable
```

未配置视觉适配器时，打开条目里的 `source-review.md`，对照原图修正 `problem.md`，然后由实际查看原图的人执行：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library approve-source <entry-id> \
  --reviewer teacher --note "已核对公式、方向和全部小问"
```

若已配置视觉边车，在 `student-error-library/config.json` 中设置：

```json
{
  "source_review": {
    "mode": "adapter",
    "adapter_command": "/absolute/path/to/visual-review-wrapper",
    "adapter_locality": "local"
  }
}
```

命令协议见 Skill 的 `references/ocr-adapters.md`。命令中不要写密钥；远程适配器还需单独把 `privacy.allow_remote_visual_review` 设为 `true`，且只能在用户明确授权上传学生图片后设置。

项目已提供 `openai_compatible_vision_adapter.py`。它可以连接本机 OpenAI-compatible 多模态服务；只需通过环境变量提供端点和模型名，不需要在项目中保存密钥。非本机端点具有脚本和生命周期两道独立授权门禁。

### 视觉边车环境变量

> 默认视觉路由已是模型注册表 `defaults.vision`（`source_review.mode=registry`）；
> 下表仅用于显式启用旧边车兼容路径（`--source-review-mode adapter` 或薄 CLI
> `--legacy-adapter`）。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VISUAL_REVIEW_BASE_URL` | 无 | OpenAI-compatible 服务的 `/v1` 基地址 |
| `VISUAL_REVIEW_MODEL` | 无 | 支持图片输入的模型名 |
| `VISUAL_REVIEW_API_KEY` | 无 | 可选密钥，只从安全环境读取 |
| `VISUAL_REVIEW_TIMEOUT_SECONDS` | `120` | HTTP 请求超时秒数 |
| `VISUAL_REVIEW_ALLOW_REMOTE` | 未设置 | 非回环端点必须明确设为 `true` |

本地边车冒烟命令和返回 JSON 规范见 [`visual-review-integration.md`](visual-review-integration.md)。

### 视觉模型探针

视觉路由 fail-closed：候选模型必须声明 `traits.vision=true`，且
`vision_probe.status != failed`。探针使用合成图片（禁止真实学生图）：

```bash
/Users/qingyuan/miniconda3/bin/python3 -B -c "
import model_registry as mr
from visual_extraction import run_vision_probe
from pathlib import Path
cfg = mr.model_config_for_trait('vision', model_id='mimo-v2.5-flash')
result = run_vision_probe(cfg, 'teacher-console/tests/fixtures/visual-routing/clear-question.png',
                          urlopen=__import__('urllib.request', fromlist=['urlopen']).urlopen,
                          allow_remote=True)
mr.record_vision_probe(cfg['id'], result)
print(result['status'], result.get('reason'))
"
```

### 单元测试运行时（固定入口）

从项目根目录任意位置运行工作树验收单测，不依赖偶然进入 `teacher-console/`：

```bash
/Users/qingyuan/miniconda3/bin/python3 -B teacher-console/scripts/run_tests.py \
  --python /Users/qingyuan/miniconda3/bin/python3 --strict
```

`--all` 运行全部单元测试，`--strict` 把 missing/skipped 计为失败（`--exclude`
声明已知排除项、单独报告）；也可追加单个测试文件名。错误解释器会快速报告缺失
依赖（Pillow 等）。

### 显式静态图动作（CLI）

静态图是答案后的显式可选流程，网页按钮与 CLI 共用同一应用服务：

```bash
/Users/qingyuan/miniconda3/bin/python3 -B teacher-console/scripts/entry_action.py \
  build-diagram <entry-id> --library student-error-library --tier economy
```

仅在题干已批准、分层答案存在且 `visual-facts.json` 当前时运行；无图不阻塞答案或
交付。生成/修改 `assets/explanatory.svg` 会通过答案摘要使旧答案批准失效并回到
答案复核（`visualization` 交互仿真仍是独立流程）。

## 中间状态排查

| 状态 | 处理方式 |
|---|---|
| `needs-source-review` | 打开 `source-review.md` 和原图，修正 `problem.md`，由实际看图者执行 `approve-source`；不要手改 JSON 状态 |
| `needs-analysis-and-answer` | 补知识点、可观察错误类型、分层答案与解释图 |
| `needs-answer-review` | 教师核对学生版、教师版和图像；批准当前版本或填写返修意见 |
| `needs-visualization-build` | 从当前 `physics-model.json` 构建预审仿真；不要直接手改 HTML |
| `needs-visualization-review` | 教师在页面核对动态交互仿真；批准当前产物，或对话要求修复模型；静态解释图不进入此状态 |
| `ready-to-finish` | 执行 `finish`，不要手工改成 delivered |
| `delivered` | 以 `delivery.json` 指向的输出目录和 manifest 为准 |

## 依赖与降级

- Agent Gateway：CLI 只在修改前失败时自动切换 provider；内容校验失败、输出截断或未形成修改时，上层会把当前错误与同类历史模式放入新的隔离候选区，最多纠正一次。候选越权、canonical 并发变化、provider 故障或构建失败不会自动重试。实际运行失败会暂时熔断该 provider，单项默认超时 300 秒；主动 probe 不发送学生材料。作业记录位于私有 `.cache/agent-jobs/`，服务重启后重新提交失败任务。Scheduler 默认各任务类型上限为 4、全局上限为 6，同题并发始终阻断。
- 服务单实例：同一知识库不能同时启动两个教师工作台；关闭时若有 Agent 正在运行，终端会等待它安全结束后再释放锁。
- 自定义 provider 环境：默认只传基础运行变量与该 provider 的认证变量；额外变量通过 `TEACHER_CONSOLE_AGENT_ENV_ALLOWLIST` 显式加入。
- 推理位置：Codex/Claude 是本机启动的 CLI，但底层推理可能远程执行；查看 health 中的 `execution_locality` 和 `data_locality`，不要把“本机进程”等同于“数据不离机”。
- 远程模型 API：需要在 `student-error-library/config.json` 中设置 `privacy.allow_remote_agent=true`。密钥可放环境变量，也可通过设置页保存到已忽略的本地模型注册表，禁止写入示例配置、公开站、日志或可提交文件。
- OpenAI-compatible 超时：`TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS` 控制单次 HTTP 请求，默认 300 秒；`TEACHER_CONSOLE_AGENT_ATTEMPT_TIMEOUT_SECONDS` 控制 Gateway 对单个 provider 的总等待时间。
- OCR：优先 Apple Vision 本地识别；失败时保留可复核条目，不丢弃原图。远程 OCR 必须先取得授权。
- 视觉复核：边车失败、返回不确定项或无可用边车时生成教师复核单；绝不以 OCR 置信度代替复核。
- PDF：本地交付优先使用 `pandoc+xelatex` 生成 `带答案错题.pdf`；失败时降级到 Python `reportlab`。两条链路都不可用时继续交付 Markdown，并在 manifest 的 `pdf` 字段记录跳过原因。
- 公开 PDF：公开题图会重新参与 PDF 生成，输出文件名固定为 `带答案错题.pdf`，下载入口只在具体题目阅读页显示。公开端不得直接复制私有 `output/` 中的 PDF；必须从脱敏后的 `content.md` 和公开题图重新生成。`pandoc+xelatex` 失败时降级到 `reportlab`，降级版保留 Markdown 中的 LaTeX 编码；若仍失败则记为 `skipped`，不阻断安全的 Markdown 页面发布，学生站不得展示不存在的下载链接。
- JSON Schema：需要 Python `jsonschema`。缺失时模型验证失败，不把未校验模型标为成功。
- 浏览器运行时：需要 Node.js、Playwright 和可启动的 Chromium。依赖不存在时 `runtime_check.status=skipped`；浏览器成功启动但页面或控件报错时为 `failed`，阻止交付。
- 不支持的 `model_type`：仿真构建明确失败，不套用错误模板。

## 仿真单独构建

```bash
python3 .claude/skills/build-physics-simulator/scripts/build_simulator.py \
  student-error-library/entries/<entry-id>/physics-model.json \
  --entry-dir student-error-library/entries/<entry-id> \
  --output-dir output/<题目名称>/simulation \
  --name physics-simulator --zip --runtime-check auto
```

需要把浏览器检查设为硬门槛时使用 `--runtime-check required`；在明确无法启动浏览器且只做静态预检时使用 `--runtime-check skip`，报告仍会记录跳过原因。

如果 Node 或 Playwright 不在默认搜索路径，可在调用前设置项目专用环境变量 `PHYSICS_SIMULATOR_NODE` 和 `PHYSICS_SIMULATOR_NODE_MODULES`。构建器只把后者转换为子进程所需的 Node 模块搜索路径，不把平台专用绝对路径写进 Skill。

## 交付前检查

1. 打开 PDF，确认分页、公式和图片可读；
2. 确认 `answer-review.json` 的摘要仍对应当前答案和模型；
3. 若存在 `physics-model.json`，在工作台查看预审动态仿真，确认 `visualization-review.json` 对应当前产物摘要；否则确认 manifest 把可视化记为 `not-generated` / `not-required`，页面入口仍保留；
4. 对动态仿真查看条目 `visualization/simulation-build.json` 的模型、静态校验和运行时状态；桌面端确认画布与控制栏并排，手机端确认无需滚动即可看到画布、当前结论、播放和进度，次要控制默认折叠；
5. 确认 `delivery-manifest.json` 中答案、PDF、仿真和 `runtime_check` 状态；
6. 解压 `student-package.zip`，确认文件名为 ASCII 且 HTML/PDF/Markdown 可打开；
7. 优先向学生发送页面标记的 `student-package.zip` 或可用的 PDF；内部 JSON/截图不会出现在下载区。
8. 若发布学生端，检查公开预览不含姓名、学校、原题上传、教师版解析或本地路径，再执行公开确认；推送前只查看 `student-site/` 的 Git 变更。

## 常见故障

- 页面显示 Agent 不可用：查看 `/api/agent/providers` 的版本、缺失参数或熔断原因，再执行带 provider 的 `POST /api/agent/providers/probe`；不要只用 `command -v` 判断。若提示模型要求新版 CLI，升级该 CLI，或显式切到结构化 API/另一个 provider。
- 某个模型灰色不可选：在设置页检查该行状态。未测试、测试失败或修改过 API 地址/真实模型名/API Key 后，模型都会置灰并且默认不调用；重新点击该行“测试”，通过后再保存。
- API Key 看起来消失：这是正常脱敏。设置页不会回显明文；若显示已保存/已配置或测试通过，说明本地私有注册表仍有 key。只有勾选“清除已保存 Key”并保存才会删除。
- 教师端 Claude/DeepSeek 与终端 `claude` 使用不同 Key：这是预期行为。工作台只为当前 Gateway 子进程注入所选模型的地址、模型名和认证，不改写 `~/.claude/settings.json`；终端直跑仍读取用户自己的 Claude 配置。分别在设置页和终端环境中排查，不要靠修改全局 Key 修复网页任务。
- 模型测试通过但任务仍不可用：测试只证明当前配置能连通；Claude/Codex 的测试还证明一次性目录可受限写文件，不代表模型具备所有任务能力。检查模型 `capabilities`、模式默认映射和当前任务类型。完整解析 `analysis.generate` 会主动关闭文件工具，这是设计边界。
- OpenAI-compatible/LiteLLM 模型不会调用本地 Skill：它只返回结构化候选；后端负责确定性落盘和验证。需要模型主动读多文件或运行 Skill 脚本时，切换到 Claude/Codex 文件 Agent，或为目标 Agent 工具接入 JSON adapter。
- 修改设置后是否要重启：模型注册表、Codex 路径和代理配置会在每次任务前重读，保存后无需重启；修改 Python/JavaScript、provider adapter 或环境变量后需要重启教师工作台。重启会把当时仍在 queued/running 的作业标记为 `worker_interrupted`，请先等待任务结束或准备重新提交。
- Codex 测试超时或流中断：打开“设置”，点击“添加 Codex 可视化预设”旁的 Codex 状态框。若页面发现本地代理，点击对应“使用”按钮后再“检测”；若提示认证失效，先在终端执行 `codex login`。通过状态会保存在本机，后续任务自动复用。
- Agent 作业失败但文件没变化：这是修改前安全失败，可修复 provider 后重试；若 `auto` 还有可用 provider，Gateway 会自动降级。
- 显示“候选未通过范围或内容校验”：先查看作业结果的 `failure_repair`。`recovered` 表示一次性修复候选已通过；`exhausted` 表示修复一次后仍失败；`not-retried` 会给出安全边界或配置原因。再结合 `unauthorized_changes`、`validation_errors` 排查；canonical 未提升时不要手工伪造成功状态。
- HTML 双击打不开：先查看静态校验错误，再检查是否含远程 URL、模块脚本或丢失资源。
- HTML 能开但没有动画：查看 `runtime_check` 的控制交互和控制台错误。
- 答案与仿真事件不同：不要手改 HTML；修正 `physics-model.json` 的对应所有者字段，重新校验和构建。
- 修改解析后检索仍是旧内容：运行 `kb.py rebuild`。正常的 finalize、答案渲染和导出会自动重建。
