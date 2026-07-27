# W3 自适应推理与交叉验证

W3 解决的不是“再多召回几条”，而是复杂题中目标遗漏、物理阶段混淆、首次/唯一/
临界条件误判，以及候选答案缺少可复算关系的问题。W2 仍是生产基线；W3 在独立
holdout 达到准确率不退化门槛前只运行影子模式。

当前完成状态、五道新题到达后的固定命令顺序、生产灰度和最终回滚验收统一见
[`rag-completion-work-tree.md`](rag-completion-work-tree.md)；本文只维护 W3 契约与
实验事实。

## 固定流程

```text
题干
→ 确定性复杂度初筛
  ├─ 低结构风险：保持 W2
  └─ 复杂题：
     problem.decompose 双层蓝图
     → 3 路默认、最多 5 路定向召回
     → W2 evidence-set-v2 统一选集
     → Solver A 结构化求解
     → 目标级风险与预期收益
       ├─ 收益≤0：确定性检查
       └─ 收益>0：独立验证器复算
          └─ 极高风险或冲突：盲解 Solver B
             └─ 按证据、条件与可复算关系仲裁
     → 单一推荐结果 + 最多 2 张教师核对卡
```

外部调度器仍只看到一个 `analysis.generate` 作业。拆题、求解、验证和仲裁是该作业
内部的结构化阶段，不新增模型 capability，也不绕过同题单任务锁。

## 两层蓝图

- `physical_stages`：真实发生的物理过程、进入/退出条件、跨阶段继承的状态；
- `reasoning_steps`：建模、列式、分类、排除、复算等求解操作；
- 正式解题蓝图不包含难度评分字段；`cognitive_operation`、`knowledge_units` 和
  `type_distance` 只存在于 `difficulty-shadow-v1` 成对实验契约，实验输出在进入
  检索、Solver、验证器和仲裁前必须剥离这些标注；
- `stage_step_links`：只表达两层之间的对应关系，不把解题流程冒充物理过程；
- `verification_obligations`：每个题目目标至少绑定一项必须复核的断言；
- `retrieval_needs`：按目标/阶段聚合的方法需求，而不是把整道长题只检索一次。

蓝图是规划提示，不是答案真源。Solver 可以标记 `revised-equivalent` 并给出等价修订；
验证器检查的是结论与关系，不检查答案是否逐字照抄蓝图。

难度评分是 W3 的下游消费者，不是 W3 的生成约束。正式评分优先投影 Solver 的
`supporting_relations`、验证器的 `decisive_checks` 和仲裁决定性关系，再按固定知识单元
注册表确定性去重；没有解后证据时才回退蓝图关系。影子评分契约必须通过同题、同模型、
同参数的成对非退化评测，才能申请并入正式蓝图契约。

## 召回、验证与隐私边界

`build_blueprint_evidence()` 默认执行优先级最高的 3 个需求；后续需求只有在增加目标
覆盖或新候选时才继续，最多 5 路，连续两路无边际收益就停止。所有候选最终仍经过
W2 的精度门禁、去重、冲突阻断和统一字符预算。

独立验证器不读取历史答案正文。`verification_evidence_view()` 会删除标题、命中片段、
条目/路径标识，只保留知识点、方法、带条件二级结论和证据冲突摘要。验证器只获得
当前题干、蓝图、待验证目标及其可复算关系。

Solver B 不是常驻调用：仅当目标风险达到 `0.9` 的挑战阈值，或验证器发现事实冲突时
启动，而且只接收命中的目标子蓝图，不重算其余低风险目标。
仲裁禁止多数投票，必须给出决定性关系；仍无法确定时内部标记教师核对，但教师端
不显示 `unresolved`、角色争论或长日志，只给最多两张行动卡。

行动卡默认收起。教师点开具体冲突后，可查看经过限长和角色中性化处理的主候选结论、
独立复算结论、差异候选、仲裁采用结论与决定性关系；每段支持复制，候选结论还可一键
填入“修改意见”。该操作只减少人工转写，不会直接覆盖答案，也不会绕过教师复核。

结构化结论不会再触发一次纯排版模型调用。`render_recommended_student_solution()`
确定性生成包含“答案速览、一眼识别、详细解答、易错点、30 秒自测”的单一候选，
并复用现有高中方法/五步上限检查。每个内部阶段按题干、上下文、契约和模型摘要写入
私有检查点；失败后重试只复用完全相同输入的阶段，避免重复消耗。

## 影子门禁与命令

冻结评测集：

```bash
python3 -B teacher-console/scripts/w3_shadow_benchmark.py seed --holdout-count 5
```

运行单题影子重放（不改正式答案）：

```bash
python3 -B teacher-console/scripts/w3_shadow_run.py <entry-id> \
  --routing-tier expert --model-id codex-visualization
```

每题由教师按 `labels/<entry-id>.json` 做目标级判定；然后执行：

```bash
python3 -B teacher-console/scripts/w3_shadow_benchmark.py score
```

当前影子策略在进入 holdout 前冻结以下参数：挑战阈值 `0.9`、验证收益函数
`positive-expected-gain-v1`、默认/最大召回路数 `3/5`、证据选择器
`evidence-set-v2`。生产资格硬门槛：

- 独立 holdout 至少 5 题且至少 12 个目标；
- W3 目标准确率不低于 W2；
- 每题教师核对卡平均不超过 2；
- 真值摘要、标签、影子报告全部完整。

准确率是第一门槛。即使调用数下降或教师核对更快，只要正确率退化，就不得切换生产。

### 教师参考答案可被证据挑战

教师复核稿是主参考，但“文本不一致”不再直接等同于“答案错误”。目标判定支持：

- `correct`：与当前参考答案一致且复算成立；
- `incorrect`：违反题干、决定性关系或边界条件；
- `valid-supplement`：参考答案未覆盖，但教师确认题干约束、可复算关系和边界枚举三项均成立；
- `needs-review`：尚不能确定，不进入评分。

`valid-supplement` 计入数学正确率，同时单独记录补充解数量和参考答案修订。若补充解由
本轮影子输出触发，修订后的准确率可以纠正假阴性，但该 holdout 已不再独立；
`fresh_holdout_required=true`，不得用它直接宣称生产门禁通过。

## 2026-07-26 验收结果

冻结策略完成 5 道校准题和 5 道独立 holdout：

| Cohort | 题数 | 目标数 | W3 准确率 | W2 准确率 | 平均调用 | 平均核对卡 |
|---|---:|---:|---:|---:|---:|---:|
| calibration | 5 | 16 | 100% | 100% | 4.2 | 0 |
| holdout（修订前文本一致性口径） | 5 | 16 | 93.75% | 100% | 4.6 | 0.4 |
| holdout（教师确认数学正确性口径） | 5 | 16 | 100% | 100% | 4.6 | 0.4 |

原先唯一“错误”来自“三维复合场中电子的类平抛、螺旋运动与共圆心条件”。教师复算
确认：题干没有排除发射延迟 $\Delta t=nT$；此时两电子横向相位复现，$z_0=l$ 且
任意 $x_0$ 都是合法补充分支。正式解析和参考答案已修订，该目标标为
`valid-supplement`，所以修订后数学正确率为 16/16。

这次修订也意味着 W3 输出参与发现了参考答案缺口，本轮 holdout 不再是未见独立集：

- `holdout_ready=true`；
- `accuracy_non_regression=true`；
- `teacher_focus_bounded=true`；
- `independent_holdout_intact=false`；
- `fresh_holdout_required=true`；
- `production_eligible=false`。

因此 W3 的架构与数学正确性验收完成，W2 暂时继续作为默认解析路径；生产切换需要
一组新的未见 holdout，不能复用本轮五题证明泛化。

## W4-1：新鲜 holdout 冻结

新一轮不再允许“先运行 W3、再补教师目标标签”。独立评测分为四个不可倒置的动作：

```text
检查未见题数量
→ 生成 fresh-only manifest
→ 教师批准逐目标真值并生成 truth-lock.json
→ 携带 experiment 参数运行 W3 影子重放
```

历史上进入任何 W3 manifest 的题都会被排除，即使它当时属于 calibration、没有生成
标签或没有完成影子运行，也不能重新包装成新 holdout。新批次至少需要 5 道从未进入
旧 W3 manifest 的教师复核题；真值锁至少覆盖 12 个目标。`student-solution.md`、
逐目标真值或锁文件任一摘要改变后，评分失败关闭。

```bash
# 只读检查当前是否有足够的新鲜样本
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-1 \
  status --holdout-count 5

# 样本充足后冻结批次；不足时不创建任何实验目录
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-1 \
  seed --fresh-only --holdout-count 5 --batch-id w4-1

# 教师把 truth/*.json 的目标补全并设为 approved 后，生成不可覆盖的真值锁
python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-1 \
  freeze-truth

# 只有携带有效真值锁的批次才进入正式 W4-1 重放
python3 -B teacher-console/scripts/w3_shadow_run.py <entry-id...> \
  --experiment student-error-library/evals/w3-shadow-w4-1 \
  --routing-tier expert --model-id codex-visualization
```

截至 2026-07-26，14 道教师复核题均已进入 `w3-shadow-v1` manifest，新鲜可用题为
0，距离 W4-1 最低门槛还缺 5 道。因此当前只能验收冻结机制，不能生成合格的新
holdout，也不能改变 `production_eligible=false`。

教师可以显式选择旧题进入 `replay` 对照组。回放题同样先冻结当前教师真值，可用于
检查答案回归、调用量和教师核对负担，但聚合报告将其与 `holdout` 分栏；无论回放题
数量和准确率多高，都不计入独立 holdout，也不能单独打开生产门禁。

首批 `w3-shadow-w4-replay-1` 选择 5 道旧题、冻结 15 个目标。持久化 W3 输出回放
结果为 15/15，平均内部调用 4.4，平均教师核对卡 0.4；旧报告中的 W2 字段只是教师
复核稿基线，不计作 W2 生成成绩，真实 W2 成对候选在 W4-2 单独生成；验证记录完整，
但 `holdout_ready=false`、`fresh_holdout_required=true`、
`production_eligible=false`。

## W4-2：同条件 W2/W3 成对回放

W4-2 不再把教师复核稿当作 W2 输出。W2 候选必须从隔离题库的真实教师页面点击
“生成解析”，并固定 `routing_tier`、`model_id` 和 candidate evidence；W3 候选必须
使用相同模型与路由档位。`paired-score` 只接受浏览器来源、已完成状态、冻结目标完整
标注和一致运行条件，并同时报告目标准确率与交付质量：

```bash
python3 -B teacher-console/scripts/paired_answer_web_run.py <entry-id> \
  --evidence-mode candidate --routing-tier expert --model-id codex-visualization \
  --artifact-dir student-error-library/evals/w3-shadow-w4-replay-1/artifacts/<entry-id>

python3 -B teacher-console/scripts/w3_shadow_benchmark.py \
  --experiment student-error-library/evals/w3-shadow-w4-replay-1 paired-score
```

首个真实配对为交替电场与磁场分段运动题。初次诊断先后暴露 compact LaTeX 分数
残片、JSON 单反斜杠产生的隐藏控制字符和过窄的决定性关系长度上限；物化层现会原子
恢复这些分数命令、清除 ANSI 序列、拒绝其他控制字符，并允许最长 360 字的可复算
复合关系。失败重跑也不再覆盖上一份成功候选。

修复后完成了全部 5 道真实网页配对，共 15 个冻结目标：

| 指标 | W2 | W3 |
|---|---:|---:|
| 目标准确率 | 14/15（93.33%） | 15/15（100%） |
| 相对 W2 增量 | — | +6.67 个百分点 |
| W2 可交付性通过 | 3/5 | — |

唯一准确率差异来自三维电磁运动题的完整解集：W2 把“回旋圆心重合”误当必要条件，
漏掉 `z0=l、任意 x0` 的同相位分支；W3 保留了该分支。另有一道双区域磁场题结论
正确，但首次相遇证明对两个中间时段只写“联立无解”，未展示位置式，因此 W2 交付
完整性未通过。

这 5 题全部属于 replay，且唯一差异目标曾由旧 W3 输出推动教师补充真值，不能作为
独立泛化增益。`production_evidence=false` 继续成立；本轮证明成对链路可用、W3 能
在已知高风险分支上避免 W2 漏解，但不能替代新鲜 holdout。

## 主要实现

- `teacher-console/problem_decomposition.py`
- `teacher-console/solution_reasoning.py`
- `teacher-console/solution_verification.py`
- `teacher-console/w3_pipeline.py`
- `.claude/skills/decompose-physics-problem/`
- `teacher-console/scripts/w3_shadow_run.py`
- `teacher-console/scripts/w3_shadow_benchmark.py`
