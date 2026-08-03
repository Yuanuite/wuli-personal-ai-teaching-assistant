# Ruff/Mypy 存量清理原子任务树

> **目标**：将 `ruff check teacher-console/` 和 `mypy teacher-console/` 的存量违规清零，
> 清零后在 `.github/workflows/deploy.yml` 中去掉 `continue-on-error: true`，升级为硬门禁。
>
> **基线快照（2026-08-03）**：ruff 247 errors / mypy 305 errors = **552 total**
>
> **关联 Issue**：[#17 — CI: Add ruff and mypy checks to test job in deploy.yml](https://github.com/Yuanuite/wuli-personal-ai-teaching-assistant/issues/17)

---

## 全局约束

| 约束 | 说明 |
|------|------|
| 工作目录 | 仓库根 `/Users/qingyuan/Documents/zhangxinqi` |
| 检查范围 | `teacher-console/`（源码 + 测试 + 脚本 + e2e fixtures） |
| 配置文件 | `pyproject.toml`（`[tool.ruff]` 与 `[tool.mypy]` 段，禁止修改配置） |
| 禁止事项 | 禁止用 `# type: ignore` 或 `# noqa` 压制；禁止放宽 `pyproject.toml` 规则 |
| 回归验证 | 每个任务完成后必须跑 `pytest teacher-console/tests/ -q` 全绿 |
| 提交粒度 | 每个 Task 一个 commit；commit message 前缀 `lint:` |

---

## 任务依赖图

```
T1 (ruff auto-fix)  ──┐
                      ├──→ T2 (E501 行长) ──→ T4 (arg-type) ──→ T7 (升级硬门禁)
T3 (E731/F841)  ──────┘                     ├──→ T5 (index)
                                             ├──→ T6 (dict-item)
                                             └──→ T8 (剩余 mypy)
```

- T1 必须最先执行（自动排序 import 会改变行号，后续手动修复需基于新行号）
- T2 依赖 T1（行长修复可能涉及 import 行）
- T3 与 T2 可并行但建议 T1 后执行
- T4–T8 互相独立，可并行分配给不同 agent
- T7（升级硬门禁）必须在 T1–T6 全部完成后执行

---

## T1 — Ruff 自动修复（77 errors）

| 字段 | 值 |
|------|-----|
| 违规码 | `I001`（73）+ `UP012`（3）+ `F401`（1）= 77 |
| 风险 | 低 — 仅排序 import 和删除未使用 import |
| 预计耗时 | 1 分钟 |

### 步骤

```bash
cd /Users/qingyuan/Documents/zhangxinqi
ruff check teacher-console/ --fix
```

### 验证

```bash
# 确认这三类归零
ruff check teacher-console/ --select I001,UP012,F401
# 输出必须为：Found 0 errors.

# 回归测试
pytest teacher-console/tests/ -q
# 必须全绿
```

### 注意

- `ruff --fix` 会重排 import 顺序，**导致后续文件行号变化**；T2 及所有 mypy 任务必须在此之后基于新行号工作。
- 如果 `--fix` 引入语法错误（极少见），用 `git diff` 定位并手动修正。

---

## T2 — Ruff E501 行太长手动修复（165 errors）

| 字段 | 值 |
|------|-----|
| 违规码 | `E501` line-too-long（>120 字符） |
| 风险 | 低 — 纯格式，不改逻辑 |
| 预计耗时 | 30–45 分钟 |

### 高频文件（Top 5）

| 文件 | E501 数 |
|------|---------|
| `teacher-console/physics_diagram.py` | 31 |
| `teacher-console/scripts/build_evidence_fresh_holdout_v2.py` | 21 |
| `teacher-console/tests/test_physics_diagram.py` | 19 |
| `teacher-console/tests/test_analysis_run_report.py` | 17 |
| `teacher-console/scripts/build_cpho_web_eval_report.py` | 11 |

### 修复策略

1. **字符串过长**：在运算符处断行，用括号隐式续行
2. **函数签名过长**：每参数一行
3. **注释过长**：在空格处折断
4. **URL 或长路径**：用 `# noqa: E501` 标注（仅限不可折断的情况）

### 验证

```bash
ruff check teacher-console/ --select E501
# 输出必须为：Found 0 errors.
pytest teacher-console/tests/ -q
```

---

## T3 — Ruff E731 + F841 手动修复（5 errors）

| 字段 | 值 |
|------|-----|
| 违规码 | `E731`（4 lambda 赋值）+ `F841`（1 未使用变量） |
| 风险 | 低 |
| 预计耗时 | 5 分钟 |

### 步骤

```bash
# 定位所有 E731
ruff check teacher-console/ --select E731 --output-format concise
# 定位 F841
ruff check teacher-console/ --select F841 --output-format concise
```

### 修复策略

- `E731`：将 `f = lambda x: x + 1` 改为 `def f(x): return x + 1`
- `F841`：删除未使用的变量，或加 `_` 前缀表示故意不用

### 验证

```bash
ruff check teacher-console/ --select E731,F841
# Found 0 errors.
pytest teacher-console/tests/ -q
```

---

## T4 — Mypy arg-type 修复（115 errors）⚠️ 高风险

| 字段 | 值 |
|------|-----|
| 违规码 | `arg-type` — 传入参数类型与函数签名不匹配 |
| 风险 | **高** — 可能隐藏运行时 TypeError |
| 预计耗时 | 2–4 小时 |

### 高频文件（Top 5）

| 文件 | arg-type 数 |
|------|------------|
| `teacher-console/server.py` | 最多（38 total mypy） |
| `teacher-console/tests/test_w3_pipeline.py` | 26 total |
| `teacher-console/w3_pipeline.py` | 14 total |
| `teacher-console/scripts/agent_batch_benchmark.py` | 14 total |
| `teacher-console/agent_gateway.py` | 12 total |

### 典型模式与修复策略

**模式 A：Optional 传给非 Optional 参数**
```python
# 错误：函数签名要 str，但传了 Optional[str]
def foo(name: str): ...
name: Optional[str] = get_name()
foo(name)  # ← arg-type

# 修复：加判空守卫
if name is None:
    raise ValueError("name is required")
foo(name)  # 现在 mypy 知道 name 是 str
```

**模式 B：Collection[str] 传给 str 参数**
```python
# 错误：函数要 str，传了 list[str]
def normalize(key: str, payload): ...
keys = ["a", "b"]
normalize(keys, payload)  # ← arg-type

# 修复：传单个元素，或修改函数签名接收 Sequence[str]
normalize(keys[0], payload)
```

**模式 C：StubGateway 传给 AgentGateway 参数（测试文件）**
```python
# 测试中用 _StubGateway 模拟，但函数签名要 Optional[AgentGateway]
# 修复方案：让 _StubGateway 继承 AgentGateway，或用 typing.cast
```

### 验证

```bash
mypy teacher-console/ 2>&1 | grep -c "arg-type"
# 数字必须 < 115（逐步下降），最终 = 0
pytest teacher-console/tests/ -q
```

---

## T5 — Mypy index 修复（49 errors）⚠️ 高风险

| 字段 | 值 |
|------|-----|
| 违规码 | `index` — 对可能为 None 或不可索引的类型直接取下标 |
| 风险 | **高** — `TypeError: 'NoneType' object is not subscriptable` |
| 预计耗时 | 1–2 小时 |

### 典型模式与修复策略

```python
# 错误：返回值可能是 None，直接取下标
result: Optional[dict] = lookup()
value = result["key"]  # ← index error

# 修复 A：判空
result = lookup()
if result is not None:
    value = result["key"]
else:
    value = default

# 修复 B：用 dict.get 避免下标
value = result.get("key") if result else None
```

### 验证

```bash
mypy teacher-console/ 2>&1 | grep -c "\[index\]"
# 最终 = 0
pytest teacher-console/tests/ -q
```

---

## T6 — Mypy dict-item 修复（36 errors）

| 字段 | 值 |
|------|-----|
| 违规码 | `dict-item` — 字典键值类型与声明不匹配 |
| 风险 | 中 |
| 预计耗时 | 1 小时 |

### 典型模式

```python
# 错误：声明 dict[str, str] 但值可能是 list
d: dict[str, str] = {}
d["items"] = ["a", "b"]  # ← dict-item

# 修复：修正类型声明为 dict[str, list[str]]，或转换值类型
d: dict[str, list[str]] = {}
```

### 验证

```bash
mypy teacher-console/ 2>&1 | grep -c "\[dict-item\]"
# 最终 = 0
pytest teacher-console/tests/ -q
```

---

## T7 — 升级 CI 为硬门禁

| 字段 | 值 |
|------|-----|
| 前置条件 | T1–T6 全部完成，`ruff` 和 `mypy` 均输出 `Found 0 errors` |
| 文件 | `.github/workflows/deploy.yml` |
| 预计耗时 | 2 分钟 |

### 步骤

删除 `deploy.yml` 中两个步骤的 `continue-on-error: true` 行：

```yaml
# 修改前
      - name: Run ruff lint
        continue-on-error: true
        run: ruff check teacher-console/

      - name: Run mypy type check
        continue-on-error: true
        run: mypy teacher-console/

# 修改后
      - name: Run ruff lint
        run: ruff check teacher-console/

      - name: Run mypy type check
        run: mypy teacher-console/
```

### 验证

```bash
# 本地确认零违规（硬门禁的前提）
ruff check teacher-console/ && echo "RUFF OK"
mypy teacher-console/ && echo "MYPY OK"
pytest teacher-console/tests/ -q
```

确认以上三条全绿后，修改 `deploy.yml` 并提交。

---

## T8 — Mypy 剩余类别清理（61 errors）

| 违规码 | 数量 | 说明 |
|--------|------|------|
| `no-any-return` | 34 | 函数声明返回具体类型但实际返回 `Any` |
| `assignment` | 18 | 变量类型与赋值不匹配 |
| `misc` | 14 | 杂项类型不兼容 |
| `var-annotated` | 13 | 变量需要显式类型注解 |
| `operator` | 7 | 操作符两边类型不兼容（如 `bytes + str`） |
| `call-overload` | 5 | 调用重载函数时参数不匹配 |
| `valid-type` | 4 | 类型注解本身无效 |
| `return-value` | 3 | 返回值类型与声明不匹配 |
| `type-var` | 2 | 泛型类型变量问题 |
| `no-redef` | 2 | 变量重复定义 |
| `method-assign` | 1 | 给方法重新赋值 |
| `call-arg` | 1 | 缺少必需参数 |

### 修复策略

- `no-any-return`：给被调用函数加返回类型注解，或在返回处用 `cast()`
- `assignment`：修正变量声明类型或转换赋值
- `var-annotated`：加 `: dict[str, Any]` 等显式注解
- `operator`：统一 `bytes`/`str`，用 `.encode()`/`.decode()` 转换
- 其余逐个看错误信息修复

### 验证

```bash
mypy teacher-console/ 2>&1 | tail -3
# 最后一行必须是：Success: no issues found in N source files
pytest teacher-console/tests/ -q
```

---

## 执行检查清单

每个 Task 完成后逐项打勾：

```
[ ] ruff/mypy 对应违规码归零
[ ] pytest teacher-console/tests/ -q 全绿
[ ] git add + commit（前缀 lint:）
[ ] 更新本文档底部进度表
```

## 进度表

| Task | 违规码 | 基线 | 当前 | 状态 |
|------|--------|------|------|------|
| T1 | I001,UP012,F401 | 77 | 77 | ⬜ 待执行 |
| T2 | E501 | 165 | 165 | ⬜ 待执行 |
| T3 | E731,F841 | 5 | 5 | ⬜ 待执行 |
| T4 | arg-type | 115 | 115 | ⬜ 待执行 |
| T5 | index | 49 | 49 | ⬜ 待执行 |
| T6 | dict-item | 36 | 36 | ⬜ 待执行 |
| T8 | 剩余 mypy | 61 | 61 | ⬜ 待执行 |
| T7 | 升级硬门禁 | — | — | ⬜ 阻塞中 |
| **合计** | | **552** | **552** | |
