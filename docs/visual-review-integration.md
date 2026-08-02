# 悟理视觉复核边车接入指南

## 适用场景

当 Claude Code 使用 DeepSeek 等纯文本模型时，让主模型继续负责分析和解题，把原图读取交给独立多模态服务。视觉边车只负责忠实转写题干和提取图形事实，不负责解题。

视觉边车与教师端 Agent Gateway 都采用“结构化请求 → 结构化结果”的适配器思路，但信任语义不同：Gateway provider 只生成答案或物理模型候选，视觉边车结果必须继续满足本文件的 `source_review` 协议。某个 provider 能处理图片，不代表它可以自动批准原题。

## 教师端默认数据流（模型注册表）

```text
教师端上传
  → process_uploads.py 建立 needs-source-review 条目
  → AgentGateway.extract_visual_facts
  → model_config_for_trait("vision")
  → MiMo OpenAI-compatible /chat/completions
  → wuli.visual-facts.v1
  → wuli.visual-facts-gate-result.v1
  → visual_source_review.py 暂存 problem.md 候选和复核单
  → 始终等待教师“确认题干”
```

模型调用失败、返回非法 JSON、存在不确定项或低置信度事实时，生命周期保持
`needs-source-review`。即使视觉事实门控为 `passed`，也只表示机器视觉候选满足
本轮提取要求，不等于教师已经批准题干。

教师端默认视觉模型由本地模型注册表的 `defaults.vision` 决定；当前明确指向
`mimo-v2.5-flash`。真实运行身份来自 Gateway trace，不能由模型输出自报。
这是本地稳定路由 ID；小米平台当前 `/models` 返回并接受的上游 API 名称是
`mimo-v2.5`，两者会分别记录为 `model_id` 与 `upstream_model`，不得混为同一字段。

CLI 与网页共用同一条视觉提取编排：默认 `source_review.mode=registry` 时，
`process_uploads.py start` 只调用确定性薄 CLI
`teacher-console/scripts/entry_visual_extract.py`，该入口与网页上传走同一个
`extract_visual_facts → stage_visual_extraction`，产出同形
`visual-facts.json` / `visual-facts-gate.json` / `source-review` 工件，且永不
授予来源批准。旧 `VISUAL_REVIEW_*` 命令行边车保留为显式兼容 override
（`--source-review-mode adapter` 或薄 CLI `--legacy-adapter`），记录
`legacy-adapter`，不再是默认路由。

## 视觉模型探针（wuli.vision-probe.v1）

视觉路由采用 fail-closed 探针策略：

- `resolve_model_id_for_trait` 与 `model_config_for_trait` 校验候选模型必须声明
  `traits.vision=true`；cost tier（economy/expert）覆盖仅在候选声明该 trait 时
  生效，`vision + economy` 不会解析到纯文本模型，显式指定文本模型直接失败；
- 视觉探针使用与生产提取相同的 endpoint、图片消息格式和 JSON 返回契约，用
  `teacher-console/tests/fixtures/visual-routing/` 下的合成图片执行（禁止使用
  真实学生图片）；
- 探针失败（endpoint 404、鉴权、非法 JSON、图片不可读）持久化为
  `probe.vision`，模型公开状态显示 `vision_probe=failed`，并被排除出视觉路由；
  未测试（`untested`）不阻断，但生产提取仍会在端点不兼容时失败关闭并保留
  可复核条目。

## 旧 CLI 边车接入（兼容）

准备一个支持图片输入的 OpenAI-compatible 服务，然后设置：

```bash
export VISUAL_REVIEW_BASE_URL="http://127.0.0.1:PORT/v1"
export VISUAL_REVIEW_MODEL="YOUR_VISION_MODEL"
```

直接调用自带适配器（显式 override）：

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

或对已有条目复用薄 CLI：

```bash
python3 teacher-console/scripts/entry_visual_extract.py <entry-id> \
  --legacy-adapter "python3 .../openai_compatible_vision_adapter.py"
```

需要长期启用时，将 `source_review.mode`、`adapter_command` 和 `adapter_locality`
写入 `student-error-library/config.json`（当前默认为 `mode=registry`）。端点、
模型名和密钥继续保留在环境变量中。

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

旧边车标准输出只能包含一个 JSON 对象。诊断信息写到 stderr：

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

## 教师端冒烟检查

接入后用一张不含真实学生信息的测试图运行 `start`。成功标准：

- `visual-facts.json` 和 `visual-facts-gate.json` 已生成；
- trace 的 `model_id=mimo-v2.5-flash`、`provider=openai-compatible`；
- `source-review.json.status=needs-review`；
- `record.json.ocr.review_required=true`；
- `record.json.source_review.status=needs-review`；
- `problem.md` 包含 MiMo 识别的题干和图形事实候选；
- work order 仍停在 `needs-source-review`，等待教师确认。

再测试一张模糊图。成功标准是 visual facts 保留不确定项，独立 gate 返回
`needs-source-review`，流程生成教师复核单而不是继续解题。

## 解题与 SVG 下游边界

教师批准题干后，`visual-facts.json` 会作为只读结构化上下文进入 W2/W3；DeepSeek
负责解题和 `diagram` 场景意图，确定性渲染器生成 `assets/explanatory.svg`。系统随后写入
`svg-provenance.json`，绑定视觉事实指纹、DeepSeek 生成指纹、模型注册表身份和 SVG 摘要。
MiMo 不再拥有直接生成或替换最终 SVG 的接口。安全检查或任一指纹不匹配时，候选事务失败，

静态物理图采用“事实—配方—编译—软复核”分工：MiMo 的已复核事实决定图中不能违背什么，DeepSeek Flash 选择并组合语义组件；若存在 `physics-model.json`，确定性编译器接管轨迹、事件、边界与坐标。MiMo 后续对可读性、辅助性、美观性和遮挡的判断只能形成提示式建议，不直接改 SVG，也不自动升级为硬门。硬门仅保留来源矛盾、物理拓扑矛盾、模型矛盾以及安全与 provenance。
canonical 条目保持不变。

## 常见错误

| 现象 | 原因与处理 |
|---|---|
| `visual adapter returned invalid JSON` | stdout 混入日志；日志改写 stderr |
| `passed review cannot contain uncertainties` | 不确定项存在时必须返回 `needs-review` |
| `remote visual review is disabled` | 尚未取得授权，或项目隐私门禁未开启 |
| `non-loopback visual endpoint requires...` | 边车脚本的第二道远程门禁未开启 |
| 自动回退 `source-review.md` | 查看 `adapter_error`，修复后重跑新题或由教师复核 |
