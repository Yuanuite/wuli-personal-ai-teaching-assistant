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
| `POST /api/agent/model-registry/test` | 保存当前本地模型设置，并对指定 `model_id` 做不含学生数据的连通测试 |
| `GET /api/jobs/<job-id>` | 轮询后台 Agent 作业 |
| `GET /api/jobs?entry_id=<entry-id>` | 找回某题最新后台作业 |
| `GET /api/entries` | 条目摘要及本地文件夹分组 |
| `GET /api/entries/<entry-id>` | 单题题干、分层答案、复核状态、仿真、发布和下载信息 |
| `GET /api/entry-file/<entry-id>/<relative>` | 查看条目内经过路径约束的文件 |
| `GET /api/visualization/<entry-id>/physics-simulator.html` | 查看预审交互仿真 |
| `GET /api/visualization/<entry-id>/runtime-check.png` | 查看仿真运行检查截图 |
| `GET /api/public-preview/<entry-id>/<relative>` | 查看尚未发布的学生端草稿 |
| `GET /api/public-site/<relative>` | 查看本地已发布静态站文件 |
| `GET /api/download/<entry-id>/<relative>` | 下载交付清单白名单内的成品 |

文件接口会解析并检查规范路径，禁止跳出各自的条目、草稿、公开站或交付目录。可视化接口只允许两个固定文件名；交付下载还必须同时出现在 `delivery.json.files` 与教师端 `DELIVERY_CATALOG` 中。

主动探测 provider：

```text
POST /api/agent/providers/probe
```

该写操作同样要求 `X-Teacher-Console: 1`。请求体可含 `provider` 和 `timeout_seconds`（10–120 秒）；探测使用空临时目录与固定提示，不发送学生材料，也不允许读写文件。返回值新增 `live_probe.status`、provider、原因和 `student_data_sent=false`。失败会让该 provider 暂时熔断，`auto` 改选下一项；它比只检查版本/help 更能发现认证、模型版本或网络问题。

保存本地模型注册表：

```bash
curl -fsS -H 'X-Teacher-Console: 1' -H 'Content-Type: application/json' \
  -d @student-error-library/config/model-registry.json \
  http://127.0.0.1:8787/api/agent/model-registry
```

该接口只保存 provider、base URL、模型名、用途标签、能力声明和默认模式映射；不得提交 API key 明文。

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
| `approve-source` | `problem`、`reviewer`、`note` | 保存并批准正式题干；仍含待核对内容时由生命周期拒绝 |
| `analyze` | 可选 `instruction`、`routing_tier` | 创建 `analysis.generate` 后台作业；不会自动生成交互仿真 |
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

模型注册表是本地私有配置。`POST /api/agent/model-registry` 可以为 OpenAI-compatible 模型提交 `api_key`，后端会写入已忽略的 `student-error-library/config/model-registry.json`；再次读取时只返回 `api_key_saved` 和 `api_key_configured`，不会返回明文。提交空 `api_key` 会保留旧 key，提交 `clear_api_key=true` 才会清除旧 key。

可选模型默认不会仅因存在配置而被调用。`POST /api/agent/model-registry/test` 接收 `{model_id, settings, timeout_seconds}`，先保存 `settings`，再以 `gateway.probe` 做不含学生数据的真实连通测试；测试通过后保存 `probe.status=passed`。后续解析、修改和可视化任务只会调用测试通过且配置未变化的模型。

### 后台作业响应

三个 Agent 动作成功提交后返回：

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

轮询结果状态为 `queued`、`running`、`completed` 或 `failed`。只有 `completed` 且 `result.status=completed` 才表示候选已通过 Gateway 并提升；之后仍必须按题目状态重新进行教师复核。同一题存在运行中作业时，其他写操作返回 `409`。服务重启会把旧 `queued/running` 作业标记为 `failed`，不会自动重放。

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
