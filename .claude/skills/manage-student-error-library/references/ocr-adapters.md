# OCR adapters

## Default local OCR

On macOS, `kb.py ingest` invokes the bundled `vision_ocr.swift` through Apple Vision. It returns JSON containing recognized text, confidence, and normalized bounding boxes. No image leaves the machine.

Apple Vision is good for Chinese prose but can confuse formulas, subscripts, superscripts, handwritten marks, and diagram labels. Always perform visual correction.

## External command contract

Use a local executable or an explicitly authorized API wrapper:

```bash
python3 <skill-dir>/scripts/kb.py --library <library> ingest <image> \
  --ocr command --ocr-command '/absolute/path/to/adapter --input {input}'
```

The command must exit with code 0 and write one of these to stdout:

1. UTF-8 plain text; or
2. JSON with at least a top-level `text` string. Optional fields are `engine`, `average_confidence`, and `lines`.

The runner uses argument splitting without a shell. Do not depend on pipes, redirection, command substitution, or inline secrets.

## Remote privacy gate

Before using a cloud OCR/API wrapper:

1. Tell the user that the student's image will leave the device.
2. Obtain explicit authorization for that source.
3. Read credentials from the wrapper's secure environment; never save them in `config.json`, `record.json`, logs, or Markdown.
4. Store the returned OCR locally and keep the original image for audit.

If authorization is absent, keep `privacy.allow_remote_ocr` false and use local OCR/manual review.

## Visual-review adapter contract

OCR extracts characters; visual review verifies formula layout and diagram semantics. Keep them as separate adapters.

Configure a local executable or an explicitly authorized API wrapper through `source_review.adapter_command`, or pass `--visual-review-command`. The lifecycle sends one UTF-8 JSON object to the command's standard input:

```json
{
  "schema_version": 1,
  "entry_id": "...",
  "subject": "高中物理",
  "source_sha256": "...",
  "images": ["/absolute/local/path/original.png"],
  "ocr": {"engine": "apple-vision", "average_confidence": 0.82, "text": "..."},
  "required_checks": ["formula signs", "diagram arrows"]
}
```

The command must read stdin, exit with code 0, and write only one JSON object to stdout:

```json
{
  "review_status": "passed",
  "engine": "local-vlm",
  "reviewer": "visual-sidecar",
  "reviewed_text": "完整、校正后的题干",
  "diagram_facts": ["粒子带负电", "III 区磁场垂直纸面向外"],
  "uncertainties": [],
  "notes": ""
}
```

Rules:

- `review_status` is `passed` or `needs-review`.
- `passed` requires non-empty `reviewed_text` and an empty `uncertainties` array.
- Never print credentials or diagnostic prose to stdout; use stderr for diagnostics.
- The runner uses argument splitting without a shell and sends no credentials. Read credentials from the adapter's secure environment.
- Set `adapter_locality=remote` only after `privacy.allow_remote_visual_review=true` has been explicitly authorized.
- Adapter failure or uncertainty automatically produces a human `source-review.md`; it never silently falls back to OCR approval.

## Bundled OpenAI-compatible sidecar

`scripts/openai_compatible_vision_adapter.py` connects the protocol to an OpenAI-compatible multimodal `/chat/completions` endpoint using only the Python standard library. Point it at a local service with environment variables:

```bash
export VISUAL_REVIEW_BASE_URL="http://127.0.0.1:PORT/v1"
export VISUAL_REVIEW_MODEL="YOUR_VISION_MODEL"

python3 <skill-dir>/scripts/process_uploads.py --library <library> \
  start --input <uploads> --vision-capability unavailable \
  --source-review-mode adapter \
  --visual-review-command "python3 <skill-dir>/scripts/openai_compatible_vision_adapter.py" \
  --adapter-locality local
```

For a non-loopback endpoint, both privacy gates are required:

1. obtain explicit authorization and set `privacy.allow_remote_visual_review=true`;
2. set `VISUAL_REVIEW_ALLOW_REMOTE=true` in the wrapper environment and pass `--adapter-locality remote`.

Set `VISUAL_REVIEW_API_KEY` only in the secure environment. Never place it in the command, config, record, or logs.
