# 客观难度评分机制

本文是题目客观难度评分的唯一维护导航，说明当前量表、证据链、代码真源、数据落点和
修改检查表。它不复制完整实现；算法行为以代码和配置文件为准。

## 一、机制边界

评分对象是“学生面对已复核题干，沿规范化标准解题路径完成题目所需的客观负担”，不是：

- AI 生成答案的篇幅、调用次数、冲突数或返工次数；
- 教师讲解时主动增加的步骤；
- 题库中的动态排名或分位数；
- 学生个人掌握度、答题正确率或解析质量。

评分不增加新的模型调用或教师门禁。自动基线可追溯，教师校准优先；题干或标准路径变化
后旧校准进入历史记录，新自动基线先行生效并提示重新校准。

## 二、真源地图

| 职责 | 真源 |
|---|---|
| 六维权重、评分算法、总分、等级、教师校准 | [difficulty_assessment.py](../.claude/skills/manage-student-error-library/scripts/difficulty_assessment.py) |
| A–O 知识深度标杆、内部校准曲线 | [difficulty_anchors.json](../.claude/skills/manage-student-error-library/scripts/difficulty_anchors.json) |
| 最小充分知识模块及其别名 | [difficulty_knowledge_units.json](../.claude/skills/manage-student-error-library/scripts/difficulty_knowledge_units.json) |
| 批量重算、W3 路径提升和教师校准保护 | [score_difficulties.py](../.claude/skills/manage-student-error-library/scripts/score_difficulties.py) |
| 旧题标准路径回填 | [backfill_standard_paths.py](../.claude/skills/manage-student-error-library/scripts/backfill_standard_paths.py) |
| 教师端刷新、保存与历史迁移 | [server.py](../teacher-console/server.py) |
| 核心算法回归 | [test_difficulty_assessment.py](../teacher-console/tests/test_difficulty_assessment.py) |
| 教师端和学生端契约回归 | [test_static_contract.py](../teacher-console/tests/test_static_contract.py)、[test_public_site.py](../teacher-console/tests/test_public_site.py) |
| 单题输入、结果和历史 | `student-error-library/entries/<id>/record.json` |

当前版本由 `difficulty_assessment.py` 中的 `SCHEMA_VERSION`、`RUBRIC_VERSION` 和配置文件
版本共同标识。改变算法、标杆或知识模块口径时，必须显式升级对应版本，不能静默覆盖。

## 三、输入、计算与输出

### 3.1 输入

评分只使用：

1. 已复核的 `problem.md`；
2. `record.json.standard_solution_path`。

标准路径至少需要：

- `high_school_basis`：必要的高中知识基础；
- `physical_stages`：真实物理状态或阶段；
- `reasoning_steps`：最短标准解题步骤；
- `decisive_relations`：不可绕过的决定性关系。

增强证据包括 `reasoning_graph`、`assessment_relations`、`representation_transforms`、
`condition_checks`、`type_distance` 和 `verification`。W3 蓝图可在解题完成后单向投影
为同一标准路径，但评分字段不得进入 W3 检索、Solver、验证或仲裁上下文。

缺少规范化标准路径时返回 `awaiting-standard-path`，不根据题干关键词或答案长度生成
伪精确分。

### 3.2 六维与权重

| 维度 | 权重 | 评价对象 |
|---|---:|---|
| 知识深度 | 20% | 最高不可绕过的概念理解层级及其决定性概念链 |
| 知识整合 | 15% | 完成标准解法所需的最小充分独立知识模块集 |
| 题型距离与建模转换 | 20% | 学生从熟悉高中母题识别到当前模型的距离 |
| 过程与状态复杂度 | 20% | 已知正确知识点后，仍需组织的状态与过程组合拓扑 |
| 运算与表达负荷 | 10% | 正确列式后，最短标准路径上的必要计算链 |
| 条件与完备性 | 15% | 最高不可绕过的条件、边界、分类或全局完备性检查 |

每维正式分数为 `0–5`，步长 `0.1`。总分使用纯加权平均并四舍五入为整数：

```text
总分 = round(Σ(维度分 / 5 × 维度权重))
```

固定等级为：`0–20 基础`、`21–40 较易`、`41–60 中等`、`61–80 较难`、
`81–100 挑战`。等级不随题库分布变化。

### 3.3 输出

当前结果保存在 `record.json.difficulty_assessment`，主要字段为：

- `rubric_version`、`anchor_version`：算法和标杆版本；
- `score`、`level`、`dimensions`：总分、等级和六维结果；
- `knowledge_depth_trace` 等：各维确定性证据轨迹；
- `auto_baseline`：教师校准前的自动基线；
- `input_digest`：题干与标准路径的联合摘要；
- `calibration`：自动基线或教师校准状态。

旧结果保存在 `difficulty_assessment_history`，最多保留最近十次。兼容字段
`record.json.difficulty` 只同步当前等级，不是评分真源。

## 四、六维确定性规则

### 4.1 知识深度

知识深度从每个目标反向裁剪 `reasoning_graph`，只保留：

- 位于目标必要路径上的节点；
- 不是纯代数整理；
- 绑定了决定性关系。

评分取最长不可绕过概念链，而不是把多个知识点简单相加。认知操作分为：

`识别概念 → 条件内应用 → 解释或推导关系 → 审查条件与因果 → 从基础原理重建`。

路径成本通过 `difficulty_anchors.json` 的固定曲线映射到 A–O 标杆。内部校准允许到 6，
用于保持竞赛高端标杆间距；正式维度统一封顶 5。越过 5 的内部坐标必须同时具备第一性
重建链、足够长的有效概念链和独立验证，否则确定性降级。

### 4.2 知识整合

知识整合统计目标完成切片中绑定决定性关系的最小充分知识模块。核心原则：

- 同一母模型的公式、代数变形、重复使用和分量式合并；
- 水平动量与竖直动量仍属于同一个线动量守恒模块；
- 线动量与角动量属于两个独立模块；
- 相切/临界几何、非标准转角—时间映射、外部释放/相位时序，只有不可绕过时才拆分；
- 多个并行必要分支的独立模块均计入。

知识模块 ID、名称和别名只在 `difficulty_knowledge_units.json` 维护。模块数通过固定锚点
换算为维度分，不按题库频次动态调整。

### 4.3 题型距离与建模转换

使用六级固定锚点：

| 模式 | 分数 |
|---|---:|
| 教材母题 | 0.8 |
| 常规变式 | 1.6 |
| 标准迁移 | 2.5 |
| 模型重构 | 3.4 |
| 隐蔽桥梁 | 4.2 |
| 非常规构造 | 5.0 |

该维评价“未见答案时能否自然识别最短高中母题”，不是知识点数量。旧路径若没有显式
`type_distance`，评分器可保守推断；这种回退不得成为抬高题目难度的捷径。

### 4.4 过程与状态复杂度

该维不统计场的数量，而评价状态分析和过程分析的组合难度：

| 组合拓扑 | 基准分 |
|---|---:|
| 单一状态 | 0.8 |
| 标准串联 | 1.6 |
| 时序组合 | 2.5 |
| 同步耦合 | 3.4 |
| 分支耦合 | 4.2 |
| 嵌套全局组合 | 5.0 |

完全相同的周期重复折叠；截断段、释放时刻、相位窗口、多方向同步、多对象同步、独立路径
分支和全局闭合不能因为使用同一物理公式而被折叠。次级信号只能作最高 `0.3` 的有限修正。

### 4.5 运算与表达负荷

该维先在目标必要图中识别计算节点，再计算最长依赖链。基准层级为：

| 必要计算量 | 基准分 |
|---|---:|
| 极少 | 0.8 |
| 较少 | 1.6 |
| 中等 | 2.5 |
| 较大 | 3.4 |
| 很大 | 4.2 |
| 极高 | 5.0 |

非线性表达、参数函数、联立消元、范围/边界、分段计算和真实分支计算可触发层级或有限
修正。答案篇幅、公式行数和教学展开步数不参与累计。

### 4.6 条件与完备性

取最高不可绕过条件负担，不把审计标签数量累加成高分：

| 条件负担 | 基准分 |
|---|---:|
| 显式常规条件 | 0.8 |
| 方向、符号或适用条件 | 1.0 |
| 范围与边界 | 1.8 |
| 临界或极值 | 2.5 |
| 首次或唯一性 | 2.6 |
| 分类讨论 | 3.0 |
| 枚举完备性 | 3.5 |
| 全局或耦合完备性 | 4.2 |

证据优先来自具体 `condition_checks`；旧 W3 分类标签只作为类别，不直接证明负担，评分器
改从决定性关系和目标必要路径上的 `audit` 节点提取真实证据。多个条件种类最多作 `0.2`
修正。

## 五、教师校准和失效逻辑

教师可以修改六维分数、核心判断和总结，但必须：

- 保持六个维度完整；
- 分数在 `0–5` 且步长为 `0.1`；
- 保留自动基线；
- 实际改分时填写简短校准依据。

教师保存后状态为 `teacher-edited`，总分仍使用同一加权公式。题干或标准路径发生变化时，
`input_digest` 改变，旧教师校准进入历史，新自动结果标记为需要重新校准。仅修改答案措辞、
教学标签、模型调用或 W2/W3 路由不会改变客观难度。

## 六、如何调整机制

| 想调整什么 | 修改位置 | 必须同步 |
|---|---|---|
| 六维名称、权重、总分或等级 | `difficulty_assessment.py` 顶部常量与 `_score()` | 教师/学生端文案、静态契约、架构说明 |
| 某一维的识别和锚点逻辑 | 对应 `_conceptual_path_profile()`、`_knowledge_unit_profile()`、`_process_profile()`、`_calculation_profile()`、`_condition_profile()` | 该维正反例和全库分布回归 |
| 知识深度 A–O 标杆或曲线 | `difficulty_anchors.json` | `anchor_version`、标杆回归、历史解释 |
| 知识模块拆分或合并 | `difficulty_knowledge_units.json` 及最小充分合并规则 | `registry_version`、典型模块组合测试 |
| 标准路径字段 | `difficulty_assessment.py` 的路径规范化/投影逻辑及解析契约 | W2/W3 fake adapter、契约测试、既有路径迁移 |
| 教师保存和失效行为 | `server.py` 与 `normalize_teacher_edit()` | API 文档、教师端测试、历史保留测试 |
| 批量迁移现有题目 | `score_difficulties.py` | 先只读抽样，再全库重算和学生端安全同步 |
| 学生端公开摘要 | `public_site.py` | `test_public_site.py`、隐私边界、只更新元数据 |

调整算法时不要直接编辑题目的 `difficulty_assessment` 结果；应先修改算法/配置和版本，
通过测试后使用批量脚本重算。教师校准默认不可被批量覆盖，除非显式使用
`--overwrite-teacher-edits`。

## 七、验证清单

至少完成：

```bash
python3 -B -m unittest discover -s teacher-console/tests -p 'test_difficulty_assessment.py' -v
python3 -B -m unittest discover -s teacher-console/tests -p 'test_public_site.py' -v
python3 -B -m unittest discover -s teacher-console/tests -p 'test_static_contract.py' -v
git diff --check
```

涉及全库迁移时，再执行：

1. 选择简单、中等、较难和竞赛标杆题做只读抽样；
2. 检查六维证据轨迹，不只比较总分；
3. 确认教师校准未被默认覆盖；
4. 重算正式题库；
5. 仅对已经发布的条目执行学生端难度摘要同步；
6. 检查分数分布和重点题目的前后变化；
7. 更新 `RUBRIC_VERSION`、配置版本、测试、本文和 `CHANGES.md`；
8. 修改代码后执行 `graphify update .`。

当机制说明与实现不一致时，以测试和执行代码为准，并在同一变更中修正文档。
