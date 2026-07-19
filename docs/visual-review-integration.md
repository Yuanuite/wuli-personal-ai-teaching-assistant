# 悟理视觉复核边车接入指南

## 适用场景

当 Claude Code 使用 DeepSeek 等纯文本模型时，让主模型继续负责分析和解题，把原图读取交给独立多模态服务。视觉边车只负责忠实转写题干和提取图形事实，不负责解题。

视觉边车与教师端 Agent Gateway 都采用“结构化请求 → 结构化结果”的适配器思路，但信任语义不同：Gateway provider 只生成答案或物理模型候选，视觉边车结果必须继续满足本文件的 `source_review` 协议。某个 provider 能处理图片，不代表它可以自动批准原题。

## 数据流

```text
process_uploads.py
  → 通过 stdin 发送原图路径、OCR 草稿和检查项
  → visual-review adapter
  → 本地或经授权的多模态 /chat/completions
  → stdout 返回统一 JSON
  → source_review.py 校验并推进或回退人工复核
```

适配器失败、返回非法 JSON、声明 `needs-review` 或包含任何不确定项时，生命周期保持 `needs-source-review`，并生成 `source-review.md`。

## 最快接入

准备一个支持图片输入的 OpenAI-compatible 服务，然后设置：

```bash
export VISUAL_REVIEW_BASE_URL="http://127.0.0.1:PORT/v1"
export VISUAL_REVIEW_MODEL="YOUR_VISION_MODEL"
```

直接调用自带适配器：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library \
  start --input error-collection \
  --vision-capability unavailable \
  --source-review-mode adapter \
  --visual-review-command \
  "python3 .claude/skills/manage-student-error-library/scripts/openai_compatible_vision_adapter.py" \
  --adapter-locality local
```

需要长期启用时，将 `source_review.mode`、`adapter_command` 和 `adapter_locality` 写入 `student-error-library/config.json`。端点、模型名和密钥继续保留在环境变量中。

## 适配器输入

生命周期向适配器标准输入发送一个 JSON 对象：

```json
{
  "schema_version": 1,
  "entry_id": "20260719-example-ab12cd34",
  "subject": "高中物理",
  "source_sha256": "...",
  "images": ["/absolute/local/path/original.png"],
  "ocr": {
    "engine": "apple-vision",
    "average_confidence": 0.82,
    "text": "OCR 草稿"
  },
  "required_checks": ["formula signs", "diagram arrows"]
}
```

图片路径是本地绝对路径，便于适配器读取；该请求和 `source-review.json` 不进入学生交付包。

## 适配器输出

标准输出只能包含一个 JSON 对象。诊断信息写到 stderr：

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

约束：

- `review_status` 只能是 `passed` 或 `needs-review`；
- `passed` 必须有非空 `reviewed_text`；
- `passed` 的 `uncertainties` 必须为空；
- 任何无法辨认的公式、箭头或边界都必须列入 `uncertainties`；
- 不得把学生手写解答混进正式题干。

## 环境变量

| 变量 | 必需 | 说明 |
|---|---|---|
| `VISUAL_REVIEW_BASE_URL` | 是 | `/v1` 基地址；脚本默认只允许 localhost、127.0.0.1、::1 |
| `VISUAL_REVIEW_MODEL` | 是 | 多模态模型名 |
| `VISUAL_REVIEW_API_KEY` | 远程通常需要 | Authorization Bearer；禁止落盘 |
| `VISUAL_REVIEW_TIMEOUT_SECONDS` | 否 | 默认 `120` |
| `VISUAL_REVIEW_ALLOW_REMOTE` | 非回环端点需要 | 必须为字符串 `true` |

## 远程隐私门禁

学生图片离开本机前必须取得明确授权，并同时满足：

1. `student-error-library/config.json` 中 `privacy.allow_remote_visual_review=true`；
2. 进程环境中 `VISUAL_REVIEW_ALLOW_REMOTE=true`；
3. `process_uploads.py start` 使用 `--adapter-locality remote`，或配置等价属性。

只满足其中一项不会上传。OCR 的远程授权不能自动授权视觉复核。

## 冒烟检查

接入后用一张不含真实学生信息的测试图运行 `start`。成功标准：

- `source-review.json.status=passed`；
- `record.json.ocr.review_required=false`；
- `record.json.source_review.status=passed`；
- `problem.md` 包含正式题干和图形事实；
- work order 进入 `needs-analysis-and-answer`。

再测试一张模糊图。成功标准是适配器返回 `needs-review`，流程生成教师复核单，而不是继续解题。

## 常见错误

| 现象 | 原因与处理 |
|---|---|
| `visual adapter returned invalid JSON` | stdout 混入日志；日志改写 stderr |
| `passed review cannot contain uncertainties` | 不确定项存在时必须返回 `needs-review` |
| `remote visual review is disabled` | 尚未取得授权，或项目隐私门禁未开启 |
| `non-loopback visual endpoint requires...` | 边车脚本的第二道远程门禁未开启 |
| 自动回退 `source-review.md` | 查看 `adapter_error`，修复后重跑新题或由教师复核 |
