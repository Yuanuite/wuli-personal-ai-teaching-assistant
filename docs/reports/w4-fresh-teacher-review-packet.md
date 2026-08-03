# W4 新鲜 Holdout 教师复核包

状态：`AWAITING_TEACHER_APPROVAL`

日期：2026-07-29

用途：为 `CE-505` 准备 5 道从未进入历史 W3 manifest 的独立题。所有题均为 AAPT
2024 F=ma 官方题目的中文评测改写版；改写只增加显式中间目标，不改变物理条件和官方
最终答案。

官方来源：

- 试题：<https://www.aapt.org/physicsteam/upload/2024_F-ma_Exam.pdf>
- 解答：<https://www.aapt.org/physicsteam/upload/F-ma-2024-Solutions_v2.pdf>
- 历年试题页：<https://www.aapt.org/physicsteam/PT-exams.cfm>

## 待批准的 5 道答案摘要

| 题目 | 目标 1 | 目标 2 | 目标 3 |
|---|---|---|---|
| Q1 穿过等高圆环的箭 | 最高点时刻 \(1.5\ \mathrm{s}\) | \(v_{0y}=15\ \mathrm{m/s}\) | \(h=10\ \mathrm{m}\)，B |
| Q2 旋转设施离地 | \(N_{\text{floor}}=0\) | \(\tan30^\circ=g/(\omega^2R)\) | \(\omega=1.86\ \mathrm{rad/s}\)，A |
| Q9 分段制动 | \(\tfrac12mv_0^2=\mu_dmg(100\ \mathrm{m})\) | 冰面距离 \(200\ \mathrm{m}\) | 总距离 \(250\ \mathrm{m}\)，C |
| Q10 旋转箱双弹簧 | \(F_{\text{in}}=3k(r-\ell)\) | \(m\omega^2r=3k(r-\ell)\) | \(r=3k\ell/(3k-m\omega^2)\)，D |
| Q18 卫星变轨 | \(v_i=\sqrt{GM/R}\) | \(a=3R/2,\ v_f^2=4GM/(3R)\) | \(\Delta v=(\sqrt{4/3}-1)\sqrt{GM/R}\approx0.15\sqrt{GM/R}\)，B |

合计 5 题、15 个逐目标真值，超过生产门禁要求的 5 题、12 目标。

## 已完成的机器检查

- 5/5 `kb.validate_entry(..., ready_rules=True, require_answer_review=False)` 无错误；
- 5/5 学生版满足“最短主线”、高中方法、编号步骤和不超过五步的门禁；
- 5/5 `solution.md` 与 `teacher-solution.md` 字节一致；
- 每题包含题干、学生版、教师版、解释图、至少两个教师审计检查；
- 5 份拟冻结真值的答案引用 SHA-256 均与当前学生版逐字节一致；
- 在隔离临时目录中预演 `freeze_truth` 已通过：`case_count=5`、`target_count=15`；
- 5/5 评价报告唯一失败项均为预期的 `answer_review_current`，无其他失败或警告；
- 尚未批准答案、尚未生成 fresh manifest、尚未调用 W2/W3 provider。

## 教师批准语义

教师确认本复核包，即表示：

1. 批准当前 5 道题的题干和分层答案摘要；
2. 批准表中的 15 个目标作为冻结前教师真值；
3. 允许系统写入当前答案摘要、创建 fresh-only manifest 并冻结真值；
4. 不代表预先接受 W2/W3 的输出或 G5 结论。

任一题需要修改时，应先修改该题，再重新计算答案摘要并重新请求批准。
