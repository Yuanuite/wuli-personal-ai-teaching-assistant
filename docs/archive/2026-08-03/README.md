# 归档文档 — 2026-08-03

以下文档对应的代码功能已实现并验证，或已被更新版本取代。归档不删除，仅移出活跃文档目录。

## 已归档文件

| 文件 | 原用途 | 完成确认 |
|---|---|---|
| `analysis-provider-timeout-repair-work-tree.md` | Analysis provider 超时修复执行计划 | `teacher-console/agent_gateway.py`、`teacher-console/failure_intelligence.py` |
| `visual-diagram-quality-improvement-work-tree.md` | 图示质量改进执行计划 | `teacher-console/physics_diagram.py`、`teacher-console/diagram_visual_review.py` |
| `visual-diagram-quality-improvement-work-tree.v1.json` | 图示质量改进 v1 数据（已被 .md 取代） | 同上 |
| `visual-diagram-quality-improvement-work-tree.v2.json` | 图示质量改进 v2 数据（已被 .md 取代） | 同上 |
| `particle-field-process-v2-work-tree.v1.json` | 粒子场过程 v2 执行计划 | 基线已锁定 |
| `particle-field-process-v2-contract.md` | 粒子场过程 v2 契约 | 契约已完成 |
| `w3-w3r-route-deadline-repair-work-tree.md` | W3/W3R 路由与截止时间修复执行计划 | `teacher-console/deadline_budget.py`、`teacher-console/route_snapshot.py` |
| `analysis-run-observability-w3-pipeline-work-tree.md` | Analysis 运行可观测性与 W3 管线执行计划 | `teacher-console/agent_jobs.py`、`teacher-console/agent_outcome.py` |
| `mimo-deepseek-collaboration-work-tree.v1.json` | MiMo-DeepSeek 协作 v1 数据（已被 .md 取代） | `docs/mimo-deepseek-consistency-closure-work-tree.md` |
| `w3r-flash-execution-roadmap.v1.json` | W3R Flash 执行路线图 | 代码完成，E2E 测试通过 |
| `技术执行计划书.md` | 教学正确性证据链专项执行计划 | `teacher-console/evidence_agent.py` 等已实现 |
| `解题loop.md` | 复杂物理解题循环架构设计 | `teacher-console/cognitive_loop.py` |
| `构建-Wuli-Evidence-Agent-证据层-原子任务.md` | Evidence Agent 证据层升级原子任务 | `teacher-console/evidence_agent.py`、`teacher-console/evidence_contract.py` |
| `构建-W3R-非求解教学渲染与忠实性门禁-原子执行.md` | W3R 教学渲染与忠实性门禁原子任务 | `teacher-console/w3r_contract.py`、`teacher-console/w3_rendering.py` |
| `create-issues.sh` | 批量创建 GitHub Issue 的一次性脚本 | 已被 `docs/github-issues.md` 取代 |

## 同时迁移的配置文件

| 文件 | 原位置 | 新位置 | 原因 |
|---|---|---|---|
| `retrieval-eval.example.jsonl` | `docs/` | `student-error-library/config/` | 配置文件应靠近使用方 |
| `evidence-budget-eval.example.jsonl` | `docs/` | `student-error-library/config/` | 配置文件应靠近使用方 |
