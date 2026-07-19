# 悟理运行与排障手册

## 正常入口

把图片或 PDF 放入 `error-collection/`，然后对 Agent 说“处理现在新上传的题目”。除原图确实歧义、远程上传需要授权或必要依赖没有安全降级外，流程不应在中间要求例行确认。

也可以启动教师工作台：

```bash
python3 teacher-console/server.py
```

浏览器打开 <http://127.0.0.1:8787/>。它是本地服务而不是公网网站；终端关闭后服务也会停止。若 8787 被占用，可使用 `--port 8788` 并打开对应端口。为安全起见，非回环地址默认拒绝启动。

需要用其他本地页面或脚本调用工作台时，接口清单、`X-Teacher-Console` 写操作请求头和流程顺序见 [`teacher-console-api.md`](teacher-console-api.md)。学生静态站不调用这些接口。

“生成解析”会优先调用 `TEACHER_CONSOLE_AGENT_COMMAND`，否则自动检测 Codex、再检测 Claude Code。自定义命令中的 `{entry}` 与 `{entry_id}` 会替换为当前条目；按钮不会绕过来源复核，也不会替教师批准答案。未生成合格答案时，页面保持失败/待处理状态。

题干和答案使用本地 Markdown + KaTeX 实时预览。答案编辑后按“保存当前 Markdown”或 `Cmd/Ctrl+S`；保存会撤销旧答案批准并自动重建检索。“交给大模型修改”会把意见限定在当前条目的分层答案和引用解释图内，完成后仍需教师重新批准。标准解析默认不生成交互仿真；答案批准后进入始终保留的“可视化（可选）”页面。没有模型时可直接输入“我想为这道题生成一个可视化结果”或点击“调用 Skill 生成”，之后 Agent 才调用仿真 Skill。新模型会使答案摘要失效，需要先重新复核答案，再批准 iframe 中的动态结果。静态 SVG 仍在解析复核中查看和修改。

左栏来自 `student-error-library/folders/` 的本地同步视图。首次启动会按 `created_at` 建立日期文件夹；网页改名会原子同步本地目录。真正的条目仍固定保存在 `entries/<entry-id>/`，文件夹中使用相对软链接（不支持软链接的平台使用指针），因此改名不会破坏图片、检索或旧 URL。

## 手动命令

以下命令均从项目根目录执行：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library \
  start --input error-collection --subject 高中物理
```

查看全部或单题状态：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library status

python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library status <entry-id>
```

在完成原图核对、记录分类、分层答案和解释图后先批准答案：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library approve-answer <entry-id> \
  --reviewer teacher --note "已核对结论、步骤和图像"

# 教师明确请求并由 Agent 创建 physics-model.json 后，执行下面两步。
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library prepare-visualization <entry-id> \
  --runtime-check auto

python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library approve-visualization <entry-id> \
  --reviewer teacher --note "已核对物理阶段、轨迹、文字与交互控件"

python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library finish <entry-id> --simulator auto
```

`--simulator required` 要求必须存在并成功构建仿真；`--simulator skip` 明确不构建仿真。

## 发布只读学生端

网页方式：题目完成“生成最终文件”后，在“交付下载”底部先检查自动建议裁剪；用四边滑杆调整范围，在图上拖拽遮挡姓名、学校、二维码或不应公开的笔迹，并逐页选择是否加入。确认公开题图后再点击“生成公开预览”，逐页检查，最后填写复核人、勾选隐私确认并发布到本地学生站。原图不会被修改，该按钮也不会推送 GitHub。

对应手动命令：

```bash
python3 .claude/skills/manage-student-error-library/scripts/public_site.py init
python3 .claude/skills/manage-student-error-library/scripts/public_site.py prepare <entry-id>
python3 .claude/skills/manage-student-error-library/scripts/public_site.py publish <entry-id> \
  --reviewer teacher --note "已检查公开内容与隐私"
```

本地检查可从项目根目录运行 `python3 -m http.server 8000 --directory student-site`，打开 <http://127.0.0.1:8000/>。确认后，把 `student-site/` 单独初始化为公开 GitHub 仓库并在仓库 Settings → Pages 选择从主分支根目录部署。不要把项目根目录、`student-error-library/` 或 `output/` 一起提交。每次新增公开题目后仍需人工执行 Git 提交与推送。

## DeepSeek 等无视觉模型

显式声明主模型不能识图：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library start --input error-collection \
  --vision-capability unavailable
```

未配置视觉适配器时，打开条目里的 `source-review.md`，对照原图修正 `problem.md`，然后由实际查看原图的人执行：

```bash
python3 .claude/skills/manage-student-error-library/scripts/process_uploads.py \
  --library student-error-library approve-source <entry-id> \
  --reviewer teacher --note "已核对公式、方向和全部小问"
```

若已配置视觉边车，在 `student-error-library/config.json` 中设置：

```json
{
  "source_review": {
    "mode": "adapter",
    "adapter_command": "/absolute/path/to/visual-review-wrapper",
    "adapter_locality": "local"
  }
}
```

命令协议见 Skill 的 `references/ocr-adapters.md`。命令中不要写密钥；远程适配器还需单独把 `privacy.allow_remote_visual_review` 设为 `true`，且只能在用户明确授权上传学生图片后设置。

项目已提供 `openai_compatible_vision_adapter.py`。它可以连接本机 OpenAI-compatible 多模态服务；只需通过环境变量提供端点和模型名，不需要在项目中保存密钥。非本机端点具有脚本和生命周期两道独立授权门禁。

### 视觉边车环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VISUAL_REVIEW_BASE_URL` | 无 | OpenAI-compatible 服务的 `/v1` 基地址 |
| `VISUAL_REVIEW_MODEL` | 无 | 支持图片输入的模型名 |
| `VISUAL_REVIEW_API_KEY` | 无 | 可选密钥，只从安全环境读取 |
| `VISUAL_REVIEW_TIMEOUT_SECONDS` | `120` | HTTP 请求超时秒数 |
| `VISUAL_REVIEW_ALLOW_REMOTE` | 未设置 | 非回环端点必须明确设为 `true` |

本地边车冒烟命令和返回 JSON 规范见 [`visual-review-integration.md`](visual-review-integration.md)。

## 中间状态排查

| 状态 | 处理方式 |
|---|---|
| `needs-source-review` | 打开 `source-review.md` 和原图，修正 `problem.md`，由实际看图者执行 `approve-source`；不要手改 JSON 状态 |
| `needs-analysis-and-answer` | 补知识点、可观察错误类型、分层答案与解释图 |
| `needs-answer-review` | 教师核对学生版、教师版和图像；批准当前版本或填写返修意见 |
| `needs-visualization-build` | 从当前 `physics-model.json` 构建预审仿真；不要直接手改 HTML |
| `needs-visualization-review` | 教师在页面核对动态交互仿真；批准当前产物，或对话要求修复模型；静态解释图不进入此状态 |
| `ready-to-finish` | 执行 `finish`，不要手工改成 delivered |
| `delivered` | 以 `delivery.json` 指向的输出目录和 manifest 为准 |

## 依赖与降级

- OCR：优先 Apple Vision 本地识别；失败时保留可复核条目，不丢弃原图。远程 OCR 必须先取得授权。
- 视觉复核：边车失败、返回不确定项或无可用边车时生成教师复核单；绝不以 OCR 置信度代替复核。
- PDF：PDF 工具链不可用时，继续交付 Markdown，并在 manifest 的 `pdf` 字段记录跳过原因。
- JSON Schema：需要 Python `jsonschema`。缺失时模型验证失败，不把未校验模型标为成功。
- 浏览器运行时：需要 Node.js、Playwright 和可启动的 Chromium。依赖不存在时 `runtime_check.status=skipped`；浏览器成功启动但页面或控件报错时为 `failed`，阻止交付。
- 不支持的 `model_type`：仿真构建明确失败，不套用错误模板。

## 仿真单独构建

```bash
python3 .claude/skills/build-physics-simulator/scripts/build_simulator.py \
  student-error-library/entries/<entry-id>/physics-model.json \
  --entry-dir student-error-library/entries/<entry-id> \
  --output-dir output/<题目名称>/simulation \
  --name physics-simulator --zip --runtime-check auto
```

需要把浏览器检查设为硬门槛时使用 `--runtime-check required`；在明确无法启动浏览器且只做静态预检时使用 `--runtime-check skip`，报告仍会记录跳过原因。

如果 Node 或 Playwright 不在默认搜索路径，可在调用前设置项目专用环境变量 `PHYSICS_SIMULATOR_NODE` 和 `PHYSICS_SIMULATOR_NODE_MODULES`。构建器只把后者转换为子进程所需的 Node 模块搜索路径，不把平台专用绝对路径写进 Skill。

## 交付前检查

1. 打开 PDF，确认分页、公式和图片可读；
2. 确认 `answer-review.json` 的摘要仍对应当前答案和模型；
3. 若存在 `physics-model.json`，在工作台查看预审动态仿真，确认 `visualization-review.json` 对应当前产物摘要；否则确认 manifest 把可视化记为 `not-generated` / `not-required`，页面入口仍保留；
4. 对动态仿真查看条目 `visualization/simulation-build.json` 的模型、静态校验和运行时状态；
5. 确认 `delivery-manifest.json` 中答案、PDF、仿真和 `runtime_check` 状态；
6. 解压 `student-package.zip`，确认文件名为 ASCII 且 HTML/PDF/Markdown 可打开；
7. 优先向学生发送页面标记的 `student-package.zip` 或 PDF；内部 JSON/截图不会出现在下载区。
8. 若发布学生端，检查公开预览不含姓名、学校、原题上传、教师版解析或本地路径，再执行公开确认；推送前只查看 `student-site/` 的 Git 变更。

## 常见故障

- HTML 双击打不开：先查看静态校验错误，再检查是否含远程 URL、模块脚本或丢失资源。
- HTML 能开但没有动画：查看 `runtime_check` 的控制交互和控制台错误。
- 答案与仿真事件不同：不要手改 HTML；修正 `physics-model.json` 的对应所有者字段，重新校验和构建。
- 修改解析后检索仍是旧内容：运行 `kb.py rebuild`。正常的 finalize、答案渲染和导出会自动重建。
