# 悟理教师工作台 API

## 使用边界

该 API 是 `teacher-console/` 页面与本地生命周期脚本之间的内部接口，不是面向公网的开放 API，也不供 `student-site/` 调用。服务只允许监听回环地址，不提供局域网/公网模式。

启动：

```bash
python3 teacher-console/server.py
```

所有写操作必须携带请求头：

```text
X-Teacher-Console: 1
```

缺少该请求头时返回 `403`。流程门禁未满足时，操作通常返回 `409`，JSON 中的 `status` 为 `blocked` 并附带 `errors`；后台 Agent 动作成功入队时返回 `202`。页面和外部调用方不得把 `blocked`、`failed` 或仅仅“已入队”当作教学内容成功。

## 只读接口

| 方法与路径 | 作用 |
|---|---|
| `GET /api/health` | 服务状态、项目位置、选中 provider、版本、能力、数据位置和本地模型注册表 |
| `GET /api/agent/providers` | 当前 Gateway provider 探测快照 |
| `GET /api/agent/model-registry` | 读取本地模型注册表设置（不回显 API Key 明文） |
| `GET /api/agent/runtime` | 读取本机 Codex/代理设置、可执行文件候选和本地代理探测结果 |
| `POST /api/agent/model-registry/test` | 保存当前本地模型设置，并对指定 `model_id` 做不含学生数据的连通测试 |
| `GET /api/jobs/<job-id>` | 轮询后台 Agent 作业 |
| `GET /api/jobs?entry_id=<entry-id>` | 找回某题最新后台作业 |
| `GET /api/entries` | 条目摘要及本地文件夹分组 |
| `GET /api/entries/<entry-id>` | 单题题干、分层答案、复核状态、仿真、发布和下载信息 |
| `GET /api/retrieval-review` | 读取本地固定检索集、复核统计和可勾选题目卡片；只含题干摘要与题图，不含教师解析 |
| `GET /api/entry-file/<entry-id>/<relative>` | 查看条目内经过路径约束的文件 |
| `GET /api/visualization/<entry-id>/physics-simulator.html` | 查看预审交互仿真 |
| `GET /api/visualization/<entry-id>/runtime-check.png` | 查看仿真运行检查截图 |
| `GET /api/public-preview/<entry-id>/<relative>` | 查看尚未发布的学生端草稿 |
| `GET /api/public-site/<relative>` | 查看本地已发布静态站文件 |
| `GET /api/download/<entry-id>/<relative>` | 下载交付清单白名单内的成品 |

文件接口会解析并检查规范路径，禁止跳出各自的条目、草稿、公开站或交付目录。可视化接口只允许两个固定文件名；交付下载还必须同时出现在 `delivery.json.files` 与教师端 `DELIVERY_CATALOG` 中。

检索评测复核通过顶部“检索评测”进入。保存单条复核使用 `POST /api/retrieval-review/save`，请求体包含 `id`、`query`、`category`、`relevant_entry_ids` 和 `review_status`；批准状态至少要勾选一道 `ready/delivered` 题目。该接口只原子更新被 Git 忽略的 `student-error-library/evals/retrieval-cases.jsonl`，不调用 Agent、不改变条目审批，也不会进入 `student-site/`。

主动探测 provider：

```text
POST /api/agent/providers/probe
```

该写操作同样要求 `X-Teacher-Console: 1`。请求体可含 `provider`、`timeout_seconds`（10–120 秒）和 `require_file_tools`。默认探测使用空临时目录与固定提示，不发送学生材料，也不允许读写文件；`require_file_tools=true` 时只允许在一次性目录写入固定的 `gateway-probe.txt`，并拒绝额外文件变化。返回值包含 `live_probe.status`、provider、原因、`capability` 和 `student_data_sent=false`。它比只检查版本/help 更能发现认证、模型版本、网络或 CLI 工具能力问题。

网页运行环境接口：

```text
POST /api/agent/runtime
POST /api/agent/runtime/diagnose
```

两者都要求本地写操作请求头。前者保存 `codex_path` 与 `proxy.mode`（`inherit/direct/manual`）；`manual` 只接受无凭据的本机回环 URL。后者可在 `settings` 字段中携带同一配置，保存后立即执行不含学生数据的 Codex 真实推理。结果包含稳定的 `diagnosis.code`、教师可读说明和 `student_data_sent=false`。配置与检测摘要仅写入被忽略的 `student-error-library/config/agent-runtime.json`，无需重启工作台。

保存本地模型注册表：

```bash
curl -fsS -H 'X-Teacher-Console: 1' -H 'Content-Type: application/json' \
  -d @student-error-library/config/model-registry.json \
  http://127.0.0.1:8787/api/agent/model-registry
```

该接口保存 provider、base URL、模型名、用途标签、能力声明和默认模式映射。设置页也可提交 API Key，后端只把它写入被 Git 忽略的本机注册表且读取时不回显；命令行接入优先使用 `api_key_env`，不要把含明文 Key 的请求体保存在脚本、shell 历史或仓库文件中。

## 上传与文件夹接口

| 方法与路径 | 请求内容 | 作用 |
|---|---|---|
| `POST /api/upload?filename=<name>` | 原始文件字节 | 上传 JPG、PNG、WebP、HEIC、TIFF、BMP 或 PDF |
| `POST /api/run-upload` | `filename`、可选 `ocr`、`subject`、`vision_capability` | 将已经上传的文件创建为知识库条目 |
| `POST /api/folders/rename` | `old_name`、`new_name` | 重命名本地同步视图中的文件夹 |

文件夹重命名不会移动 `entries/<entry-id>/` 真源。

## 单题动作接口

统一路径为：

```text
POST /api/entries/<entry-id>/<action>
```

| `action` | 关键请求字段 | 说明 |
|---|---|---|
| `rename-entry` | `title` | 修改当前条目标题，直接更新 `record.json` 并刷新索引；最长 120 字符 |
| `source-clean` | 可选 `routing_tier`、`model_id` | 创建 `source.clean` 后台作业，让 Agent 修正 OCR 草稿并从题干提取内容相关标题；默认走 economy 档 |
| `approve-source` | `problem`、`reviewer`、`note` | 保存并批准正式题干；仍含待核对内容时由生命周期拒绝 |
| `analyze` | 可选 `instruction`、`routing_tier` | 创建 `analysis.generate` 后台作业；模型返回结构化解析，程序确定性生成学生版、教师版、兼容版和解释 SVG；不会自动生成交互仿真。若存在与当前输入匹配的生成检查点，优先零 Token 恢复 |
| `save-answer` | `layer`、`markdown`、可选 `base_digest` | 保存学生版或教师版 Markdown，并撤销旧答案批准 |
| `approve-answer` | `reviewer`、`note` | 批准当前题干、答案、模型和引用图片的联合摘要 |
| `request-revision` | 修改意见、可选 `routing_tier` 及页面提供的版本摘要 | 创建 `answer.revise` 后台作业，在隔离区返修答案和解释图 |
| `build-visualization` | 可选 `message`、`runtime_check`、`routing_tier` | 无模型时创建 `visualization.model` 作业；有模型时直接确定性构建预审仿真 |
| `approve-visualization` | `reviewer`、`note` | 批准当前模型、HTML/ZIP 和运行证据摘要 |
| `visualization-chat` | `message`、可选 `base_digest`、`routing_tier` | 创建后台作业，请求生成或修复当前题目的模型候选 |
| `clear-visualization-chat` | 无 | 清空当前题目的可视化对话记录 |
| `finish` | 可选 `simulator` | 校验并生成 Markdown、PDF、学生包和交付清单 |
| `save-publication-images` | `pages`、`privacy_confirmed: true`、`reviewer`、`note` | 保存裁剪和遮挡后的公开题图副本 |
| `prepare-publication` | 无 | 从已交付白名单产物生成学生端草稿并执行安全扫描 |
| `publish-publication` | `privacy_confirmed: true`、`reviewer`、`note` | 将已复核草稿复制到本地 `student-site/` |

教师批准与隐私确认必须来自实际页面使用者或明确的人工操作。Agent 可以生成和返修，但不得代填批准或绕过 `409 blocked`。

`routing_tier` 可取 `auto`、`economy`、`expert`，省略时为 `auto`；页面里的“自定义”会转换为 `routing_tier=auto` 并携带具体 `model_id`。请求也可携带 `model_id`：`auto` 表示沿用 Gateway 自动 provider/档位路由，或按注册表默认模式解析；其他值必须存在于 `student-error-library/config/model-registry.json`，且能力声明支持当前任务。后台作业公开结果可包含 `requested_tier`、`model_tier`、`model_id`、`model_display_name`、`model`、`usage` 与诚实降级说明 `routing_notice`；这些是成本审计信息，不代表内容已获批准。

模型注册表是本地私有配置。`POST /api/agent/model-registry` 可以为 OpenAI-compatible API 或 Claude Code Agent 模型提交 `base_url`、`model` 与 `api_key`，后端会写入已忽略的 `student-error-library/config/model-registry.json`；再次读取时只返回 `api_key_saved` 和 `api_key_configured`，不会返回明文。Claude Code Agent 的地址与认证只注入该次子进程，不改写 `~/.claude/settings.json`。提交空 `api_key` 会保留旧 key，提交 `clear_api_key=true` 才会清除旧 key。

可选模型默认不会仅因存在配置而被调用。`POST /api/agent/model-registry/test` 接收 `{model_id, settings, timeout_seconds}`，先保存 `settings`，再以 `gateway.probe` 做不含学生数据的真实连通测试；测试通过后保存 `probe.status=passed`。后续解析、修改和可视化任务只会调用测试通过且配置未变化的模型。

这里的 provider 是运行时，不是模型厂商。`provider=claude` 可在该次子进程中连接教师配置的兼容后端；`analysis.generate` 即使由 Claude/Codex 执行也会关闭工具并返回结构化对象，答案返修和可视化才允许受限文件工具。`provider=openai-compatible` 始终没有本地文件工具。详见 [agent-gateway.md](agent-gateway.md#运行时模型与工具不是同一层)。

### 后台作业响应

三个 Agent 动作（`source-clean`、`analyze`、`request-revision`）以及可视化动作（`build-visualization`、`visualization-chat`）成功提交后返回：

```json
{
  "status": "queued",
  "job": {
    "id": "hex-job-id",
    "kind": "answer.revise",
    "entry_id": "entry-id",
    "status": "queued",
    "url": "/api/jobs/hex-job-id"
  }
}
```

轮询结果状态为 `queued`、`running`、`completed` 或 `failed`。只有 `completed` 且 `result.status=completed` 才表示候选已通过 Gateway 并提升；之后仍必须按题目状态重新进行教师复核。失败结果可带稳定的 `failure_type`，同时保留 `message`、`validation_errors` 和 `unauthorized_changes` 供教师排障。同一题存在运行中作业时，其他写操作返回 `409`。服务重启会把旧 `queued/running` 作业标记为 `failed`、`failure_type=worker_interrupted`，不会自动重放。

## 最小调用示例

```bash
curl -fsS http://127.0.0.1:8787/api/health

curl -fsS \
  -H 'X-Teacher-Console: 1' \
  -H 'Content-Type: application/json' \
  -d '{"provider":"codex","timeout_seconds":120}' \
  http://127.0.0.1:8787/api/agent/providers/probe

curl -fsS \
  -H 'X-Teacher-Console: 1' \
  -H 'Content-Type: application/json' \
  -d '{"reviewer":"teacher","note":"已核对当前版本"}' \
  http://127.0.0.1:8787/api/entries/<entry-id>/approve-answer
```

上传文件时应把文件内容作为请求体，并对 `filename` 做 URL 编码。不要把学生原图、内部条目路径或教师版答案发送到学生静态站。

## 推荐调用顺序

```text
upload → run-upload → approve-source → analyze
→ save-answer（如需人工编辑）→ approve-answer
→ [build-visualization → approve-answer → approve-visualization]
→ finish
→ [save-publication-images → prepare-publication → publish-publication]
```

方括号阶段为按需步骤。仿真模型创建后会改变答案联合摘要，因此必须重新批准答案；公开发布属于交付后的独立隐私门禁，不改变题目的 `delivered` 状态。
