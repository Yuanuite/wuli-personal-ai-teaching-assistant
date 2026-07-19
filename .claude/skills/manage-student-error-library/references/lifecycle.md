# End-to-end question lifecycle

## State machine

```text
uploaded → ingested → source-reviewed → analyzed → answered → answer-reviewed
         → [teacher explicitly requests interactive visualization
            → model-created → answer-re-reviewed → visualization-built → visualization-reviewed]
         → validated → delivered → reviewed
```

The entry owns `pipeline.json`, which records the current state and artifacts. State may advance only when the preceding gate is satisfied.

## Natural-language trigger

For requests such as “处理现在新上传的题目”:

1. Run `process_uploads.py start` against `error-collection/`.
2. Declare whether the reasoning model can inspect images. Resolve every source through a vision-capable agent, a configured visual-review adapter, or `source-review.md` plus explicit human approval; never approve OCR from text alone.
3. Search prior ready entries, classify knowledge points and observable error types, and solve independently.
4. Write the layered answer and its static reasoning image. The default analysis pass does not create an interactive model, even when the process appears visualizable.
5. Have the teacher review both answer layers and their referenced images, then run `approve-answer`. If revision is requested, return to analysis; the workbench may send the scoped request to a local Agent, but the teacher must approve the result again. Changing Markdown, the shared model, or a referenced SVG/PNG invalidates the prior approval.
6. Keep the optional visualization page visible. Without a model it says “not generated” and offers a prompt/button such as “我想为这道题生成一个可视化结果”. Only that explicit teacher action invokes `build-physics-simulator`. After model creation, return to answer review because the shared digest changed, then build and approve the staged interactive simulator. A repair request returns to model editing and deterministic rebuilding. Do not promote the answer SVG into this page.
7. Run `process_uploads.py finish <entry-id> --simulator auto`. It must copy the approved staged simulator rather than build a different last-minute version.
8. Optional public publication: create separate cropped/redacted WebP copies of selected source pages and require teacher approval of their digests; then generate a sanitized `publication-draft/`, let the teacher inspect it, record final privacy approval, and copy only allowlisted files into `student-site/`. Do not auto-push GitHub.
8. Open the reported PDF and simulator screenshot when available. Report only the artifacts listed in `delivery-manifest.json`.

Do not stop between steps merely to ask routine questions. Stop only when the source is genuinely ambiguous, remote upload needs authorization, or a required external dependency has no safe fallback.

For a text-only reasoning model, use:

```bash
python3 <skill-dir>/scripts/process_uploads.py --library <library> \
  start --input <uploads> --vision-capability unavailable
```

If no adapter is configured, the returned state is `needs-source-review`. After a human compares `source-review.md` with the original and corrects `problem.md`, approve it explicitly:

```bash
python3 <skill-dir>/scripts/process_uploads.py --library <library> \
  approve-source <entry-id> --reviewer teacher
```

After the answer is generated and reviewed, run the following visualization commands only when `physics-model.json` exists:

```bash
python3 <skill-dir>/scripts/process_uploads.py --library <library> \
  approve-answer <entry-id> --reviewer teacher --note "已核对结论、步骤和图像"

python3 <skill-dir>/scripts/process_uploads.py --library <library> \
  prepare-visualization <entry-id> --runtime-check auto

python3 <skill-dir>/scripts/process_uploads.py --library <library> \
  approve-visualization <entry-id> --reviewer teacher --note "已核对轨迹、方向、关键事件和交互"
```

## Delivery contract

Each completed entry produces one human-readable output directory containing:

- student and teacher Markdown;
- student PDF when the PDF toolchain is available;
- `simulation/` with offline HTML/ZIP when applicable;
- `student-package.zip`, with ASCII member names and local assets;
- `delivery-manifest.json`, including answer, PDF, static simulator, browser runtime, and skipped-artifact reasons.

For a simulator, copy `runtime_check` from `simulation-build.json` into the delivery manifest. A browser-reported page/control failure blocks delivery. A missing browser dependency may be recorded as `skipped` with an explicit reason; never silently equate a static pass with a runtime pass.
