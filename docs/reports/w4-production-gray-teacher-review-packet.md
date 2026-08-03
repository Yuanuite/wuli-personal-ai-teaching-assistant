# W4-4 生产灰度教师复核包

```yaml
policy: wuli-analysis-adaptive-v1
cohort_size: 3
holdout_reuse_count: 0
source: AAPT 2024 F=ma official exam and solutions
state: teacher-approved
```

这三题只用于新的生产灰度，不属于
`w3-shadow-w4-fresh-1`，也不会回填该 holdout 的成绩。教师批准当前答案后才会把
条目 ID 写入灰度白名单并从真实“生成解析”入口运行。

## 1. Q13：斜绳双物块的瞬时张力

条目：
`20260729-generated-f-ma-2024-q13改写-斜绳双物块的瞬时张力-4326ccb7`

题目与答案：

- [题干](../../student-error-library/entries/20260729-generated-f-ma-2024-q13改写-斜绳双物块的瞬时张力-4326ccb7/problem.md)
- [学生版答案](../../student-error-library/entries/20260729-generated-f-ma-2024-q13改写-斜绳双物块的瞬时张力-4326ccb7/student-solution.md)

需要确认的三个结论：

1. 释放瞬间约束为 \(a_y=(x/\sqrt{h^2+x^2})a_x\)；
2. \(T=mg(h^2+x^2)/(h^2+2x^2)\)；
3. \(mg/2<T\le mg\)，两个极限分别为 \(mg\)、\(mg/2\)，原题图像选 B。

确定性初筛预期：`decompose`，风险分数 6。

## 2. Q14：可动圆环上的小珠

条目：
`20260729-generated-f-ma-2024-q14改写-可动圆环上的小珠-f17e1feb`

题目与答案：

- [题干](../../student-error-library/entries/20260729-generated-f-ma-2024-q14改写-可动圆环上的小珠-f17e1feb/problem.md)
- [学生版答案](../../student-error-library/entries/20260729-generated-f-ma-2024-q14改写-可动圆环上的小珠-f17e1feb/student-solution.md)

需要确认的三个结论：

1. 相对静止瞬间共同速度为 \(1.0\,\mathrm{m/s}\)；
2. 小珠上升 \(0.10\,\mathrm m\)；
3. 圆心角 \(60^\circ\)，原题选 C。

确定性初筛预期：`w2`，用于验证灰度白名单中的低结构风险题仍保持 W2。

## 3. Q25：变半径退绕的悠悠球

条目：
`20260729-generated-f-ma-2024-q25改写-变半径退绕的悠悠球-628d3257`

题目与答案：

- [题干](../../student-error-library/entries/20260729-generated-f-ma-2024-q25改写-变半径退绕的悠悠球-628d3257/problem.md)
- [学生版答案](../../student-error-library/entries/20260729-generated-f-ma-2024-q25改写-变半径退绕的悠悠球-628d3257/student-solution.md)

需要确认的三个结论：

1. \(mgz=\frac12mv^2+\frac12I(v/r)^2\)；
2. \(v^2=2gz/[1+\ell/(2(\ell-z))]\)；
3. 平动速度从零增大后又趋零，后段存在向上加速度，原题选 E。

确定性初筛预期：`decompose`，风险分数 2。

## 运行边界

- 灰度配置只包含上述 3 个条目；
- Q13、Q25 命中 W3，Q14 保持 W2；
- W3 阶段失败、超时、结构警告、调用超限、教师核对卡超限或候选校验失败时自动回退 W2；
- 所有生成结果仍进入 `needs-answer-review`，Agent 无权批准；
- W3 候选若引入经复算确认的新错误，立即停止灰度，不进入 W4-5。

教师若确认当前三题题干、答案和九个结论，可回复：

> 确认 W4-4 灰度题：Q13、Q14、Q25 当前答案与九个结论均正确，同意将这三个条目加入一次性灰度白名单。
