# 悟理文档入口

本目录面向维护者、接入者和未来接手的 Agent。根目录 `CLAUDE.md` 只保留项目规则和边界；具体机制、接口和运维说明以这里为准。

## 核心文档

| 文档 | 用途 |
|---|---|
| [architecture.md](architecture.md) | 生命周期、组件职责、信任边界、`physics-model.json` 真源关系 |
| [operator-runbook.md](operator-runbook.md) | 本地启动、人工命令、复核门禁、发布学生端、故障排查 |
| [teacher-console-api.md](teacher-console-api.md) | 教师工作台本地 HTTP 路由、写操作请求头、状态码和调用顺序 |
| [agent-gateway.md](agent-gateway.md) | Agent Gateway provider、隔离候选、成本档位、隐私门禁和作业恢复 |
| [visual-review-integration.md](visual-review-integration.md) | OCR 后视觉复核边车、OpenAI-compatible 多模态接入和隐私门禁 |
| [high-school-physics-techniques.md](high-school-physics-techniques.md) | 高中物理解题技巧速查；自动采用二级结论时仍以 JSON 条件库为准 |

## 项目交付与规划

| 文档 | 用途 |
|---|---|
| [competition-submission.md](competition-submission.md) | 竞赛申报精简稿，数字应从代码和状态命令核验 |
| [competition-project-description.md](competition-project-description.md) | 完整项目说明、演示脚本、价值定位和落地计划 |
| [github-issues.md](github-issues.md) | 可复制到 GitHub Issues 的阶段性改进清单 |
| [create-issues.sh](create-issues.sh) | 从本地批量创建上述 Issues 的辅助脚本 |
| [CHANGES.md](CHANGES.md) | 面向人类维护者的阶段性能力变化记录 |

## 同步规则

- 新增教师端路由时，同步更新 [teacher-console-api.md](teacher-console-api.md) 和 [architecture.md](architecture.md)。
- 新增 Agent provider、模型档位、环境变量或隐私门禁时，同步更新 [agent-gateway.md](agent-gateway.md)、[operator-runbook.md](operator-runbook.md) 和根 [../README.md](../README.md)。
- 新增完整能力或竞赛口径变化时，同步更新 [competition-submission.md](competition-submission.md)、[competition-project-description.md](competition-project-description.md) 和 [CHANGES.md](CHANGES.md)。
- 不把单次开发流水账写进根 `CLAUDE.md`；规则写根文件，机制写本文档目录，历史写 [CHANGES.md](CHANGES.md)。
