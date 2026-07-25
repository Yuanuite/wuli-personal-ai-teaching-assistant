# 答案质量成对评测

目标是回答一个可证伪的问题：教师工作台中的 RAG、结构化契约、物化与校验加入后，
答案是否真的优于同一模型直接阅读题干的输出。

## 三层答案

| 层级 | 定义 | 用途 |
|---|---|---|
| `direct` | 同一模型只读取 `problem.md` 和题干不可缺少的原题图，不使用 RAG、既有答案、物理模型或工作流模板 | 直出基线，不是真值 |
| `web` | 隔离题库重置到“题干已复核、尚无答案/物理模型”，再从网页点击“生成解析”得到的 `.agent-baseline/student-solution.md` | 被测流水线 |
| `teacher-reviewed` | 当前教师已经批准的 `student-solution.md` | 正确性与教学质量真值 |

必须固定模型 ID、模型版本、参数和题干摘要。模型不一致的结果不能归因于工作流。
正式测试不能在真实题库上重生成；网页组必须使用独立临时题库，测试产物只复制到私有
评测目录。

若关键数值或几何关系只存在于原题图，而直出组没有实际获得图像内容，该样本必须标记
`direct_input_status=incomplete-image-data`，只能用于检查流程，不能进入胜负统计。

## 难度分层

按教师可修改的客观难度五档分别报告，不只看总平均：

`基础 / 较易 / 中等 / 较难 / 挑战`

每档至少 5 题才可做方向性判断，至少 10 题才允许作为默认策略切换证据。当前题库
“挑战”样本不足时只能报告个案，不能外推。

## 自动指标与人工盲评

自动指标只负责筛查：

- 多选题逐项判断是否与教师答案一致；
- 教师答案关键公式的保留率；
- 候选到教师答案的语义修改比例；
- 是否涉及物理结论、数值、方向或条件的关键纠正。

最终比较由教师盲评，隐藏 `direct/web` 标签，检查：

1. 结论和数值正确；
2. 关键事件或分支完整；
3. 只使用高中范围方法；
4. 推导是否达到“最少但充分”；
5. 图像是否表达物理关系而非装饰流程。

流水线组只有在各难度档均不劣于直出组、关键物理错误不增加，并且教师平均修改量下降
时才算改善。任一高难度档明显退化，都不得用总体平均掩盖。

## 命令

```bash
python3 teacher-console/scripts/paired_answer_benchmark.py \
  --library student-error-library \
  --experiment student-error-library/evals/answer-paired-v1 \
  seed --per-level 2

python3 teacher-console/scripts/paired_answer_benchmark.py \
  --experiment student-error-library/evals/answer-paired-v1 \
  refresh-references

python3 teacher-console/scripts/paired_answer_web_run.py \
  --experiment student-error-library/evals/answer-paired-v1 \
  --only-with-direct

python3 teacher-console/scripts/paired_answer_benchmark.py \
  --library student-error-library \
  --experiment student-error-library/evals/answer-paired-v1 \
  run --format markdown
```

同模型直出结果保存为 `artifacts/<entry-id>/direct.md`。网页答案只有同时具备
`web.meta.json`、来源为 `teacher-console-browser-click` 且状态为 `completed` 才进入统计；
从正式条目的历史 `.agent-baseline` 导入不能冒充本轮网页点击。报告的
`comparison_ready` 还要求直出输入完整且教师标准答案摘要未过期。
