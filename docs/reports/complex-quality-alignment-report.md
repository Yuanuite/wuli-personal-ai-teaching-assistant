# 复杂题质量对齐验收报告（Work-Tree D4）

- 日期：2026-08-04
- 对应执行树：`docs/core-failure-attribution-repair-work-tree.md` § D4「7 月样本对照集验收」
- 验收场景：`teacher-console/e2e/analysis-core-quality-alignment.e2e.mjs`（已纳入 `npm run test:e2e`，第 24 个场景）
- 结果：**5/5 黄金题六维断言全通过，claim 验证全部 canonical，全量 24 个 E2E 场景通过**

## 1. 黄金对照集

从 `student-site/catalog.json`（只读参照，不修改公开发布产物）选 `difficulty.score >= 60` 的 7 月教师审核样本，落档于 `teacher-console/tests/fixtures/quality-alignment.json`：

| 题目 ID | 标题 | 难度分 |
|---------|------|--------|
| question-1edcc8241bf2 | 方波电场与匀强磁场中的带电粒子运动 | 66 |
| question-ccdf4cfc702c | 交替电场与磁场中带电粒子的分段运动 | 80 |
| question-4a1ddcd4877a | 三维复合场中电子的类平抛、螺旋运动与共圆心条件 | 82 |
| question-83c1f4acef52 | 两种材料圆环的感应电动势与端电压 | 67 |
| question-212ecbad04d3 | 带电粒子在同心圆复合场中的运动 | 67 |

E2E 重放方式：从每题 `content.md` 提取题干（在 `---` 分隔符前截断、剥离图片与 asset 引用），
经 `run-upload → approve-source → analyze(economy)` 走完整 D1-D3 链；5 题全部经
`complexity_screen` 判定 `decompose`（契约 `wuli.core-rich.v2`）。

## 2. 六维对照清单

| ID | 维度 | 断言内容 |
|----|------|----------|
| five-sections | 五段齐全且内容针对本题 | 答案速览/一眼识别/详细解答/易错点/30 秒自测齐全；每条 claim 的 `final_answer` 与 `key_relations` 逐字出现 |
| cross-target-references | 跨小问引用正确 | 详细解答段内编号步骤 1-5 个，每个 claim 结论在段内且与 claim id 一一对应 |
| paired-pitfalls | 易错点成对 | 每条同时含「错误表现」与「纠正策略」，条数覆盖全部 claim |
| conditioned-conclusions | 二级结论带适用条件 | 一眼识别段含二级结论且显式标注适用条件 |
| static-svg | 静态 SVG 图示存在 | 复杂题自动排队的 diagram 完成，`assets/explanatory.svg` 存在 |
| dimension-checks | 量纲/适用条件核对项列出 | 30 秒自测段含量纲核对，且逐 target 列出核对项 |

## 3. 验收结果

5 题全部通过六维断言，且 D3 链验收指标一致：

- `claim_verification.status == completed`，`answer_status == canonical`；
- verifier 与 solver 身份隔离（`e2e-claude-verifier` ≠ `e2e-claude-solver`）；
- 每题 `agent_call_count == 3`（rich 求解 + 自动 diagram + 独立 claim 验证）。

配套测试基线（提交前复跑）：

- `pytest teacher-console/tests`：793 passed；
- `ruff check teacher-console`：All checks passed；
- `mypy teacher-console`（仓库根）：Success, no issues found in 178 source files；
- `python3 teacher-console/e2e/run_e2e.py`：All 24 E2E scenarios passed。

## 4. 排查过程沉淀

重放真实 7 月题干时暴露两个 fake adapter 与门禁的接缝问题，已修复（见提交历史）：

1. **固定符号答案被物理门禁拒绝**：fake adapter 固定输出 `$B^*=3mv_0/(qd)$`，而黄金题干
   使用 `$U_0$`/`$t_0$` 等符号，`v0` 未在题干或推导中出现，被确定性质量门判
   `symbol-undefined@Q1/Q2/Q3`。修复：fake adapter 从题干提取 `$X_0$` 形式符号动态构造
   答案，并在推导首句显式声明全部符号；无 `$X_0$` 时回退 `$v_0$` 并在推导中定义。
   （`e2e/` 与 `tests/fixtures/` 两份 fake adapter 同步修改。）
2. **题干提取泄漏解析段图片引用**：`## 题干` 匹配会越过 `---` 吞进已发布解析段的
   `![...](assets/asset-1.svg)`，候选校验报 `problem.md: missing image assets/asset-1.svg`。
   修复：提取逻辑在 `---`/一级标题处截断并剥离全部图片引用。
