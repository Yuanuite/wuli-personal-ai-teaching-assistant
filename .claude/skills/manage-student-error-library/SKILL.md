---
name: manage-student-error-library
description: "Own the complete local lifecycle of a student's wrong questions: discover new uploads, OCR and deduplicate them, coordinate source review and analysis, create layered answers and diagrams, invoke an optional physics simulator specialist, validate entries, rebuild retrieval, export Markdown/PDF, create a portable student package, schedule review, and analyze weaknesses. Trigger for 处理现在新上传的题目, 错题入库, OCR错题, 生成解析, 生成变式题, 导出PDF, 复习错题, 薄弱点分析, and equivalent English requests."
---

# Manage Student Error Library

This Skill is the lifecycle owner. Keep student data local. Delegate interactive physics rendering to `build-physics-simulator`; do not implement simulator Canvas or ZIP logic here.

Read [references/responsibility-matrix.md](references/responsibility-matrix.md) before crossing Skill boundaries. For end-to-end requests, follow [references/lifecycle.md](references/lifecycle.md). Read [references/schema.md](references/schema.md) before editing records and [references/answer-template.md](references/answer-template.md) before writing answers.

## TL;DR

| 你要做什么 | 操作 |
|-----------|------|
| 处理新上传的题目（全流程） | 说"处理现在新上传的题目" → source review → 分析 → 出答案 → 教师审批 → finish |
| 只做 OCR 入库 | 图片放入 `error-collection/` → 说"错题入库" → 核对 OCR → approve-source |
| 生成解析 | 说"生成解析" → 教师复核答案 → approve-answer → finish |
| 生成可视化 | 说"生成可视化" → delegate `build-physics-simulator` → 重审答案 → approve-visualization → finish |
| 生成变式题 | 说"生成变式题" → 检索原题 → 改两个维度生成新题+答案 |
| 复习错题 / 薄弱点 | 说"复习错题" 或 "薄弱点分析" |
| 查看统计 | `kb.py stats` + `kb.py due` |

## One natural-language entry point

When the user says “处理现在新上传的题目” or equivalent, complete the whole lifecycle in one turn when the source is unambiguous:

```bash
python3 <skill-dir>/scripts/process_uploads.py \
  --library <workspace>/student-error-library \
  start --input <workspace>/error-collection --subject "高中物理" \
  --vision-capability <available|unavailable>
```

Declare the actual model capability. Never claim `available` merely because OCR exists. If the reasoning model cannot inspect images, use the configured visual-review adapter; otherwise generate `source-review.md` and wait for explicit human approval. `auto` is fail-closed and selects the human path unless an adapter is configured.

For every returned work order:

1. Resolve source review through a vision-capable agent, a configured adapter, or explicit human approval. OCR alone never satisfies this gate.

   🔴 **CHECKPOINT · 原图核对**：OCR 文本核对完毕后，确认题目文字、数字、符号、图中标注与原始图片一致后再继续。无视觉能力的模型不得自行解除此门禁；使用独立视觉适配器，或等待人工执行 `approve-source`。
2. Update `record.json`: subject, known grade, knowledge points, observable error types, difficulty, and honest student-error evidence. Set `ocr.review_required=false` only after visual review.
3. Search ready entries and retrieve relevant methods. Solve independently; do not treat handwriting as ground truth.
4. Write the layered answer and a reasoning image. Keep the student main line normally within five steps; put alternate proofs and exhaustive checks in the teacher layer.
5. During the default analysis pass, create a precise answer SVG but do not create `physics-model.json` or an interactive simulator. Keep the visualization page available as an optional teacher decision. Only after the teacher explicitly asks for an interactive visualization—such as “我想为这道题生成一个可视化结果”—invoke `build-physics-simulator` to create and validate the model. Do not build a second renderer here.
6. Ask the teacher to review the generated student/teacher layers. Approval must record the reviewer and the current answer-artifact digest; a revision request returns the entry to analysis.

   🔴 **CHECKPOINT · 答案复核**：逐项确认——(a) 分层答案段落齐全（答案速览/详细解答/易错点/30秒自测），(b) 至少两重验证通过，(c) 所有图片引用指向存在的文件，(d) 无未解决标记。任一未通过 → 修复后再执行 `approve-answer`。
7. Always retain the optional visualization page. When no model exists, label it “not generated” and accept a teacher generation request; do not infer that the question is unsuitable. After an explicit request creates `physics-model.json`, prepare the interactive visualization, show the exact staged HTML, and record a separate approval. Because the shared model is part of the answer digest, its creation or change requires answer re-review before visualization approval. A static SVG/PNG remains inside answer review. Never hand-edit generated HTML or let the model approve itself.

   🔴 **CHECKPOINT · 可视化复核**：`physics-model.json` 创建或修改后必须先重新复核答案（答案摘要变了），再执行 `prepare-visualization` → `approve-visualization`。验证物理阶段、轨迹方向、关键事件文字和交互控件与答案一致后，才进 finish。
8. Finish and deliver:

```bash
python3 <skill-dir>/scripts/process_uploads.py \
  --library <workspace>/student-error-library \
  approve-answer <entry-id> --reviewer teacher --note "已复核答案与图像"

# Run these two commands only for an entry with physics-model.json.
python3 <skill-dir>/scripts/process_uploads.py \
  --library <workspace>/student-error-library \
  prepare-visualization <entry-id> --runtime-check auto

python3 <skill-dir>/scripts/process_uploads.py \
  --library <workspace>/student-error-library \
  approve-visualization <entry-id> --reviewer teacher --note "已复核物理阶段、轨迹、文字和控件"

python3 <skill-dir>/scripts/process_uploads.py \
  --library <workspace>/student-error-library \
  finish <entry-id> --simulator auto
```

Return only artifacts recorded in `delivery-manifest.json`. The final directory includes Markdown, PDF when available, optional simulator, and `student-package.zip`.

Public student-site publication is a separate, explicit post-delivery action. For entries with source images, first create a non-destructive cropped/redacted WebP copy through `save_public_images`; record the source/output digests and teacher approval in `publication-images.json`. Never treat automatic cropping as privacy approval. Then run `scripts/public_site.py prepare <entry-id>`, show the private `publication-draft/`, and require the teacher's privacy confirmation before `publish`. Publish only the allowlisted student Markdown, teacher-approved public question images, a PDF regenerated from that sanitized Markdown when available, referenced safe answer images, and a teacher-approved simulator. Never publish source uploads, teacher answers, internal IDs/JSON, manifests, review evidence, absolute paths, or unapproved simulations. Copy into `student-site/` only; never initialize, commit, or push GitHub without a separate explicit request.

Do not pause for routine confirmation. Pause only for unresolved source ambiguity, permission to upload private material remotely, or a required dependency without a safe fallback.

## Answer ownership

This Skill alone owns `student-solution.md`, `teacher-solution.md`, `solution.md`, PDF generation, and student packaging.

- Use [references/answer-template.md](references/answer-template.md).
- For a shared physics model, render answers with:

```bash
python3 <skill-dir>/scripts/render_answers.py \
  <entry>/physics-model.json --entry-dir <entry>
```

- A teacher may edit the rendered Markdown in the local workbench. Record this as `physics-model.json.source.answer_render_mode=manual`, revoke the previous answer approval, and rebuild retrieval. `finish` must preserve that manual version instead of silently rendering over it. Explicitly set the mode back to `model` only when the teacher chooses to regenerate from the structured teaching fields.

- Define symbols before use, state shortcut conditions, and perform at least two applicable checks.
- Prefer geometry, conservation laws, graphs, and standard high-school conclusions over unnecessary coordinate expansion.
- Every finalized solution must contain `答案速览`, `详细解答`, `易错点`, and at least one existing local image.
- Never infer the student's mental cause from markings alone; use `待学生确认` for hypotheses.

## Knowledge-base operations

Use the deterministic local script:

```bash
python3 <skill-dir>/scripts/kb.py --library <library> search "<query>"
python3 <skill-dir>/scripts/kb.py --library <library> validate <entry-id>
python3 <skill-dir>/scripts/kb.py --library <library> review <entry-id> correct --note "<note>"
python3 <skill-dir>/scripts/kb.py --library <library> due
python3 <skill-dir>/scripts/kb.py --library <library> stats
```

`validate`, `finalize`, answer rendering, and export rebuild the index. Fix every validation error before delivery.

The canonical entry always stays at `entries/<entry-id>/`. `folders/` is a rebuildable local view grouped by upload date. Synchronize it after ingestion; a teacher rename changes the view and grouping metadata, never the canonical path, answer digest, or review timestamps.

### Retrieval and weakness analysis

When the user asks for薄弱点分析 or 复习错题:

1. **Get the numbers first** — `kb.py stats` and `kb.py due`. Stats gives totals by knowledge point, difficulty, and error type. Due lists entries ready for spaced review.
2. **Distinguish frequency from weakness** — a knowledge point appearing 5× in stats means it was tested 5×, not necessarily that the student is weakest at it. Cross-check with `review` correctness records: high error rate on a rare topic > low error rate on a frequent topic.
3. **Present as a ranked table** — top 3-5 knowledge points by (error count × avg difficulty), not by raw frequency. Example output:
   ```
   | 知识点 | 错题数 | 平均难度 | 最近一次错误 | 建议 |
   |--------|--------|---------|-------------|------|
   | 带电粒子在磁场中的圆周运动 | 3 | 4/5 | 2026-07-17 | 重点复习几何约束 + 多解枚举 |
   ```
4. **Recommend one concrete action per weak point** — not "多做题" but "重做 entry X 的变式题" or "先讲切线交点定理再回头做错题".
5. **Schedule review with `kb.py review`** — record correctness and notes after each review session. Do not claim mastery from one correct attempt; a knowledge point is marked `mastered` only after two consecutive correct reviews spaced ≥ 7 days apart.
6. **For ad-hoc search** — `kb.py search "<concept>"` returns the most relevant ready entries. Use this before generating variants to ensure the transferable misconception is correctly identified.

## OCR and privacy

- Prefer local Apple Vision OCR; use [references/ocr-adapters.md](references/ocr-adapters.md) only when needed.
- Keep visual review separate from OCR. An adapter must return reviewed text, diagram facts, and uncertainties through the documented JSON protocol.
- When no image-capable model or adapter exists, edit `problem.md` against `source-review.md`, then run `process_uploads.py approve-source <entry-id> --reviewer <role>` only after a human has checked the original.
- Remote OCR is disabled unless the user explicitly authorizes uploading student material.
- Remote visual review has a separate `privacy.allow_remote_visual_review` gate; OCR authorization does not imply visual-review authorization.
- Preserve empty or failed OCR as a reviewable entry; never discard the source.
- Split unrelated questions into atomic entries while retaining source provenance.

## Failure modes and fallbacks

| # | Trigger | First-line fix | Last-resort fallback |
|---|---------|---------------|---------------------|
| F1 | **OCR returns empty text** | Retry with `--ocr vision` (Apple Vision) or an external adapter | Mark `ocr.review_required: true`, set `problem.md` to `[待人工转写]`, ask user to type the question |
| F2 | **ingest returns `status: duplicate`** | Report existing `entry_id` and title; ask whether to view or re-OCR | If user wants a separate entry, re-ingest with a different filename |
| F3 | **validate reports errors** | Read each error; most are mechanical (missing heading/image/unresolved marker) — fix and re-run | If stuck on semantic errors, fill with best-guess concepts and flag in completion report |
| F4 | **render_svg.py fails** (non-zero exit or empty output) | Check JSON scene for valid `type` values and finite coordinates | Skip SVG, describe the diagram in text inside solution; mark omission in completion report |
| F5 | **OCR unavailable (non-macOS or Swift/Clang broken)** | Use `--ocr command --ocr-command '<cmd> {input}'` with any external OCR tool | Set `ocr.engine: "unavailable"`, leave `problem.md` as draft, ask user for manual transcription |
| F6 | **Library path missing or corrupted** | Run `kb.py init` — re-creates directory structure without touching `entries/` | If `entries/` also gone, restore from latest `output/` export |
| F7 | **Visual-review adapter unavailable and model cannot inspect images** | `process_uploads.py start --vision-capability unavailable` → state is `needs-source-review`; generate `source-review.md` | Human compares `source-review.md` with original, corrects `problem.md`, runs `approve-source` |
| F8 | **Browser runtime check fails for simulator** | Rebuild simulator with corrected model; re-run `prepare-visualization --runtime-check auto` | Record as `skipped` with explicit reason in manifest; never equate static pass with runtime pass |
| F9 | **process_uploads.py not found or incompatible** | Verify `<skill-dir>/scripts/process_uploads.py` exists and Python ≥ 3.10 | Fall back to manual `kb.py` step-by-step: ingest → validate → finalize → export; mark entry for later re-processing |

## Generated variants

Retrieve the parent error, identify the transferable misconception, change at least two of representation/unknown/context/constraint/reasoning direction, solve and verify the new question, then store it with `kind=generated` and `generated_from` parent IDs. Question and full answer must be delivered together.

## Anti-pattern blacklist（不要做这些）

| # | 反模式 | 为什么不要做 | 正确做法 |
|---|--------|-------------|---------|
| A1 | **自己实现 Canvas/ZIP 仿真逻辑** | 职责越界，与 `build-physics-simulator` 冲突 | delegate 给 `build-physics-simulator`；本 skill 只协调 physics-model.json 和审批流水线 |
| A2 | **仅凭 OCR 文本就通过 source review** | OCR 经常搞错数字/下标/运算符；不核对的解析基于错误题目 | visual-review agent/adapter 或人工 approve-source 后才能把 `ocr.review_required` 置为 false |
| A3 | **声称 vision-capability=available 但模型不能看图** | 跳过人工核对门禁，错题入库基于未验证的 OCR | 如实声明模型能力；`auto` fail-closed → 走人工路径 |
| A4 | **把手写批改当标准答案** | 学生原笔迹和红笔批改都可能有误 | 独立求解；只在 `student_error` 字段引用手写作为参考 |
| A5 | **在 finish 时重建仿真而非复制已审批版本** | 审批后的 staged simulator 和 finish 时重建的可能不同 | `finish --simulator auto` 必须复制审批过的 staged bytes |
| A6 | **把答案 SVG 塞进可视化页面** | answer SVG 和 simulator HTML 是不同产物，混用导致审批链混乱 | answer SVG 留在答案审批内；可视化页面只放 simulator 产物 |
| A7 | **推断学生错因** | 从红叉/划痕推测思维过程不可靠 | 标记 `待学生确认`；只记录可观察的错误类型 |
| A8 | **混用 `\(...\)` 和 `$...$` 数学分隔符** | 部分渲染器不识别 `\(...\)` | 统一用 `$...$`（行内）和 `$$...$$`（独立公式） |
| A9 | **在 solution 中重复贴原题图** | `problem.md` 已展示原题；合并输出会出现同一张图两次 | solution 只放**新**的示意图（轨迹/受力分析 SVG），不放 `original.jpg` |
| A10 | **用坐标法+多行代数展开推导几何关系** | 切线交点定理一行出结果；坐标法掩盖几何同构性 | 几何优先：$r=R_{边界}\tan(\theta/2)$，弦长公式，矢量图 |

## Completion gate

A question is complete only when:

1. source review is resolved;
2. the teacher approved the current answer digest; changing an answer, shared model, or locally referenced answer image invalidates that approval;
3. for model-backed entries only, the teacher separately approved the current staged simulator digest; changing the model, HTML, ZIP, runtime evidence, or build report invalidates it;
4. answer and images validate;
5. `record.json` is ready and indexed;
6. PDF status is recorded, including a skip reason when unavailable;
7. a required simulator passes model and static validation, and its browser runtime check is recorded as `passed` or explicitly `skipped` because the dependency is unavailable;
8. `finish` copies the teacher-approved staged simulator bytes instead of rebuilding them;
9. `delivery-manifest.json` and `student-package.zip` exist.

Report entry ID, OCR engine, answer summary, output directory, PDF status, simulator status, package path, and unresolved fields.
