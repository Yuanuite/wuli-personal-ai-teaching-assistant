# Better Harness 改进计划：原子执行 Work-Tree

> 状态：`provisional`（分解完整，可执行；含 5 个执行期人工门禁与 1 个观察窗口）  
> 计划真源：[`better-harness-improvement-plan.md`](better-harness-improvement-plan.md)  
> 结构化同源文件：[`better-harness-improvement-work-tree.json`](better-harness-improvement-work-tree.json)  
> 基线日期：2026-08-03；任何执行都必须先完成 B00–B03，禁止直接从 Finding 修复任务开工。

## 0. 结论先行

这项改进不能按“5 个 finding = 5 个提交”执行。正确的闭环是：

```text
冻结真源与当前状态
→ 给未提交工作建立真实可恢复点
→ 清理 ruff/mypy 存量债务
→ 修复日志基础设施与 W3 trace 传播
→ 声明依赖、注册 Qoder Skills
→ 接通强制 CI
→ 全量回归、远程 canary、复跑 Better Harness
→ 人工验收并进入至少两个 Episode 的观察窗口
```

原计划的方向保留，但本 Work-Tree 修正了 5 个会导致误执行的细节：

1. `git tag` 只能保护当前 `HEAD`，不能保护未跟踪文件；恢复点必须覆盖 working tree，并在恢复后核对哈希。
2. 当前质量债务不是“小于 50 条”：同一工作区实测 `ruff=247`、`mypy=305`，不能直接把全量命令加入强制 CI。
3. `get_logger("child")` 当前会因 handler 未注入 `trace_id` 产生格式化错误；必须先修 `log.py`，再接六个模块。
4. `w3_pipeline.py` 使用线程池，基于 thread id 的 trace 不会自动传播；“导入 logger”不等于全链可观测。
5. `.agents/skills/` 当前已经是指向 `.claude/skills/` 的符号链接，不需要再“去重”；真正缺口是 `.qoder/skills/` 与 Qoder 是否跟随链接的验证。

## 1. 真源、范围与当前证据

### 1.1 权威输入

| 输入 | 基线 SHA-256 / 版本 |
|---|---|
| `docs/better-harness-improvement-plan.md` | `824e129f685718a6be19f8c92b98f770288c05f2e805552668f643909a315213` |
| `.qoder/better-harness/2026-08-03/182513-zhangxinqi/findings.json` | `feee930697c1fd479df46760977bcd40dad1f47505724df18a51999b59301e30` |
| `pyproject.toml` | `210bfd11f94a41d0e6281949b62d27698475a50363ed18eda6d020c78bf0cab0` |
| `.github/workflows/deploy.yml` | `c357ca708131964921e78acab95171e7f963d8a24fe18878f14d1e0bbf20150c` |
| `teacher-console/log.py` | `be7fcef96b46520ea1322adfdbe687f4575db6a69f83679184f0cccddfaab0e7` |
| Git 基线 | `3037901` (`main`) |

哈希只用于发现执行期间的输入漂移，不表示文件内容已被接受。B00 必须重新计算；任一真源变化都使 B02 及其全部下游失效。

### 1.2 2026-08-03 复核快照

| 项目 | 实测结果 | 对执行的影响 |
|---|---:|---|
| `git status --porcelain=v1 -uall` | 147 项，均为未跟踪；tracked/cached diff 为 0 | 原 finding 的 186 文件是历史快照，先重算再分类 |
| `ruff check teacher-console/ scripts/ --statistics` | 247 errors：E501 165、I001 73、E731 4、UP012 3、F401 1、F841 1 | 先清债再强制，禁止用 `continue-on-error` 伪装最终完成 |
| `mypy teacher-console/` | 305 errors / 84 files / 176 checked files | 必须按文件所有权分片，不能一次性“大修所有类型” |
| `get_logger("probe")` smoke | `KeyError: trace_id` 的 logging formatter error | O31 是六模块接入的硬前置 |
| W3 并发 | `w3_pipeline.py` 含 `ThreadPoolExecutor` | O32 必须显式传播 trace |
| Skills | `.agents/skills/*` 已为符号链接；`.qoder/skills/` 不存在 | 只补 Qoder 兼容层，并验证链接跟随能力 |

### 1.3 包含范围

- Finding 1–5 的工程闭环；
- Python 质量债务清零与 CI 强制门禁；
- 六个 W3/正确性模块的有意义日志事件、trace 连续性与隐私测试；
- 一套 Python 依赖真源和干净环境安装验证；
- Qoder Skill 发现与三消费方一致性；
- AGENTS/CLAUDE 的最小运行规范、最终回归、Better Harness 对比报告。

### 1.4 排除范围

- 不改变题目处理生命周期、审批权限、公开发布边界或 canonical 答案；
- 不修改 `analysis.generate`、W3、Evidence、Claim Ledger 的业务契约；
- 不借质量清理重构业务逻辑；
- 不自动推送 GitHub、不自动删除用户文件、不提交原始学生数据；
- 不保证一次复跑即可提升 Learning Capture 分数；该维度至少需要两个可比 Episode。

## 2. 执行期决策门

只有真正改变路线的选择保留为人工门禁。推荐值已经给出；用户未批准时不得越过对应任务。

| ID | 决策 | 推荐默认 | 必须讨论的条件 | 记录位置 |
|---|---|---|---|---|
| DG-01 | 147 项未跟踪内容的 `commit / ignore / defer / sensitive` 分类 | 代码/测试/经复核报告提交；`.qoder/better-harness/` 忽略；隐私内容不提交 | 任一路径含学生原图、密钥、真实答案私有资料或归属不明 | `decision-register.json` |
| DG-02 | Python 支持策略 | 本轮保持最低 3.9、CI 运行 3.11；版本升级另立任务 | 用户明确允许放弃 3.9，或依赖已不支持 3.9 | 同上 |
| DG-03 | 依赖声明形式 | 方案 B：`requirements.txt` + `requirements-dev.txt`；当前是脚本型应用、无打包意图 | 用户希望发布可安装包，且安装 spike 证明 `[project]` 不会引入错误包发现 | 同上 |
| DG-04 | Qoder 公开 Skill allowlist | AGENTS.md 中的 8 个 Skill；暂不暴露内部 `decompose-physics-problem` | 用户确认内部 Skill 也应被 Qoder 直接触发 | 同上 |
| DG-05 | 远程 CI canary | 临时分支/PR，验证一绿两红后删除 | 未授权 push/PR、仓库保护策略不允许临时 canary | 同上；未授权则最终状态为 `provisional` |

## 3. 稳定目标

| Target | 可观察成功标准 | 优先级 | 批准者 |
|---|---|---|---|
| T1 基线可信 | 真源、HEAD、工作区、工具版本和 finding 复现结果均有哈希与时间戳；决策门有签字状态 | critical | 人类维护者 |
| T2 可恢复交付 | dirty snapshot 有可验证恢复对象；保留内容按逻辑组提交；机器产物被精确忽略；最终工作区可解释且可回退 | critical | 人类维护者 |
| T3 强制质量门禁 | 目标范围 `ruff=0`、`mypy=0`；CI 直接运行两命令且无 `continue-on-error`；故障 canary 能阻断 | critical | CI + 人类维护者 |
| T4 W3 可观测性 | 六模块有事件目录覆盖；同一 W3 运行跨 server、线程池和子模块保持同一 trace；异常可定位且日志不含题干/答案原文 | high | 测试 + 人工日志复核 |
| T5 依赖可复现 | 只有一套 Python 依赖真源；干净 venv 可安装、import、启动、运行质量工具与测试；CI 不再手列依赖 | high | 测试 |
| T6 Skill 可发现 | Qoder inventory `Skills >= 8`；Claude/Codex/Qoder 指向同一真实内容；抽查 2 个 Skill 可触发 | medium | 工具实测 + 人类维护者 |
| T7 经验可比较 | 每个逻辑组有提交、测试证据和变更记录；Better Harness 前后报告同条件可比；至少两个 Episode 后再评价 Learning Capture | medium | 人类维护者 |
| T8 系统不回归 | 单元、E2E、隐私、日志、依赖、CI、graphify 健康检查均通过；无学生数据或审批边界变化 | critical | 人类维护者 |

## 4. 状态模型

```text
P0 unbounded
  输入可能漂移，工作区无执行级恢复证据
    │ B00–B03
    ▼
P1 frozen
  真源与决策已冻结
    │ R10–R19
    ▼
P2 recoverable
  dirty snapshot 可恢复，历史工作被逻辑化
    │ Q20–Q22 + O30–O40 + D50–D53 + S60–S62
    ▼
P3 repaired
  日志/依赖/Skill 结构修复，质量债务可归属
    │ Q23–Q29 + I70
    ▼
P4 gated
  本地 ruff/mypy 为零，CI 门禁已配置
    │ I72–I73 + C80–C83
    ▼
P5 verified
  全量回归通过、提交可回退、工作区干净
    │ I71 + I74–I75
    ▼
P6 observed
  远程 canary 与 Better Harness 同条件对比完成
    │ I76 人工批准
    ▼
P7 accepted / provisional
```

真实过程允许从 P3/P4 回到更早状态，但任务依赖 DAG 不含环。所有回跳都必须带新输入版本，并只失效最小依赖锥。

## 5. 原子任务 Work-Tree

公共约束：每个任务只消费其列出的冻结输入，只能修改“文件所有权”内的文件；失败不得提交、不覆盖已接受产物。默认最大尝试 2 次，高风险 checkpoint/commit/canary 任务 1 次。

### Wave B：冻结真源与决策

| ID / verb | 依赖 | 冻结输入 | 唯一输出 | 动作与边界 | 验证 / 失败状态 |
|---|---|---|---|---|---|
| B00 `fingerprint` | — | 计划、findings、治理文档 | `test-results/better-harness/B00-input-fingerprints.json` | 记录 SHA-256、mtime、HEAD、branch、工具版本；不改仓库 | 所有权威输入均存在且哈希可重算；缺失即 `unsupported` |
| B01 `snapshot` | — | 当前 working tree | `B01-workspace-snapshot.json` | 记录 tracked/untracked/ignored、文件哈希、stash/tag、敏感扫描摘要；不得打印敏感正文 | 同一命令二次运行结果稳定；工作区在任务内无变化 |
| B02 `audit` | B00,B01 | 两份快照、源 findings | `B02-finding-audit.json` | 重跑 ruff/mypy、日志 probe、Skill 链接和 CI/依赖静态检查；逐条标注 `confirmed/stale/expanded/unsupported` | 每个 finding 有现证据与命令 exit code；不得沿用历史数量 |
| B03 `approve` | B02 | finding audit | `B03-decision-register.json` | 用户批准 DG-01…05；未决定项记录 owner、默认、截止点 | critical 决策未批准则 `blocked`；非 critical 决策可保持 `provisional` |

### Wave R：建立真实恢复边界

| ID / verb | 依赖 | 冻结输入 | 唯一输出 | 动作与边界 | 验证 / 失败状态 |
|---|---|---|---|---|---|
| R10 `classify` | B03 | B01 清单、DG-01 | `R10-preservation-ledger.json` | 每个 dirty 路径唯一归入 `commit/ignore/defer/sensitive`，指定逻辑组和测试 | 路径全集无遗漏、无重复；归属不明即 `blocked` |
| R11 `checkpoint` | R10 | 批准后的 preservation ledger | `R11-recovery-checkpoint.json` | 用命名 stash（含 untracked）保存 approved snapshot，记录 OID 后立即 apply；可同时给基准 HEAD 打本地 tag。禁止只打 tag | apply 后文件哈希与 B01 一致；stash OID 可读；失败立即停止，禁止清理 |
| R12 `ignore` | R11 | `ignore` 分类 | `R12-ignore-diff.patch` | 只给机器产物加精确规则，如 `.qoder/better-harness/`；不得忽略整个 `.qoder/`，否则会吞掉 `.qoder/skills/` | `git check-ignore -v` 仅命中批准路径；被保留路径不得被误忽略 |
| R13 `partition` | R12 | `commit` 分类 | `R13-commit-plan.json` | 将保留内容冻结为四类：runtime+tests、evaluation+reports、docs+plans、skills；每文件只能属于一组 | 四组路径互斥、并集完整；每组给出验证命令与预期提交信息 |
| R14 `verify` | R13 | 四组候选 | `R14-group-verification.json` | 在不提交的情况下逐组运行最小测试、`git diff --check`、隐私扫描 | 任一组失败只阻断该组；不得带病提交 |
| R15 `commit` | R14 | runtime+tests 组 | `R15-runtime-commit.json` | 只 stage 并提交 runtime+tests；若组为空则显式 `skipped` | staged 路径等于组清单；记录 commit SHA 和测试证据 |
| R16 `commit` | R15 | evaluation+reports 组 | `R16-evaluation-commit.json` | 只提交评测脚本与其同源报告，不混入业务配置 | 路径清单、SHA、验证命令一致 |
| R17 `commit` | R16 | docs+plans 组 | `R17-docs-commit.json` | 只提交经复核文档、执行计划和契约示例 | Markdown 链接、敏感扫描、`git diff --check` 通过 |
| R18 `commit` | R17 | skills 组 | `R18-skills-commit.json` | 只提交 Skill 真源和兼容链接；不得复制第三份正文 | 符号链接解析、Skill 校验、路径清单通过 |
| R19 `verify` | R18 | R11、R15–R18 | `R19-recovery-acceptance.json` | 验证 stash 可定位、每个提交可独立 revert（dry-run/临时分支）、status 只剩批准的 ignored/deferred 项 | 无解释 dirty 项即 `failed`；不得 drop recovery stash |

### Wave Q/O：先清 lint，再建设可观测性，再清类型债务

| ID / verb | 依赖 | 冻结输入 | 唯一输出 | 文件所有权 / 动作 | 验证 / 失败状态 |
|---|---|---|---|---|---|
| Q20 `freeze` | R19 | pyproject、CI、DG-02 | `Q20-quality-policy.json` | 固定 ruff/mypy 范围、Python 语义、允许的 override 原则；本轮不得靠缩小扫描范围或新增全局 ignore 变绿 | policy 与 CI/本地命令一一对应 |
| Q21 `repair` | Q20 | ruff JSON 基线 | `Q21-ruff-autofix.json` | 对显式文件清单执行安全 `ruff --fix`；不启用 unsafe fixes；保存前后规则计数 | 自动修复 diff 经人工抽查，相关测试通过 |
| Q22 `repair` | Q21 | 剩余 ruff 清单 | `Q22-ruff-zero.json` | 按 E501/E731/F841 等规则手工修复；不得改业务行为或全局关闭规则 | `ruff check teacher-console/ scripts/` exit 0 |
| O30 `specify` | Q22 | `log.py`、W3 调用图、隐私规则 | `O30-observability-contract.json` | 定义 trace owner、事件名、必需键、级别、敏感字段黑名单、异常归属；内层模块不得无条件创建新 TraceContext | 六模块每个关键入口/决策/终止至少一个事件；无题干/答案字段 |
| O31 `repair` | O30 | log probe、事件合同 | `O31-log-infrastructure.json` | 修 `log.py`：handler 级 trace filter、嵌套 context 恢复外层 trace、只读 current-trace 接口；不改日志输出目的地 | 子 logger、嵌套、异常、context 清理单测通过；无 formatter error |
| O32 `propagate` | O31 | W3 线程池边界 | `O32-thread-trace.json` | 在线程提交点显式携带父 trace；worker 退出后清理，不泄漏到下一个任务 | 两个并发 worker 与父线程同 trace；复用线程无串线 |
| O33 `instrument` | O32 | O30 合同 | `O33-w3-pipeline-logging.json` | 仅改 `w3_pipeline.py`：路由、stage、batch、回跳、stop reason、异常；高频事件用 DEBUG | 行为测试与日志断言通过 |
| O34 `instrument` | O32 | O30 合同 | `O34-evidence-agent-logging.json` | 仅改 `evidence_agent.py`：candidate/need/coverage/gateway/termination 计数与状态 | 不记录 problem/reflection 正文；相关测试通过 |
| O35 `instrument` | O32 | O30 合同 | `O35-cognitive-loop-logging.json` | 仅改 `cognitive_loop.py`：round、challenge、impact cone size、no-progress/stop | 确定性返回值前后相同；循环日志量受控 |
| O36 `instrument` | O32 | O30 合同 | `O36-correctness-faults-logging.json` | 仅改 `correctness_faults.py`：case id、fault code、expected/actual、suite summary | 故障注入测试通过，异常保留 traceback |
| O37 `instrument` | O32 | O30 合同 | `O37-claim-ledger-logging.json` | 仅改 `claim_ledger.py`：图规模、验证 issue code、版本/状态迁移；纯 helper 不刷 INFO | Claim 内容和证据原文不入日志；Claim tests 通过 |
| O38 `instrument` | O32 | O30 合同 | `O38-proof-aggregation-logging.json` | 仅改 `proof_aggregation.py`：输入证书计数、VERIFIED/PROVISIONAL/UNRESOLVED 分布、根路径问题 | 聚合结果字节等价；proof tests 通过 |
| O39 `test` | O33–O38 | 六模块日志产物 | `O39-observability-test-report.json` | 添加行为型日志测试，不用“仅 grep import”的伪覆盖；注入隐私 sentinel 并捕获 records | trace 连续、事件完备、sentinel 缺席、无重复异常日志 |
| O40 `smoke` | O39 | 隔离 fake W3 fixture | `O40-w3-trace-smoke.json` | 在临时知识库/输出目录跑一条 fake W3；从 server 入口收集全链日志 | 一个 trace 覆盖 server→W3→evidence/loop/ledger/proof；正式库无变化 |
| Q23 `classify` | O39,D52 | 最新 mypy 输出 | `Q23-mypy-ownership.json` | 将每条 error 按唯一文件所有权分成 runtime、W3/evidence、visual/publication、scripts、tests；单独列第三方边界 | 305 的历史数字只作参考；最新 error 全量无遗漏 |
| Q24 `repair` | Q23 | runtime 分片 | `Q24-mypy-runtime-zero.json` | 只改 server/gateway/jobs/model/failure 等 runtime 分片；遇跨组接口只开 ticket | 分片 mypy=0 + 对应单测 |
| Q25 `repair` | Q23 | W3/evidence 分片 | `Q25-mypy-w3-zero.json` | 只改 W3、correctness、evidence、claim、proof 分片 | 分片 mypy=0 + W3/证据单测 |
| Q26 `repair` | Q23 | visual/publication 分片 | `Q26-mypy-visual-zero.json` | 只改视觉、图解、public site/知识库边界分片 | 分片 mypy=0 + 隐私/发布/视觉测试 |
| Q27 `repair` | Q23 | scripts 分片 | `Q27-mypy-scripts-zero.json` | 只改 `teacher-console/scripts/` 与批准 Skill scripts；不把 CLI 参数移回 server | 分片 mypy=0 + CLI smoke |
| Q28 `repair` | Q23 | tests 分片 | `Q28-mypy-tests-zero.json` | 只改测试注解、stub、fixture；不得用错误注解掩盖生产接口问题 | tests 分片 mypy=0，pytest 行为不变 |
| Q29 `aggregate` | Q24–Q28 | 五个 zero report | `Q29-quality-zero.json` | 汇总全范围质量检查；接口冲突回到最小分片，不批量追加 ignore | `ruff` 与 `mypy` 两条最终命令均 exit 0 |

### Wave D/S：依赖真源与 Skill 发现（可与 O/Q 的非冲突任务并行）

| ID / verb | 依赖 | 冻结输入 | 唯一输出 | 动作与边界 | 验证 / 失败状态 |
|---|---|---|---|---|---|
| D50 `inventory` | R19 | Python imports、CI、runbook | `D50-dependency-ledger.json` | 用 AST 分类 stdlib/project/runtime/dev/optional，并映射 import 名到发行包名；Node 依赖仍归 package-lock | 每个第三方 import 有唯一来源和最小验证命令 |
| D51 `select` | D50 | ledger、DG-02/03 | `D51-dependency-decision.json` | 在干净临时 venv 做方案 spike；默认选择 requirements 双文件；记录 Python 支持矩阵 | 选择规则可复现；另一方案明确拒绝理由 |
| D52 `declare` | D51 | dependency decision | `D52-dependency-spec.json` | 创建唯一 Python 依赖真源并删除 CI 手写清单；运行时、dev、optional 不混淆；不同时维护两套声明 | 解析无环、重复或遗漏；质量工具来自 dev 依赖 |
| D53 `smoke` | D52 | 依赖真源 | `D53-clean-venv-report.json` | 在 `mktemp -d` 的全新 venv 安装，验证 server import/启动、pytest/ruff/mypy 可执行；不读取正式题库 | 所有 smoke exit 0；失败先回 D50/D52，不改全局 Python |
| S60 `freeze` | R19 | AGENTS Skill 表、三目录 | `S60-skill-allowlist.json` | 固定公开 Skill 列表、canonical 路径、消费者；默认 8 个，内部 Skill 不自动暴露 | 每个 allowlist 项存在且 SKILL.md 可读 |
| S61 `register` | S60 | allowlist | `S61-qoder-registration.json` | 首选 `.qoder/skills/<name> -> ../../.claude/skills/<name>`；不得复制正文；`.agents` 保持现有链接 | 链接逐一 resolve 到 canonical；git 能保存链接类型 |
| S62 `verify` | S61 | Qoder 注册 | `S62-skill-discovery-report.json` | 复跑同版本 Qoder asset baseline，抽查 manage-student-error-library/neat-freak；若 Qoder 不跟链接，只允许带哈希校验的生成镜像后重试 S61 | `Skills >= 8`、两次触发成功、三目录内容哈希一致 |

### Wave I/C：集成、提交与最终闭环

| ID / verb | 依赖 | 冻结输入 | 唯一输出 | 动作与边界 | 验证 / 失败状态 |
|---|---|---|---|---|---|
| I70 `integrate` | Q29,D53 | quality zero、依赖 smoke | `I70-ci-gate-report.json` | CI 安装唯一 dev 依赖真源；pytest 前直接运行 ruff/mypy；无 `continue-on-error`、无重复手写依赖 | YAML 解析、静态契约和本地同源命令通过 |
| I72 `document` | I70,O40,S62 | 已验证实现 | `I72-governance-diff.json` | 只在 CLAUDE/AGENTS、runbook、CHANGES 加最小运行规则与真实命令；规则手册不写流水账 | 链接与命令有效；事实均有测试证据 |
| I73 `test` | I72 | 全部未提交实现 | `I73-full-regression.json` | 运行全量 pytest、隔离 E2E、质量检查、隐私扫描、Skill/依赖 smoke；测试只用临时库 | critical suite 全绿；skipped 必须有批准理由 |
| C80 `commit` | I73 | observability 文件组 | `C80-observability-commit.json` | 只提交 log 基础设施、六模块和日志测试 | staged scope 精确；记录 SHA 与 I73 证据 |
| C81 `commit` | C80 | quality+dependency+CI 文件组 | `C81-quality-commit.json` | 只提交类型/lint、依赖声明、CI 与相关测试 | staged scope 精确；commit 可独立 revert |
| C82 `commit` | C81 | Skill+governance 文件组 | `C82-governance-commit.json` | 只提交 Qoder links 与最小文档规则 | 不提交 `.qoder/better-harness/` 机器报告 |
| C83 `verify` | C82 | 全部提交与 recovery checkpoint | `C83-final-git-state.json` | 重跑 status、log、commit scope、revertability；recovery stash 保留到人工接受后一周 | 工作区仅有批准 ignored 项；无 staged/unknown 文件 |
| I71 `test` | C83 | DG-05、已提交 CI | `I71-ci-canary.json` | 经授权创建临时分支/PR：clean 应绿、unused import 应红、类型错误应红；记录 run URL 后删除临时分支 | 三个期望均满足；无授权则 `unsupported` 且总状态 provisional |
| I74 `update` | C83 | 已接受代码/文档 | `I74-graphify-health.json` | `graphify update .`，再查 CI、logging、Skill 路径；只更新图谱，不据图谱制造业务结论 | update 成功，无 dangling/corrupt 警告；若有则显式保留 |
| I75 `compare` | I71,I74 | 原 findings 与同版本复跑 | `docs/reports/better-harness-closure-v1.md` | 同工具版本、同范围复跑 Better Harness；逐 finding 比较，不承诺预期分数 | 5 findings 各有 closed/open/provisional 证据；Learning Capture 单列观察期 |
| I76 `accept` | I75 | closure report、全部证据 | `I76-owner-acceptance.json` | 人类确认关闭、保留风险、恢复点保留期限和下一观察日期 | critical obligation 全过才 `completed`；否则 `provisional/blocked` |

## 6. 执行波次与角色分配

同一波只表示依赖允许并行；涉及同一 Git index 的 commit 永远串行。未获用户授权时这里只是分配计划，不自动派生 Agent。

| 波次 | 任务 | 并行 | 角色 | 退出门 |
|---|---|---:|---|---|
| W0 | B00,B01 | 是 | baseline-auditor | 两份快照产生 |
| W1 | B02 | 否 | harness-auditor | findings 全量复核 |
| W2 | B03 | 否 | human-owner | critical 决策批准 |
| W3 | R10→R14 | 否 | repository-curator | checkpoint 与分组验证通过 |
| W4 | R15→R19 | 否 | repository-curator | 恢复边界接受 |
| W5 | Q20→Q22；D50→D53；S60→S62 | 有限并行 | quality/dependency/skill owners | ruff=0、依赖 smoke、Skills≥8 |
| W6 | O30→O32 | 否 | observability-owner | log 基础设施与线程传播通过 |
| W7 | O33–O38 | 是，严格一文件一 owner | module owners | 六模块行为测试通过 |
| W8 | O39→O40 | 否 | observability-verifier | fake W3 全链单 trace |
| W9 | Q23 | 否 | type-triager | 最新 mypy 清单唯一归属 |
| W10 | Q24–Q28 | 是，禁止跨分片改文件 | type owners | 各分片为零 |
| W11 | Q29→I70→I72→I73 | 否 | integration-owner | 本地全绿 |
| W12 | C80→C83 | 否 | repository-curator | 三个逻辑提交、工作区干净 |
| W13 | I71,I74 | 可并行 | CI verifier / graph maintainer | 远程 canary 与图谱健康 |
| W14 | I75→I76 | 否 | harness-auditor / human-owner | closure report 接受 |

建议最大并发 3；W7/W10 才适合并行工作。禁止两个执行者同时修改 `pyproject.toml`、`deploy.yml`、`log.py`、`CLAUDE.md` 或 Git index。

## 7. 状态传递合同

| 合同 | 生产者 → 消费者 | 载荷 | 指纹与前置条件 | 失效规则 |
|---|---|---|---|---|
| ST-baseline | B00/B01 → B02 | 真源哈希、HEAD、workspace manifest | 两者均 passed | 任一输入哈希或 HEAD 变化 |
| ST-decision | B02 → B03/R10 | finding audit + 决策问题 | audit 覆盖 5 findings | findings/tool version 变化 |
| ST-preservation | R10 → R11–R13 | 每路径分类、逻辑组、敏感标记 | 人工批准 DG-01 | 路径集合或文件哈希变化 |
| ST-checkpoint | R11 → R12/R19 | stash OID、base tag、恢复后 hash diff | hash diff 为空 | stash 不可读或工作区变化 |
| ST-quality-policy | Q20 → Q21/Q29/I70 | 扫描范围、版本、禁止规避项 | DG-02 已记录 | pyproject/CI scope 变化 |
| ST-log-contract | O30 → O31–O39 | event catalog、trace owner、privacy blacklist | 六模块覆盖完整 | 入口函数/线程边界变化 |
| ST-thread-trace | O32 → O33–O40 | 父子 trace 传播 API | 并发测试 passed | executor 或 TraceContext 实现变化 |
| ST-mypy-ownership | Q23 → Q24–Q28 | error id→唯一文件分片 | 最新全量 mypy 输出 | 任一生产文件或 mypy 配置变化 |
| ST-dependencies | D52 → D53/I70 | 唯一依赖真源与支持矩阵 | D51 决策批准 | import 集合或 Python 支持变化 |
| ST-skills | S60/S61 → S62/I72 | allowlist、canonical path、representation | 所有 link resolve | Skill 内容/allowlist/Qoder 版本变化 |
| ST-regression | I73 → C80–C82 | 命令、exit、测试数、环境指纹 | critical suite 全绿 | 提交内容与测试快照不一致 |
| ST-closure | C83/I71/I74 → I75/I76 | Git/CI/graph/harness evidence | 所有 critical evidence 可读 | 新提交、远程 run 变化或图谱陈旧 |

结构化 JSON 对每一条直接 task dependency 还给出一条一对一 state transfer；本表是面向执行者的合同归并视图。

## 8. 验证义务

| ID | Claim | 方法与证据 | 决策规则 | 可能推翻它的证据 | 无法验证时 |
|---|---|---|---|---|---|
| V1 | 执行依据仍是当前事实 | B00–B02 哈希、工具输出、graphify 结构证据 | 5 findings 全有当前复现或明确 stale 标记 | 真源/HEAD/工具版本变化 | 停在 P1，重跑 B00 |
| V2 | 未提交工作可恢复 | stash OID、apply 后哈希、逻辑 commits、status | approved 文件 100% 可恢复；无未知 dirty | stash 损坏、hash mismatch、敏感文件误提交 | `blocked`，禁止清理/提交后续改动 |
| V3 | CI 真正强制质量 | 本地 zero、YAML、远程三态 canary | clean 绿；lint/type 各自红；无 continue-on-error | 扫描范围缩小、ignore 增多、canary 假绿 | 无远程权限则 `provisional` |
| V4 | W3 日志可用于诊断 | 子 logger、嵌套、线程、六模块、fake W3 日志测试 | 单 trace 串全链；关键状态齐；异常有 traceback | formatter error、trace 分裂/泄漏、只有 import 无事件 | 保持旧行为，回滚最小日志分片 |
| V5 | 日志不泄露教学私有内容 | sentinel 注入、record 扫描、事件 schema | 题干/答案/证据正文 sentinel 0 命中 | `%s` 直接记录 payload/exception 含正文 | critical fail，不得合并 |
| V6 | 依赖可复现 | D50 ledger、D53 clean venv、CI 同源安装 | 安装/import/start/tools/tests 全部成功 | 本机预装包掩盖遗漏、CI 手列第二份依赖 | 回到 D50，禁止给全局环境补包 |
| V7 | Skill 只有一份真实内容且可发现 | readlink、hash、Qoder inventory、trigger smoke | Skills≥8、抽查 2 个、canonical hashes 一致 | Qoder 不跟链接、生成镜像漂移 | 允许有哈希守护的镜像，结果仍标注兼容层 |
| V8 | 系统无回归 | I73 全量测试、隐私扫描、E2E 隔离、graph health | critical suite 全绿且正式库未变 | skipped 无理由、正式产物 mtime 变化 | `failed`，回滚最小依赖锥 |
| V9 | 改进证据可比较 | 同版本 Better Harness 前后报告、Episode 时间窗 | 5 findings 逐项有证据；Learning Capture 至少 2 Episode 后评价 | 工具版本/范围变化、只有文档自述 | 维持 `provisional`，不虚构分数提升 |

## 9. 反馈、回跳与熔断

| 触发 | 需要的新证据 | 回跳任务 | 只失效哪些任务 | 停止规则 |
|---|---|---|---|---|
| 真源/HEAD 漂移 | 新 fingerprints | B00 | B02 及全部下游 | 连续 2 次在冻结期间漂移则 `blocked` |
| checkpoint hash 不一致 | 差异路径与 stash OID | R10 或 R11 | R11–R19 | R11 只允许 1 次；失败不清理工作区 |
| ruff 自动修复改变行为 | 最小测试失败与 diff | Q21 | Q21–Q29、I70+ | 第二次仍失败则逐文件手修，禁止 unsafe fix |
| mypy 跨分片接口冲突 | interface ticket、producer/consumer | Q23 | 受影响分片 + Q29 | 每个 error 最多 2 次归属变更 |
| child logger/线程 trace 失败 | 捕获 records、线程 id、trace id | O31 或 O32 | O31–O40、Q23+ | 2 轮无进展则恢复旧日志行为并标 `provisional` |
| 日志 sentinel 泄露 | 命中 event 与字段 | 对应 O33–O38 | 该模块 + O39/O40 | privacy failure 不允许豁免 |
| clean venv ImportError | import→distribution mapping | D50 | D50–D53、I70+ | 2 轮仍不明则 `unsupported` 并列出缺包 |
| Qoder 不跟 symlink | Qoder 版本、inventory、link probe | S61（representation v2） | S61/S62/I72+ | 第 2 次使用带 hash 守护的生成镜像；仍失败则 `provisional` |
| CI canary 与本地不一致 | run URL、job logs、环境版本 | I70 | I70–I76 | 2 次相同失败后停止，禁止通过 continue-on-error 绕过 |
| Better Harness 分数未升 | finding 逐项证据、工具版本 | I75 | 仅 I75/I76 | 不按分数盲修；只处理仍有可复现证据的 finding |

所有重试必须改变输入版本或修复假设。相同 fingerprint 的任务直接复用上次结果，不重复运行。全局最多 3 个反馈 round；单任务最多 2 次，R11/commit/canary 最多 1 次。一个 round 内没有任何 obligation 状态变化即触发 no-progress fuse。

## 10. 最终聚合与完成定义

只有通过各自 verifier 的产物才能进入 `better-harness-closure-v1.md`。最终报告必须保留：

- 当前与历史数字的差异，不把 186/33723 行当作永恒事实；
- Python 版本、依赖形式、Skill allowlist 的批准决定；
- 所有 skipped/unsupported 项及原因；
- CI run URL、commit SHA、recovery stash OID（OID 可留本地附件）；
- Learning Capture 的观察期，不能用一次复跑宣称“经验沉淀已验证”。

工程状态 `completed` 的硬条件：

1. V1–V8 全部 passed；
2. T1–T8 均有接受产物；
3. `ruff check teacher-console/ scripts/` 与 `mypy teacher-console/` exit 0；
4. CI clean/lint/type 三态 canary 符合预期；
5. 六模块同 trace 行为测试与隐私 sentinel 测试通过；
6. 干净 venv 和全量 pytest/E2E 通过；
7. Qoder Skills≥8；
8. 三个新逻辑提交可独立回退，工作区无未知 dirty 项；
9. 人类维护者完成 I76。

若只缺 DG-05 远程授权或两个 Episode 的时间窗口，交付状态是 `provisional`，不是 `failed`。任何 recovery/privacy/业务回归问题都必须是 `blocked` 或 `failed`，不得降级为文档说明。

## 11. 预计工期（以当前 247/305 基线为准）

| 工作包 | 乐观 | 常规 | 放大条件 |
|---|---:|---:|---|
| B/R 基线与恢复 | 0.5 天 | 1 天 | 分类中发现隐私内容或提交组测试失败 |
| Ruff 清理 | 0.5 天 | 1 天 | E501 涉及大量模板/长字符串 |
| Mypy 五分片 | 2 天 | 4–6 天 | 核心接口推断错误、跨分片依赖多 |
| 日志基础设施+六模块 | 1.5 天 | 2–3 天 | 线程 trace、异常隐私处理需要重构 |
| 依赖/Skill/CI | 0.5 天 | 1–2 天 | Qoder 不跟链接、依赖分层不清 |
| 全量回归/远程 canary/复跑 | 0.5 天 | 1–2 天 | E2E 或 Actions 排队/环境差异 |
| 合计 | 5 天 | 10–15 天 | 不含 2–4 周 Learning Capture 观察窗 |

原计划的“2 天完成”不再作为承诺；以质量债务实际清零和验证义务为准。

## 12. 禁止捷径

- 不用 `git tag` 冒充 working tree 备份；
- 不用 `continue-on-error`、缩小扫描范围、全局 ignore 或大面积 `# type: ignore` 冒充质量门禁；
- 不只检查六模块是否 `import logger`，必须验证事件与 trace 行为；
- 不在内层 helper 无条件创建新 `TraceContext`；
- 不在日志记录题干、答案、证据全文或密钥；
- 不同时维护 `[project].dependencies` 和 requirements 两套手工真源；
- 不忽略整个 `.qoder/`；
- 不因 Qoder 不跟链接就手工复制且无漂移校验；
- 不在无授权时 push、建 PR、删除分支或 drop recovery stash；
- 不把 Better Harness 分数当作比测试、隐私与可恢复性更高的真源。
