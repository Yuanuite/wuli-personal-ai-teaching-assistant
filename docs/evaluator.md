# 悟理 Evaluator 薄切片

Evaluator 是悟理“教学进化系统”的第一块轻基建：它把一次题目处理的结果写成可审计的 `evaluation.json`，记录哪些门禁通过、哪些产物缺失、哪些地方需要教师继续复核。

它不调用大模型，不替教师批准，也不发布学生端。它只读取已有文件并生成确定性报告。

## 当前评价范围

第一版覆盖四类事实：

| 范围 | 检查内容 |
|---|---|
| 题目与答案结构 | `record.json`、`problem.md`、`solution.md`、本地图片引用、必需答案栏目 |
| 来源与答案复核 | OCR/来源复核状态、答案摘要是否仍对应教师批准版本 |
| 可视化 | 有 `physics-model.json` 时检查预审 HTML、构建报告、运行时状态和可视化复核摘要；无模型时记为 `skipped` |
| 交付 | `delivery-manifest.json`、Markdown、学生包、PDF 状态和交付文件清单 |

认知负担目前只是启发式提示，例如答案过短或过长；物理语义正确性仍由教师复核和后续专门 verifier 承担。

## 输出文件

关键生命周期动作会自动刷新：

```text
approve-source
save-answer
approve-answer
request-answer-revision
prepare-visualization
approve-visualization
finish
prepare-publication / save-publication-images / publish-publication
Agent 解析、返修、可视化任务结束
```

手动运行或自动触发后会生成：

```text
student-error-library/entries/<entry-id>/evaluation.json
output/<题目>/evaluation.json  # 已交付时
```

`finish` 还会把摘要写入 `delivery-manifest.json`：

```json
{
  "evaluation": {
    "status": "passed",
    "scores": {
      "completeness": 5,
      "correctness": 5,
      "student_cognitive_load": 5,
      "safety": 5,
      "deliverability": 5
    },
    "teacher_review_required": true,
    "file": "/absolute/path/to/output/evaluation.json"
  }
}
```

## 手动命令

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library evaluate <entry-id>
```

只查看、不写文件：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library evaluate <entry-id> --no-write
```

也可以直接调用底层脚本：

```bash
python3 .claude/skills/manage-student-error-library/scripts/evaluator.py \
  --library student-error-library <entry-id>
```

## 与后续 RAG / Evolve 的关系

Evaluator 先回答“什么结果值得记住”。后续 Candidate Archive 和题库 RAG 应复用它的字段：

- `checks`：候选产物通过/失败的具体门禁；
- `failure_reasons`：可沉淀为下次 Agent 的避坑上下文；
- `scores`：用于比较不同候选或模型；
- `evidence_sources`：给 AI 审计 RAG 提供稳定引用。

这让悟理的闭环从“生成一次”升级为：

```text
生成候选 → 自动评价 → 教师复核 → 记录成败 → 下次检索复用
```
