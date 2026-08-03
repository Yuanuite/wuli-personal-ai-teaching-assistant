# 悟理文档入口

本目录面向维护者、接入者和未来接手的 Agent。根目录 `CLAUDE.md` 只保留项目规则和边界；具体机制、接口和运维说明以这里为准。

## 核心文档

| 文档 | 用途 |
|---|---|
| [ai-editing-map.md](ai-editing-map.md) | AI 修改入口地图：按任务类型选择最小上下文，减少误读和 token 浪费 |
| [architecture.md](architecture.md) | 生命周期、组件职责、信任边界、`physics-model.json` 真源关系 |
| [architecture-governance.md](architecture-governance.md) | 基于 graphify 的项目治理协议：功能归位、复杂度删减、变更影响分析 |
| [diagrams/README.md](diagrams/README.md) | 架构图与 Pipeline 的 JSON 真源、复核导出物和 Story 对焦验收契约 |
| [operator-runbook.md](operator-runbook.md) | 本地启动、人工命令、复核门禁、发布学生端、故障排查 |
| [teacher-console-api.md](teacher-console-api.md) | 教师工作台本地 HTTP 路由、写操作请求头、状态码和调用顺序 |
| [agent-gateway.md](agent-gateway.md) | Agent Gateway provider、运行时/模型/工具兼容矩阵、隔离候选、接入协议和故障排查 |
| [agent-scheduler.md](agent-scheduler.md) | 后台 Agent 作业调度：优先级队列、任务并发、配置文件与后续 Evolve 接口 |
| [failure-intelligence.md](failure-intelligence.md) | Agent 失败分类、预算保护、零 Token 修复与一次性纠正规则 |
| [evaluator.md](evaluator.md) | 单题产物质量评价报告：解析、可视化、交付和 AI 审计闭环的第一块轻基建 |
| [candidate-archive.md](candidate-archive.md) | 候选、教师反馈、Agent 结果和 Evaluator 摘要的追加式事件档案 |
| [knowledge-store.md](knowledge-store.md) | 本地 SQLite/FTS 派生检索层：把题库、评价和候选历史聚合为 RAG evidence pack |
| [objective-difficulty-rubric.md](objective-difficulty-rubric.md) | 客观难度六维量表、证据链、配置真源、教师校准、迁移与修改检查表 |
| [evolve-roadmap.md](evolve-roadmap.md) | RAG 效果观测、检索后端增强和慢循环策略更新的样本门槛与顺序 |
| [w3-reasoning-pipeline.md](w3-reasoning-pipeline.md) | W3 复杂题拆解、定向召回、求解、验证、仲裁、自适应生产路由及私有审计报告契约 |
| [rag-completion-work-tree.md](rag-completion-work-tree.md) | W3 从影子评测到独立 holdout、生产灰度、默认启用和回滚验收的已完成执行树（已完成，保留为治理参考） |

| [reports/w4-final-production-acceptance.md](reports/w4-final-production-acceptance.md) | W3 自适应复杂题路由的最终生产验收、失败恢复与 W2 回滚演练 |
| [reports/w5-ipho-2021-theory-closed-book.md](reports/w5-ipho-2021-theory-closed-book.md) | 2021 IPhO 三道理论题的解答隔离、完整作答、逐小问阅卷与时间/用量观测 |
| [reports/default-obligation-shadow-report.md](reports/default-obligation-shadow-report.md) | 默认义务 shadow-only 触发统计：评估“不限定唯一则枚举全部物理解支”规则的帮助与误伤风险 |
| [reports/correctness-evidence-metrics-v1.md](reports/correctness-evidence-metrics-v1.md) | 断言证书覆盖、故障检出、回跳、重复率和未决率基准 |
| [reports/correctness-cognitive-loop-ablation-v1.md](reports/correctness-cognitive-loop-ablation-v1.md) | 同题同模型同证据固定种子的认知环开/关消融 |
| [reports/correctness-replay-diagnostic-v1.md](reports/correctness-replay-diagnostic-v1.md) | 旧题只读投影回放、摘要漂移检查与生产证据边界 |


| [litellm-gateway.md](litellm-gateway.md) | 用 LiteLLM Proxy 作为悟理上游模型网关的配置与职责边界 |
| [visual-review-integration.md](visual-review-integration.md) | OCR 后视觉复核边车、OpenAI-compatible 多模态接入和隐私门禁 |
| [high-school-physics-techniques.md](high-school-physics-techniques.md) | 高中物理解题技巧速查；自动采用二级结论时仍以 JSON 条件库为准 |

## 项目交付与规划

| 文档 | 用途 |
|---|---|
| [competition-submission.md](competition-submission.md) | 竞赛申报精简稿，数字应从代码和状态命令核验 |
| [competition-project-description.md](competition-project-description.md) | 完整项目说明、演示脚本、价值定位和落地计划 |
| [github-issues.md](github-issues.md) | 统一待办真源：已确认工程缺陷、反馈闭环、待验证实现和条件型远期方向 |

| [CHANGES.md](CHANGES.md) | 面向人类维护者的阶段性能力变化记录 |

## 归档文档

已完成的执行计划、被取代的文件和 dated 报告已归档至 [archive/2026-08-03/](archive/2026-08-03/README.md)。归档不删除，仅移出活跃目录。

## 同步规则

- 新增教师端路由时，同步更新 [teacher-console-api.md](teacher-console-api.md) 和 [architecture.md](architecture.md)。
- 新增 Agent provider、模型档位、环境变量或隐私门禁时，同步更新 [agent-gateway.md](agent-gateway.md)、[operator-runbook.md](operator-runbook.md) 和根 [../README.md](../README.md)。
- 修改 `analysis.generate` 契约时，同步更新 Gateway/架构/运维说明、单元与 E2E 两个 fake adapter，并同时验证结构化门禁、固定检索集和 4 条隔离 E2E。
- 新增完整能力或竞赛口径变化时，同步更新 [competition-submission.md](competition-submission.md)、[competition-project-description.md](competition-project-description.md) 和 [CHANGES.md](CHANGES.md)。
- 不把单次开发流水账写进根 `CLAUDE.md`；规则写根文件，机制写本文档目录，历史写 [CHANGES.md](CHANGES.md)。
