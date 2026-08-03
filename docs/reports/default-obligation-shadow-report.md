# 默认义务 Shadow 触发报告

- 生成时间：`2026-07-29T05:39:21.400634+00:00`
- 策略：`wuli.default-obligation-rules.v1`
- 模式：`shadow-only`，只统计，不影响 Solver / VERIFIED / 交付
- W3 报告数：23
- 可重算 blueprint 数：21
- 触发题目数：7
- 建议总数：10

## 结论

当前规则已经能从旧 W3 blueprint 中识别出潜在“默认枚举全部物理解支”义务。

这些命中仍需要教师/开发者逐条审查；在误伤率被确认前，不建议升级为硬 Gate。

## 安全边界

- 不重新调用 Solver；
- 不修改 `verification_obligations`；
- 不影响 `VERIFIED`、评分或交付；
- 所有命中均标记为人工审查材料。

## IPhO 整卷覆盖状态

- 状态：`not-covered-by-w3-blueprint-recompute`
- 路径：`student-error-library/evals/ipho-2021-full-exam-v1/results/summary.json`
- 已有整卷成绩：30.0/30.0

The IPhO full-exam harness answers official subparts directly through Agent Gateway and does not persist W3 decomposition blueprints; default obligation shadow suggestions therefore cannot be audited from this summary without a separate blueprint pass.

## 触发明细

### 交替电场与磁场中带电粒子的分段运动

- Entry：`20260722-7-22物理练习题-p3-f5a4ce56`
- 报告：`student-error-library/entries/20260722-7-22物理练习题-p3-f5a4ce56/w3-shadow-report.json`
- 建议数：2

- `default.solve.all-physical-solutions.v1` → target `T1`
  - 触发文本：求t=0释放粒子在指定时刻的位置坐标。
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested
- `default.solve.all-physical-solutions.v1` → target `T2`
  - 触发文本：求给定时间区间内静电力对t=0释放粒子所做的总功。
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested

### 导体轨道方程与安培力做功

- Entry：`20260724-7-24物理错题-1-p1-dd865c2c`
- 报告：`student-error-library/entries/20260724-7-24物理错题-1-p1-dd865c2c/w3-shadow-report.json`
- 建议数：1

- `default.solve.all-physical-solutions.v1` → target `T2`
  - 触发文本：确定导体棒所受安培力F安随位置x的变化关系
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested

### F=ma 2024 Q13改写：斜绳双物块的瞬时张力

- Entry：`20260729-generated-f-ma-2024-q13改写-斜绳双物块的瞬时张力-4326ccb7`
- 报告：`student-error-library/entries/20260729-generated-f-ma-2024-q13改写-斜绳双物块的瞬时张力-4326ccb7/w3-shadow-report.json`
- 建议数：1

- `default.solve.all-physical-solutions.v1` → target `Q3`
  - 触发文本：确定所有 x≥0 时张力的范围，并核对 x=0 与 x→∞ 两个极限及其图像一致性。
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested

### F=ma 2024 Q1改写：穿过等高圆环的箭

- Entry：`20260729-generated-f-ma-2024-q1改写-穿过等高圆环的箭-0e75d602`
- 报告：`student-error-library/entries/20260729-generated-f-ma-2024-q1改写-穿过等高圆环的箭-0e75d602/w3-shadow-report.json`
- 建议数：2

- `default.solve.all-physical-solutions.v1` → target `T1`
  - 触发文本：确定箭到达最高点的时刻。
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested
- `default.solve.all-physical-solutions.v1` → target `T2`
  - 触发文本：确定初速度的竖直分量。
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested

### F=ma 2024 Q2改写：旋转游乐设施离地

- Entry：`20260729-generated-f-ma-2024-q2改写-旋转游乐设施离地-e0a36eea`
- 报告：`student-error-library/entries/20260729-generated-f-ma-2024-q2改写-旋转游乐设施离地-e0a36eea/w3-shadow-report.json`
- 建议数：1

- `default.solve.all-physical-solutions.v1` → target `T3`
  - 触发文本：求临界角速度，并判断其对应的原题选项。
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested

### 第38届全国竞赛复赛第3题：超球连续三次碰撞

- Entry：`20260729-generated-第38届全国竞赛复赛第3题-超球连续三次碰撞-91f7fdaa`
- 报告：`student-error-library/entries/20260729-generated-第38届全国竞赛复赛第3题-超球连续三次碰撞-91f7fdaa/w3-shadow-report.json`
- 建议数：2

- `default.solve.all-physical-solutions.v1` → target `T3`
  - 触发文本：求第二次碰撞后的水平速度分量 v_{2x}
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested
- `default.solve.all-physical-solutions.v1` → target `T4`
  - 触发文本：求第二次碰撞后的角速度 ω_{2z}
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested

### F=ma 2024 Q13改写：斜绳双物块的瞬时张力

- Entry：`20260729-generated-f-ma-2024-q13改写-斜绳双物块的瞬时张力-4326ccb7`
- 报告：`student-error-library/evals/w4-production-gray-1/artifacts/20260729-generated-f-ma-2024-q13改写-斜绳双物块的瞬时张力-4326ccb7/first-run-w3-shadow-report.json`
- 建议数：1

- `default.solve.all-physical-solutions.v1` → target `Q3`
  - 触发文本：确定所有 x≥0 时张力的范围，并核对 x=0 与 x→∞ 两个极限及其图像一致性。
  - 检查建议：题干未限定唯一或首次时，枚举所有满足题设条件和物理可行域的解支。
  - 风险：high；状态：suggested

## 建议

继续保持 shadow-only。下一步只需要对触发明细做人工标注：`useful` / `false-positive` / `already-covered`，攒够样本后再决定是否进入软提示层。
