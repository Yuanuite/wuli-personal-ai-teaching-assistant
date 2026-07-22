# 悟理 AI 修改入口地图

这份地图面向未来接手项目的 AI：先用最少上下文找准入口，再决定是否需要读更深文档。它的目标是减少 token 浪费和误改范围。

## 先判断任务类型

| 用户请求 | 先读 | 常改文件 | 必要验证 |
|---|---|---|---|
| 处理新上传错题、导出 PDF/MD、入库、复习 | `.claude/skills/manage-student-error-library/SKILL.md` | `student-error-library/`、`output/`、相关脚本 | `process_uploads.py` 对应命令、`kb.py rebuild` |
| 生成或修复交互物理仿真 | `.claude/skills/build-physics-simulator/SKILL.md`、条目 `physics-model.json` | 仿真 Skill 的 schema/renderer/test、条目 `visualization/` | 模型校验、HTML/ZIP 检查、浏览器截图 |
| 教师端页面或本地 API | `docs/teacher-console-api.md`、`docs/architecture.md#教师工作台` | `teacher-console/server.py`、`teacher-console/static/`、`teacher-console/tests/` | `python3 -B -m unittest discover -s teacher-console/tests -p 'test_*.py' -v` |
| Agent provider、模型选择、API Key、后台任务 | `docs/agent-gateway.md` | `teacher-console/agent_gateway.py`、provider adapter、模型注册表测试 | Agent Gateway 单测、静态契约测试 |
| 学生端公开站、GitHub 只读展示、公开 PDF | `docs/architecture.md#学生端公开边界`、`docs/operator-runbook.md#发布只读学生端` | `student-site/`、`public_site.py`、公开发布测试 | public site 测试、隐私扫描 |
| 视觉复核、OCR 后图像语义确认 | `docs/visual-review-integration.md` | 视觉边车 adapter、source-review 流程 | source review 测试，不能让无视觉模型自批 |
| 高中物理解题策略、二级结论 | `docs/high-school-physics-techniques.md` 和 JSON 条件库 | 技巧库、检索脚本、答案模板 | 技巧适用条件测试 |
| 架构归位、复杂度删减、影响分析 | `docs/architecture-governance.md`，再用 `graphify query` | 取决于查询结果 | `git diff --check` + 相关模块测试 |
| 会话收尾、文档同步、规范审计 | `.claude/skills/neat-freak/SKILL.md` | README、docs、CLAUDE/AGENTS、变更日志 | 文档链接/规则/测试状态核验 |

## 默认不要先读的大文件

除非任务明确需要，AI 不应先读这些内容：

- `student-site/questions/`：公开题库产物，适合学生检索，不适合架构定位；
- `output/`：交付产物，只在核对具体条目时读取；
- `student-error-library/entries/`：知识库真源，只在处理具体题目时读取；
- `teacher-console/static/vendor/`、`student-site/vendor/`、`shared/vendor/`：离线依赖；
- `graphify-out/GRAPH_REPORT.md`：只在宽架构复盘时读，普通问题先用 `graphify query`；
- PDF、图片、ZIP：除非用户问具体视觉/交付问题。

## graphify 查询优先级

当 `graphify-out/graph.json` 存在时，先问小问题，不要把整份报告塞进上下文：

```bash
graphify query "这个改动会影响哪些模块、文档和测试？"
graphify path "teacher-console/server.py" "teacher-console/agent_gateway.py"
graphify explain "Agent Gateway 信任边界"
graphify affected "model-registry"
```

如果查询结果混入 vendor/minified 节点，先检查 `.graphifyignore` 是否被新图谱采用，再考虑重建图谱。

## 关键真源

| 真源 | 含义 |
|---|---|
| `CLAUDE.md` / `AGENTS.md` | 项目规则入口；`AGENTS.md` 应保持指向 `CLAUDE.md` |
| `docs/architecture.md` | 生命周期、组件边界、信任边界 |
| `docs/architecture-governance.md` | 功能归位、复杂度删减、变更影响的治理协议 |
| `docs/agent-gateway.md` | Agent provider、模型路由、候选隔离、隐私门禁 |
| `docs/teacher-console-api.md` | 本地教师端 HTTP 行为契约 |
| `output/<题目>/delivery-manifest.json` | 单题最终交付完成真源 |
| `student-error-library/entries/<id>/record.json` | 单题知识库记录真源 |
| `physics-model.json` | 答案与仿真共享的物理模型真源 |

## 修改前最小检查

1. 这次请求属于上表哪一类？
2. 是否触发某个 Skill？如果触发，先完整读对应 `SKILL.md`。
3. 是否涉及架构归位或复杂度？如果是，先读治理协议并用 graphify 查证据。
4. 是否可能改变学生可见内容或隐私边界？如果是，读学生端公开边界。
5. 是否改变 provider/API/model？如果是，只能通过 Agent Gateway 入口修改。

## 修改后最小检查

- 改 UI：跑静态契约测试；
- 改 Gateway：跑 Agent Gateway 与模型注册表测试；
- 改发布：跑 public site 相关测试；
- 改知识库脚本：跑对应生命周期命令并 `kb.py rebuild`；
- 改文档或规则：至少 `git diff --check`，必要时用 `neat-freak` 收尾；
- 改代码后按根规则更新 graphify：`graphify update .`。
