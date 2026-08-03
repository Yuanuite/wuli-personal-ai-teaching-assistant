# Better Harness 改进计划 — 悟理项目

> 来源：`/better-harness` 分析运行（2026-08-03），findings 见
> `.qoder/better-harness/2026-08-03/182513-zhangxinqi/findings.json`。
> 本文档为每个保留发现提供：现状证据、根因分析、影响评估、分步修复方案、
> 验收标准与工作量估计，并给出总体路线图。

## 一、总体现状评估

| 维度 | 得分 | 一句话结论 |
|------|------|-----------|
| 任务理解 | 72 | AGENTS.md 路由清晰，但缺少可观测的 Episode 验证 |
| 可控执行 | 60 | 权限边界完备，依赖声明缺失、Skill 发现断链 |
| 改动验证 | 55 | 测试覆盖好，但质量门禁形同虚设、新模块无日志 |
| 可靠交付 | 45 | 交付边界有设计，但工作区无恢复点 |
| 经验沉淀 | 35 | 会话证据不足，Learning Capture 无法评估 |

**核心矛盾**：项目在"设计层"（AGENTS.md 路由、接受边界、权限门禁、
delivery-manifest）做得很完备，但在"运行层"（CI 强制执行、可观测性、
版本恢复、依赖复现）存在系统性断层。改进重心应放在把已有配置
"接通电路"，而不是新增更多设计文档。

**优先级总原则**：

1. **先建恢复边界**（finding 5）——所有后续改动都依赖可回滚的 git 状态；
2. **再接通 CI 门禁**（finding 1）——让后续改动受保护；
3. **然后补可观测性**（finding 2）——提升排障能力；
4. **再做依赖声明与 Skill 发现**（finding 3/4）——改善环境与工具链；
5. **最后建立经验沉淀节奏**（横向建议）——靠日常习惯而非一次性工程。

---

## 二、Finding 1：CI 不执行已配置的 ruff/mypy 质量门禁

**ID**: `ci-quality-gates-not-enforced` · **严重度**: Medium · **维度**: 改动验证

### 2.1 现状证据

- `pyproject.toml` 已配置：
  - `[tool.ruff]`：target py39，line-length 120，select `E/F/W/I/UP`；
  - `[tool.mypy]`：strict 模式（含 5 项 disable_error_code 降噪），
    并对 `agent_gateway`、`server` 等 11 个核心模块启用 `follow_imports = "normal"`；
- `.github/workflows/deploy.yml` 的 `test` job 只做：
  - `pip install pytest pillow reportlab jsonschema coverage`（**不含 ruff/mypy**）；
  - `coverage run -m pytest` + `npm run test:e2e`。
- 结论：ruff/mypy 配置存在但从未在 CI 执行，等于"门禁只装了门框没装门"。

### 2.2 根因与影响

**根因**：质量工具配置与 CI 配置分属两次独立演进，中间缺少
"配置必须被 CI 引用"的一致性约束。

**影响**：

- 风格违规（未排序 import、unused variable、pyupgrade 机会）静默进入主分支；
- 类型错误（strict 模式下本可捕获的 assignment/arg-type/no-any-return）
  只能在本地偶尔手动运行时被发现；
- 对 Agent 生成代码尤其危险：本项目大量代码由 Agent 产出，
  没有机器门禁意味着每次都要靠人工 review 兜底。

### 2.3 修复方案

#### 步骤 1：本地基线检查（先确认存量债务规模）

```bash
cd /Users/qingyuan/Documents/zhangxinqi
ruff check teacher-console/ scripts/ 2>&1 | tail -5
mypy teacher-console/ 2>&1 | tail -5
```

根据输出规模选择策略：

- **违规 < 50 条**：直接修复后上 CI（推荐，一次到位）；
- **违规 50–500 条**：先上 CI 但允许 `continue-on-error: true`
  一周观察期，再转强制；
- **违规 > 500 条**：说明配置过严，先放宽规则集再上门禁
  （例如先只开 `E,F`，后续逐步加 `I,UP,W`）。

#### 步骤 2：ruff 自动修复可自动项

```bash
ruff check --fix teacher-console/          # 修 F401/I001/UP 等可自动修复项
ruff format --check teacher-console/       # 只检查不强制格式化（可选）
```

注意 `pyproject.toml` 已豁免 `teacher-console/tests/*.py` 的
`F401/F811`，无需担心测试文件噪音。

#### 步骤 3：修改 deploy.yml

在 `Install dependencies` 步骤加入工具，并在 pytest 之前插入两个检查步骤
（失败即阻断，置于最前可最快反馈）：

```yaml
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install pytest pillow reportlab jsonschema coverage ruff mypy

      - name: Lint with ruff
        run: ruff check teacher-console/

      - name: Type check with mypy
        run: mypy teacher-console/
```

> **注意**：mypy strict 全量扫描 `teacher-console/` 可能报出
> scripts/ 目录内模块的导入错误。如遇 `module not found`，
> 先确认 `mypy_path` 已包含所需路径（pyproject.toml 已配置
> `teacher-console` 和 skills scripts 路径），仍失败则按
> pyproject 现有 overrides 模式追加模块豁免，而不是关掉 strict。

#### 步骤 4（可选增强）：本地 pre-push 兜底

CI 是最终门禁，但本地提前拦截能减少 Agent 往返。可加一个
不依赖 git hooks 的显式脚本：

```bash
# scripts/lint.sh
#!/bin/sh
set -e
ruff check teacher-console/
mypy teacher-console/
echo "lint passed"
```

并在 AGENTS.md 的提交前检查清单中引用它。

### 2.4 验收标准

- [ ] `deploy.yml` 含 `ruff check teacher-console/` 步骤且无 `continue-on-error`；
- [ ] `deploy.yml` 含 `mypy teacher-console/` 步骤；
- [ ] `pip install` 行包含 `ruff mypy`；
- [ ] 本地两条命令均 exit 0；
- [ ] 故意引入一个 `unused import` 提交到分支，确认 CI 变红。

**工作量**：半天（若存量违规 < 50 条）至 2 天（需批量清理时）。

---

## 三、Finding 2：6 个新增核心模块缺少日志可观测性

**ID**: `new-modules-no-logging` · **严重度**: Medium · **维度**: 改动验证

### 3.1 现状证据

- `teacher-console/log.py` 提供完整的共享日志设施：
  - `logger`（`wuli` 命名空间）、`get_logger(name)` 子 logger；
  - `TraceContext(trace_id)` 上下文管理器，按线程绑定
    `pipeline_trace_id`，所有 handler 通过 `_TraceFilter` 注入 `record.trace_id`；
  - 格式：`%(asctime)s [%(levelname)s] [%(trace_id)s] %(message)s`；
- 已接入：`agent_gateway.py`、`agent_jobs.py`、`failure_intelligence.py`、`server.py`；
- **未接入（0 处日志导入）**：
  1. `evidence_agent.py` — 证据代理影子评估；
  2. `cognitive_loop.py` — 认知循环；
  3. `correctness_faults.py` — 正确性故障记录；
  4. `w3_pipeline.py` — W3 推理主流程；
  5. `claim_ledger.py` — Claim 证据链策略；
  6. `proof_aggregation.py` — 证明聚合。

这 6 个模块恰好构成 W3 推理链的核心路径——最复杂的流程恰恰没有观测。

### 3.2 根因与影响

**根因**：新模块按功能独立开发，没有"新模块必须接入共享日志"的
约定或检查；AGENTS.md 也没有这条规范。

**影响**：

- W3 路由失败、证据评估超时、仲裁异常时，只能靠 stdout 打印或猜测；
- 无法用 trace_id 把一次题目处理的全链路（gateway → W3 → 证据 → 渲染）
  串起来，跨模块故障定位成本高；
- failure_intelligence 的失败归因缺少上游日志佐证。

### 3.3 修复方案

#### 统一接入模式（每个模块相同）

**模块顶部**：

```python
from log import get_logger, TraceContext

logger = get_logger("w3_pipeline")   # 用模块名做子 logger 名
```

**流水线入口函数**（每个模块选 1–2 个对外入口）：

```python
def run_pipeline(entry_id: str, ...):
    with TraceContext() as ctx:
        ctx.info("stage=w3 entry_id=%s status=started", entry_id)
        try:
            result = _do_work(entry_id)
        except Exception:
            ctx.exception("stage=w3 entry_id=%s status=failed", entry_id)
            raise
        ctx.info("stage=w3 entry_id=%s status=completed route=%s",
                 entry_id, result.get("route"))
        return result
```

**内部决策点**用模块 logger（继承外层 trace_id，同线程自动关联）：

```python
logger.info("stage=w3.arbitrate candidates=%d decision=%s", n, decision)
logger.warning("stage=correctness.faults fault_type=%s count=%d", ft, c)
```

#### 分模块要点

| 模块 | 入口函数建议 | 关键决策点日志 |
|------|-------------|---------------|
| `w3_pipeline.py` | W3 主 run 函数 | 路由决策、stage 转换、超时/回跳（backjump） |
| `evidence_agent.py` | 影子评估入口 | 候选数、验收门结果、覆盖门结果 |
| `cognitive_loop.py` | 循环主入口 | 每轮迭代结论、终止原因 |
| `correctness_faults.py` | fault 记录入口 | fault 类型、严重度、关联 entry |
| `claim_ledger.py` | claim 写入/查询入口 | claim 状态变迁 |
| `proof_aggregation.py` | 聚合入口 | 聚合输入数、裁决输出 |

> 具体函数名以各模块实际公开入口为准；`w3_pipeline` 与 `server` 之间
> 已有调用链时，优先复用 server 传入的 trace_id（`TraceContext(entry_trace_id)`），
> 避免一次处理产生两个不相关的 trace。

#### 日志内容规范

- **结构化键值**：沿用现有 `stage=xxx status=xxx entry_id=xxx` 风格，
  便于后续 grep/解析；
- **不记录敏感内容**：题干原文、答案全文不进日志（隐私门禁要求），
  只记 id、状态、计数、路由名；
- **异常用 `ctx.exception(...)`**：保留 traceback；
- **循环内高频路径用 `logger.debug`**：避免 INFO 洪水。

#### 防止回退：加一条约定 + 一个检查

1. 在 AGENTS.md 增加一行规范：
   > 新增 `teacher-console/` 流水线模块必须接入 `log.py` 的
   > `get_logger`/`TraceContext`，入口函数绑定 trace_id。
2. （可选）在 tests 中加一个轻量守护测试：

```python
# teacher-console/tests/test_logging_coverage.py
import re
from pathlib import Path

PIPELINE_MODULES = [
    "evidence_agent", "cognitive_loop", "correctness_faults",
    "w3_pipeline", "claim_ledger", "proof_aggregation",
]

def test_pipeline_modules_import_shared_logger():
    root = Path(__file__).resolve().parents[1]
    for mod in PIPELINE_MODULES:
        text = (root / f"{mod}.py").read_text(encoding="utf-8")
        assert re.search(r"from log import", text), f"{mod} 未接入共享日志"
```

### 3.4 验收标准

- [ ] 6 个模块均有 `from log import` 且至少一处 `logger`/`ctx` 调用；
- [ ] 跑一次真实 W3 流程，日志可用 `grep trace_id` 串联出完整链路；
- [ ] 全量 pytest 通过（日志不改变行为）；
- [ ] 守护测试（若添加）通过。

**工作量**：1 天（每模块约 30–60 分钟，含测试回归）。

---

## 四、Finding 3：项目缺少标准依赖声明

**ID**: `no-standard-dependency-spec` · **严重度**: Low · **维度**: 可控执行

### 4.1 现状证据

- 无 `requirements.txt` / `setup.py` / `setup.cfg`；
- `pyproject.toml` 只有 `[tool.*]` 工具配置，无 `[project]` 段；
- CI 手工维护依赖列表：`pip install pytest pillow reportlab jsonschema coverage`；
- 运行时依赖分散在代码 import 中（如 `reportlab`、`PIL`、`jsonschema`
  等第三方库），与 CI 列表的一致性靠人工维护。

### 4.2 根因与影响

**根因**：项目以脚本方式生长，从未做"可安装单元"的正式化。

**影响**：

- 新环境（新机器、新 Agent 沙箱、评审者）无法一键复现；
- CI 依赖列表与实际 import 漂移时无人察觉（已有迹象：CI 未装 ruff/mypy
  正是同类漂移）；
- E2E/评测脚本的隐式依赖（若有 playwright 之外的 Python 侧依赖）不可见。

### 4.3 修复方案

#### 方案 A（推荐）：pyproject.toml 加 `[project]` 段

```toml
[project]
name = "wuli-teacher-console"
version = "0.1.0"
description = "悟理 · AI 全流程教学助教平台（教师控制台）"
requires-python = ">=3.9"
dependencies = [
    "pillow",
    "reportlab",
    "jsonschema",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "coverage",
    "ruff",
    "mypy",
]
```

依赖清单提取方法（务必实测，勿凭记忆）：

```bash
# 1) 列出所有第三方 import（排除标准库与项目内模块）
grep -rh "^import \|^from " teacher-console/*.py | sort -u

# 2) 在干净 venv 中逐个验证
python3 -m venv /tmp/wuli-depcheck && source /tmp/wuli-depcheck/bin/activate
pip install pillow reportlab jsonschema
python -c "import server" 2>&1   # 在 teacher-console/ 目录下验证
```

> 注意 `requires-python`：pyproject 中 ruff/mypy 目标为 py39，
> 但 CI 用 3.11。声明 `>=3.9` 兼容两者；若想收敛，建议统一到 3.11
> 并同步更新 `[tool.ruff] target-version` 与 `[tool.mypy] python_version`
> （见 §六 横向建议）。

#### 方案 B（最小改动）：requirements 文件

若不想让项目变成"可安装包"（无 build 意图），可退而求其次：

```
# requirements.txt —— 运行时
pillow
reportlab
jsonschema

# requirements-dev.txt —— 开发与 CI
-r requirements.txt
pytest
coverage
ruff
mypy
```

deploy.yml 改为：`pip install -r requirements-dev.txt`

**选择建议**：方案 A 更标准（`pip install -e .` 可用），方案 B 改动最小。
两者选一即可，不要同时维护两套。

#### 同步更新 CI

无论选哪个方案，deploy.yml 的 Install 步骤都应改为引用声明文件，
消除第二份手工清单：

```yaml
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"        # 方案 A
          # 或 pip install -r requirements-dev.txt   # 方案 B
```

### 4.4 验收标准

- [ ] 干净 venv 中 `pip install -e ".[dev]"`（或 `-r requirements-dev.txt`）成功；
- [ ] 安装后 `python3 teacher-console/server.py` 启动无 ImportError；
- [ ] pytest + ruff + mypy 均可运行；
- [ ] deploy.yml 不再出现手工依赖列表。

**工作量**：半天（含干净环境验证）。

---

## 五、Finding 4：8 个已文档化 Skill 不被 Qoder 原生发现

**ID**: `qoder-skill-discovery-gap` · **严重度**: Low · **维度**: 可控执行

### 5.1 现状证据

- 磁盘上有 9 份 SKILL.md，分布于 `.agents/skills/`（8 个）与
  `.claude/skills/`（9 个，多一个 `decompose-physics-problem`）；
- Qoder asset-baseline inventory 报告 **Skills: 0**（8 个候选资产、
  0 个被观测、1 个未解析名称匹配）；
- 当前 Skill 触发完全依赖 AGENTS.md 的自然语言路由 + Agent 自觉读取；
- Session 证据：neat-freak 曾被调用 1 次，但记录为 unscoped observation。

### 5.2 根因与影响

**根因**：`.agents/skills/` 与 `.claude/skills/` 是 Claude Code 的发现路径，
不是 Qoder 的原生发现路径；项目从未为 Qoder 做过 Skill 注册。

**影响**：

- 路由可靠性依赖模型对 AGENTS.md 的注意力，不如结构化发现稳定；
- 两个目录各维护一份 SKILL.md，存在漂移风险
  （`decompose-physics-problem` 已只在 `.claude/skills/` 出现）；
- Skill 调用无法被 Qoder 的会话统计覆盖，Learning Capture 更难评估。

### 5.3 修复方案

#### 步骤 1：确认 Qoder 项目级 Skill 发现路径

Qoder 原生项目级 Skill 约定路径为 `.qoder/skills/<skill-name>/SKILL.md`
（本项目 `.qoder/` 目录已存在但无 `skills/` 子目录）。

#### 步骤 2：用符号链接统一三处，避免三份拷贝

本项目已有 `AGENTS.md → CLAUDE.md` 符号链接先例，同样手法：

```bash
mkdir -p .qoder/skills
cd .qoder/skills
for s in build-physics-simulator complex-process-decomposer darwin-skill \
         graphify grill-me manage-student-error-library neat-freak scout; do
  ln -s ../../.claude/skills/$s $s
done
```

以 `.claude/skills/` 为单一事实源（它比 `.agents/skills/` 多一个 skill）。
若符号链接在某个工具链中不被跟随，退化为拷贝脚本
（`scripts/sync-skills.sh`）并纳入 neat-freak 收尾审计项。

#### 步骤 3：处理 `.agents/skills/` 的重复

确认 `.agents/skills/` 是否还有其他工具链消费；若没有，将其也改为
指向 `.claude/skills/` 的符号链接或直接移除，杜绝双份维护。

#### 步骤 4：验证与路由确认

```bash
# 重新跑基线，确认 Skills 计数 > 0
node <better-harness-cli> coding-agent-practices asset-baseline qoder \
  --workspace /Users/qingyuan/Documents/zhangxinqi --language zh-CN --json
```

同时保留 AGENTS.md 的 Skill 职责表（人类可读路由），但注明
"Skill 已通过 `.qoder/skills/` 注册，结构化发现优先"。

### 5.4 验收标准

- [ ] asset-baseline inventory 报告 Skills ≥ 8；
- [ ] `ls -la .qoder/skills/` 显示 8 个可解析的 SKILL.md；
- [ ] 磁盘上每个 Skill 只有一份真实内容（其余为链接）；
- [ ] 至少抽查 2 个 Skill（如 manage-student-error-library、neat-freak）
      可被正常触发。

**工作量**：2 小时。

---

## 六、Finding 5：186 个未提交变更缺少恢复边界

**ID**: `uncommitted-changes-no-recovery` · **严重度**: Medium · **维度**: 可靠交付

### 6.1 现状证据

- `git status`：约 114+ 条变更记录（含未跟踪目录展开后 186 个文件、
  33723 行变更），diffSeverity=critical，diffScore=100；
- 变更横跨：新增核心模块（`evidence_agent.py`、`cognitive_loop.py`、
  `correctness_faults.py` 等及测试）、文档归档（docs/archive、docs/reports）、
  配置（pyproject、deploy.yml）、Skill 目录、`.qoder/` 分析产物等；
- 所有工作悬空在一个提交点之上，任何误操作（误 reset、误 checkout、
  Agent 误覆盖）都无法恢复。

### 6.2 根因与影响

**根因**：长周期多任务并行开发，缺少"每完成一个逻辑单元即提交"的节奏；
AGENTS.md 未定义提交策略。

**影响**：

- 无恢复点 = 可靠交付维度最低分项（45）的直接原因；
- 后续所有修复（finding 1–4）都混入这 33723 行后，无法独立评审与回滚；
- core-change-watch 无法区分"核心代码变更"与"文档噪音"，
  后续 Harness 分析质量也会受损。

### 6.3 修复方案（先做此项！）

#### 步骤 0：安全备份（防呆）

```bash
cd /Users/qingyuan/Documents/zhangxinqi
git stash list                          # 确认没有遗忘的 stash
git tag backup/pre-split-2026-08-03     # 当前 HEAD 打安全标签
```

#### 步骤 1：决定未跟踪产物的去留

先分类不该进仓库的内容（一次性决策）：

| 路径 | 建议 |
|------|------|
| `.qoder/better-harness/` 分析产物 | 加入 `.gitignore`（机器生成报告） |
| `test-results/`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/` | 确认已在 `.gitignore` |
| `error-collection/` 学生题目图片 | 视隐私策略：样例保留、真实题目入库前须走公开发布门禁，建议 ignore 或 LFS |
| `graphify-out/` 每日快照 | 已有日期目录堆积，建议只提交 `graph.json`/`GRAPH_REPORT.md`，快照目录 ignore |

#### 步骤 2：按逻辑分组提交（建议 5 组）

```bash
# 组 1：.gitignore 与仓库卫生（先做，让后续 add 干净）
git add .gitignore
git commit -m "chore: 忽略机器生成产物与分析缓存"

# 组 2：新增核心模块 + 对应测试（最大的一组，可再按模块细拆）
git add teacher-console/evidence_agent.py teacher-console/cognitive_loop.py \
        teacher-console/correctness_faults.py teacher-console/w3_pipeline.py \
        teacher-console/claim_ledger.py teacher-console/proof_aggregation.py \
        teacher-console/tests/
git commit -m "feat: W3 证据链与认知循环核心模块及测试"

# 组 3：文档与归档
git add docs/
git commit -m "docs: 归档执行工作树与评测报告"

# 组 4：Skill 目录
git add .claude/skills/ .agents/skills/
git commit -m "chore: 同步 Skill 目录"

# 组 5：CI/配置
git add pyproject.toml .github/workflows/ package.json package-lock.json
git commit -m "build: 更新质量工具配置与 CI"
```

每组提交后立即跑回归：

```bash
coverage run --source=teacher-console -m pytest teacher-console/tests/ -q
```

#### 步骤 3：提交后核验

```bash
git log --oneline | head -6          # 确认分组提交存在
git status --short | wc -l            # 应接近 0
```

### 6.4 长效机制（防止复发）

在 AGENTS.md 增加提交纪律（2 条即可）：

> - 每个逻辑变更单元（一个模块+其测试、一份文档、一项配置）完成后立即提交；
> - 单次会话结束前 `git status` 必须为空或有明确说明。

可配合 neat-freak 收尾审计把这条纳入常规检查。

### 6.5 验收标准

- [ ] `git status --short` 干净（或仅剩有意忽略项）；
- [ ] 每个逻辑组可单独 `git revert`；
- [ ] 全量测试通过；
- [ ] `backup/pre-split-2026-08-03` 标签保留至少一周。

**工作量**：2–4 小时（大部分是分组决策与回归验证）。

---

## 七、横向建议（超出 5 个 findings 的增益项）

### 7.1 Python 版本对齐

pyproject 的 ruff/mypy 目标是 **py39**，CI 实际用 **3.11**。
建议统一到 3.11：更新 `[tool.ruff] target-version`、
`[tool.mypy] python_version` 与 `[project] requires-python`。
不一致会让 `UP`（pyupgrade）规则错过 3.10+ 语法机会，
也会让本地与 CI 的类型检查结果出现环境差异。

### 7.2 经验沉淀（Learning Capture 得分 35 的改善路径）

Learning Capture 不是写文档能解决的，需要**可比观察窗口**：

1. **短期（本日起）**：每个开发会话结束执行 neat-freak 收尾，
   把"本次改了什么、学到什么"显式落到 docs/CHANGES.md 或项目记忆；
2. **中期（2–4 周）**：保持 finding 1–4 的修复节奏，让下一轮
   `/better-harness` 运行时存在可对比的改进 Episode；
3. **长期**：当 Episode 数量 ≥ 2 个可比窗口后，Learning Capture
   才能评估"哪些实践真正减少了返工"。

### 7.3 AGENTS.md 增加三条运行层规范

```markdown
## 运行层规范
- 新增 teacher-console/ 流水线模块必须接入 log.py 共享日志（trace_id 绑定）；
- pyproject.toml 中的质量工具配置必须被 CI 引用，新增工具同步加 CI 步骤；
- 每个逻辑变更单元完成后立即提交，禁止跨会话累积未提交变更。
```

这三条分别对应 finding 2、1、5 的防复发，把一次性修复变成结构性约束。

---

## 八、执行路线图

```text
第 1 天上午   Finding 5  建立恢复边界（分组提交）          [阻塞项，最先做]
第 1 天下午   Finding 1  本地 ruff/mypy 清零 + CI 接通     [保护后续改动]
第 2 天上午   Finding 2  6 模块接入共享日志 + 守护测试
第 2 天下午   Finding 3  依赖声明 + CI 改用声明文件
第 2 天晚间   Finding 4  .qoder/skills 注册（2 小时）
第 2 天收尾   横向建议   AGENTS.md 三条规范 + Python 版本对齐决策
第 3 天      复跑 /better-harness 对比改进前后得分
```

**预期收益**：改动验证 55→75+（门禁生效+可观测），可靠交付 45→70+
（恢复边界+提交纪律），可控执行 60→75（依赖复现+Skill 发现）。

**风险提示**：

- Finding 1 的 mypy strict 全量启用可能暴露存量类型问题超预期，
  按 §2.3 步骤 1 的分级策略处理，不要为赶进度关掉 strict；
- Finding 5 分组提交时若测试在中间状态失败，以安全标签为准回退重组，
  不要带病提交；
- Finding 4 符号链接方案需验证所有消费方（Claude Code / Qoder / 备份脚本）
  都能跟随链接，任一不兼容即改拷贝脚本。
