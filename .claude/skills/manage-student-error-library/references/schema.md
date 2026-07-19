# Local knowledge-base schema

## Directory layout

```text
student-error-library/
├── config.json
├── .cache/agent-jobs/            # private operational job state; never deliver or publish
├── folders/<display-name>/<entry-id>  # rebuildable relative link/pointer view
├── entries/<entry-id>/
│   ├── record.json
│   ├── problem.md
│   ├── solution.md              # synchronized teacher layer
│   ├── student-solution.md      # optional low-load layer
│   ├── publication-draft/       # optional private full-site preview; never publish as source
│   ├── publication-draft.json   # draft digest and sanitized artifact status
│   ├── publication-review.json  # teacher privacy approval and local-publication status
│   ├── publication-images.json  # selected pages, crop/redaction geometry, source/output digests, teacher approval
│   ├── publication-assets/      # generated public WebP copies; originals remain private
│   ├── teacher-solution.md      # optional audit layer
│   ├── physics-model.json       # optional shared physics source
│   ├── pipeline.json            # lifecycle state and gates
│   ├── delivery.json            # last successful delivery manifest
│   ├── ocr.json                 # imported sources only
│   ├── source-review.json       # visual/human review audit record
│   ├── source-review.md         # local human fallback packet
│   ├── answer-review.json       # teacher approval for current answer digest
│   ├── visualization-review.json # teacher approval for current visual artifact digest
│   ├── visualization-request.json # explicit teacher generation/repair request and Agent status
│   ├── visualization-conversation.json # local teacher/Agent repair history
│   ├── visualization/           # staged artifact reviewed before delivery
│   │   ├── physics-simulator.html
│   │   ├── physics-simulator.zip
│   │   └── simulation-build.json
│   └── assets/
│       ├── original.<ext>
│       └── explanatory.svg
└── indexes/catalog.json         # regenerated; never hand-edit
```

`pipeline.json` is owned by `process_uploads.py`; do not hand-advance its state. `delivery.json` is written only after entry validation and all selected specialist builds succeed.

Keep source artifacts and student data outside the Skill directory.

`config.json` privacy flags are independent capabilities. `privacy.allow_remote_agent` authorizes sending reviewed textual answer/model context to a non-loopback Agent provider only when its environment gate is also enabled; it does not authorize OCR, visual review, source-image upload, delivery, or publication. Agent job JSON is private, rebuildable operational evidence and must not be referenced by student artifacts.

## `record.json`

Use UTF-8 JSON. Preserve unknown values as empty strings or explicit `待学生确认`; do not fabricate them.

```json
{
  "schema_version": 1,
  "id": "20260717-example-ab12cd34",
  "kind": "error",
  "status": "needs-review",
  "answer_status": "pending",
  "created_at": "2026-07-17T10:00:00+08:00",
  "updated_at": "2026-07-17T10:00:00+08:00",
  "library_folder": "2026-07-17",
  "title": "带电粒子在复合场中的运动",
  "subject": "高中物理",
  "grade": "高三",
  "knowledge_points": ["洛伦兹力", "带电粒子圆周运动"],
  "error_types": ["符号判断", "几何关系"],
  "student_error": "待学生确认",
  "difficulty": "较难",
  "source": {
    "original_name": "question.jpg",
    "sha256": "...",
    "stored_files": ["assets/original.jpg"],
    "source_type": "jpg"
  },
  "ocr": {
    "engine": "apple-vision",
    "average_confidence": 0.82,
    "review_required": false
  },
  "source_review": {
    "status": "passed",
    "method": "visual-adapter",
    "engine": "local-vlm",
    "locality": "local",
    "reviewed_at": "2026-07-19T10:00:00+08:00",
    "input_digest": "..."
  },
  "answer_review": {
    "status": "passed",
    "reviewer": "teacher",
    "reviewed_at": "2026-07-19T10:30:00+08:00",
    "answer_digest": "...",
    "note": "已核对结论、步骤和图像"
  },
  "visualization_review": {
    "status": "passed",
    "reviewer": "teacher",
    "reviewed_at": "2026-07-19T10:40:00+08:00",
    "kind": "simulator",
    "artifact_digest": "...",
    "note": "已核对轨迹、方向、关键事件和交互"
  },
  "generated_from": [],
  "pedagogy": {
    "technique_ids": [],
    "student_main_steps": 0,
    "shared_model": "physics-model.json"
  },
  "review": {
    "mastery": 0,
    "next_review": "2026-07-17",
    "history": []
  }
}
```

### Field rules

- `kind`: `error` for imported wrong questions; `generated` for variants.
- `status`: `needs-review` until `kb.py finalize` succeeds; then `ready`.
- `answer_status`: `pending` until finalize; then `complete`.
- `knowledge_points`: use 2–6 curriculum-level concepts, most specific first.
- `error_types`: use observable categories such as `审题`, `概念`, `符号方向`, `公式选择`, `计算`, `图像理解`, `步骤缺失`, or `待确认`.
- `student_error`: separate observed written work from a hypothesized cause. Prefix hypotheses with `待学生确认：`.
- `generated_from`: list parent entry IDs for generated variants.
- `review.mastery`: integer 0–5 managed by the script.
- `pedagogy`: optional. `technique_ids` must refer to the conditional secondary-conclusion registry; `student_main_steps` counts only the visible main line; `shared_model` is present for model-backed physics entries.
- `source_review`: required for newly imported entries. `status` remains `needs-review` until a visual adapter returns an uncertainty-free pass or a human explicitly approves the corrected `problem.md`.
- `ocr.review_required=false` and `source_review.status=passed` must agree. OCR confidence alone cannot satisfy either condition.
- `answer_review`: required for newly imported entries before delivery. Approval hashes `problem.md`, both layered answers, `solution.md`, optional `physics-model.json`, and every local SVG/bitmap referenced by the answer layers; edits to any of them require a new approval.
- With no `physics-model.json`, visualization `kind` is `not-generated` and review status is `not-required`; this means no teacher request has produced a simulator, not that the question is unsuitable. Keep the page entry available.
- `visualization_review`: required only after an explicit teacher request has produced `physics-model.json`. It independently hashes the shared model, staged interactive HTML/ZIP, runtime evidence, and build report. Any simulator artifact change requires a new teacher approval. Static answer images never create this stage.
- `library_folder`: display grouping only. The canonical path remains `entries/<entry-id>`; renaming the folder must not change `updated_at` or review digests.
- For model-backed entries, `physics-model.json.source.answer_render_mode` is `model` by default. The teacher workbench sets it to `manual` after a Markdown edit and records `manual_answer_files`; finalization preserves those files until an explicit model regeneration resets the mode.

## Atomicity

Create one entry per independently answerable question. Subparts sharing one stem stay together. If one photo contains unrelated questions, derive separate entries while retaining the same source hash and original image reference.
