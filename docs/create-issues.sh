#!/bin/bash
# 确保先设置了 GH_TOKEN: export GH_TOKEN=ghp_xxx
set -e

cd "$(dirname "$0")/.."

echo "=== 当前限制（6个）==="

gh issue create --title "检索：BM25 词法搜索无法捕获物理概念关系" \
  --label "enhancement" --label "knowledge-base" \
  --body '## 问题

当前 `kb.py` 使用 BM25 + 二元分词（bigram）做知识库检索。搜索"碰撞问题"找不到标记为"动量守恒"的条目，尽管它们属于同一物理领域。

## 具体表现

- 扁平 JSON 索引没有概念层级（力学 → 动量 → 碰撞）
- 没有同义词扩展
- 没有跨概念关联
- 无法按知识领域范围搜索

## 影响

平台的核心价值主张是知识检索和薄弱点分析，这是当前架构最大的单一能力缺口。

## 建议方向

1. 构建高中物理概念本体（~200 节点），定义层级/关联/前置关系
2. 在 `kb.py` 的 `search()` 中基于本体做查询扩展
3. 可选集成小嵌入模型（multilingual-e5-small, 118M）做语义匹配

详见 docs/github-issues.md'

gh issue create --title "审核：教师审核是瓶颈，缺少风险分流与聚焦" \
  --label "enhancement" --label "teacher-console" \
  --body '## 问题

当前每道 AI 生成的答案都需要教师完整审核，即使是对 AI 从未出错的简单运动学题也是如此。

## 具体表现

- 所有答案无论难度和 AI 置信度都走相同的审核路径
- 没有风险分流（不同风险的题目仍使用相同展示密度和复核顺序）
- 审计记录是隐式的（JSON 时间戳和摘要哈希），不够结构化
- 没有审核疲劳检测机制

## 建议方向

1. 在独立验证报告中记录风险、置信度和证据来源
2. 用风险等级调整队列顺序、突出可疑步骤并简化低风险题视图，但始终保留教师显式确认
3. 结构化审计记录：时间戳、模型版本、prompt 哈希、编辑 diff、拒绝原因
4. 可选：合成错误注入（~1%）验证审核注意力

详见 docs/github-issues.md'

gh issue create --title "离线：缺少开箱即用的本地 SLM 配置与发现" \
  --label "enhancement" --label "agent-gateway" \
  --body '## 问题

当前 `agent_gateway.py` 已支持本机 JSON adapter 和回环 OpenAI-compatible 服务，但尚未自动发现 Ollama、检查本地模型是否就绪，也没有面向教师的一键配置。未预先配置本地 provider 时，断网仍无法生成或修改答案。

## 具体表现

- 没有 Ollama 等本地运行时的自动发现和模型就绪检查
- 本地服务仍需教师手工填写端点与模型名

## 建议方向

1. 复用现有 OpenAI-compatible/JSON adapter，增加 Ollama 发现、模型清单和主动探测
2. 推荐模型：Qwen2.5-3B-Instruct 或 Phi-4-mini
3. 本机 provider 配置成功后优先于通用远程 CLI
4. UI 警告“本地模型生成，仍需教师复核”

详见 docs/github-issues.md'

gh issue create --title "复习：间隔复习使用固定周期，缺乏自适应调度" \
  --label "enhancement" --label "knowledge-base" \
  --body '## 问题

当前复习调度使用固定间隔（掌握度 0-5 映射到 1/3/7/14/30/60 天），不根据题目难度或学生表现自适应调整。

## 具体表现

- 简单概念和学生反复答对的难题使用相同的复习节奏
- 反复答错的概念不会获得更高频的复习
- 不区分前提性概念和孤立知识点的遗忘曲线差异

## 建议方向

采用 FSRS v6 算法替换固定间隔（纯算法，~80 行 Python，Apache 2.0）：
- 每道题跟踪三个参数：稳定性、难度、可提取性
- 根据每次复习结果自动计算下次最优复习时间

详见 docs/github-issues.md'

gh issue create --title "工具接口：Skill 编排使用临时函数调用，缺少标准化工具接口" \
  --label "enhancement" --label "teacher-console" \
  --body '## 问题

当前 Skill 编排使用临时 Python 函数调用，接口不统一。新增能力（如 Windows OCR 引擎、电路仿真器）需要修改编排代码。

## 具体表现

- 没有标准化的工具发现机制
- 每个工具自行校验输入，没有统一的 JSON Schema 校验
- 测试单个工具需要跑完整管道

## 建议方向

采用 MCP（Model Context Protocol）stdio 传输模式：
- 将 OCR、知识库、物理仿真包装为 MCP Server
- 使用 Python FastMCP 库的 `@mcp.tool()` 装饰器
- stdio 传输 = 纯子进程管理，零网络配置

详见 docs/github-issues.md'

gh issue create --title "验证：答案验证仅检查结构，缺少语义正确性校验" \
  --label "enhancement" --label "agent-gateway" \
  --body '## 问题

当前 `kb.py` 的 `validate_entry` 只做结构性校验（标题存在、无 `[待核对]` 标记、图片引用可解析），无法检测物理推理错误或计算错误。

## 具体表现

- 不验证答案步骤中的数字是否导向声明的结论
- 不检查推理链是否使用了正确的物理公式
- 结构完全正确但物理上错误的答案会通过校验

## 建议方向

采用 Generator-Verifier 模式：
- 生成步骤完成后用独立验证器模型检查答案
- 验证：数学一致性、物理概念有效性、结构完整性
- 产出结构化报告（通过/失败/不确定 + 具体问题列表）

详见 docs/github-issues.md'

echo ""
echo "=== 架构演进方向（8个）==="

gh issue create --title "[架构演进] HITL 风险分流 + 结构化审计轨迹" \
  --label "enhancement" --label "architecture" --label "teacher-console" \
  --body '## 方案

将现有四门禁审核系统形式化为：风险分级队列 + 结构化机器可读审计记录 + 保留教师最终确认的聚焦复核。

## 解决

教师审核瓶颈（所有答案都使用相同展示密度和复核顺序）

## 要点

1. 在独立验证报告中记录风险、置信度和证据来源
2. 高风险题优先并展开完整证据，低风险题使用精简视图；所有题仍由教师显式确认
3. 结构化审计记录：时间戳、模型版本、prompt 哈希、编辑 diff、拒绝原因
4. 可选：合成错误注入（~1%）验证审核注意力

**迁移复杂度：低 | 投入产出比：高 | 优先级：P1（需先有验证报告和风险校准样本）**

详见 docs/github-issues.md'

gh issue create --title "[架构演进] 混合语义搜索 + 物理概念本体" \
  --label "enhancement" --label "architecture" --label "knowledge-base" \
  --body '## 方案

用预构建的高中物理概念本体 + 可选轻量级稠密检索增强当前 BM25 词法搜索。

## 解决

BM25 无法捕获物理概念关系（搜索"碰撞"找不到"动量守恒"）

## 要点

1. 创建 `physics-concept-ontology.json`（~200 节点），覆盖高中物理
2. `kb.py` 查询扩展：查找本体同义词和子概念
3. 支持按本体分支范围过滤
4. 可选：集成 small embedding model（multilingual-e5-small 或 bge-small-zh）

**迁移复杂度：中 | 投入产出比：高 | 优先级：P1（出现稳定概念漏召回时启动）**

详见 docs/github-issues.md'

gh issue create --title "[架构演进] Generator-Verifier 复合 AI 模式" \
  --label "enhancement" --label "architecture" --label "agent-gateway" \
  --body '## 方案

在 AI 答案生成和教师审核之间插入验证器步骤，用独立（更小/更便宜）模型检查生成质量。

## 解决

答案验证仅做结构性检查，无法检测物理推理错误

## 要点

1. 验证 Schema：answer_validity（pass/fail/uncertain）+ issues[]
2. 实现 `verify_answer` 函数，复用现有 agent gateway provider 链
3. 验证报告存储为 `verification.json`
4. 与 HITL 风险分流联动；验证结果只辅助排序和聚焦，不自动批准

**迁移复杂度：中 | 投入产出比：中高 | 优先级：P1（先评审验证 Schema 与失败回退）**

详见 docs/github-issues.md'

gh issue create --title "[架构演进] MCP 协议标准化工具接口" \
  --label "enhancement" --label "architecture" --label "teacher-console" \
  --body '## 方案

将 OCR、知识库、物理仿真、答案生成等能力包装为 MCP Server（stdio 传输），每个工具成为自描述、可独立测试、带统一校验的标准化单元。

## 解决

Skill 编排使用临时函数调用，新增能力需要修改编排代码

## 要点

1. 使用 FastMCP Python 库 `@mcp.tool()` 装饰器包装现有函数
2. stdio 传输 = 纯子进程管理，零网络配置
3. 逐步迁移各能力
4. 利用 Resources 原语提供知识库只读访问

**迁移复杂度：中 | 投入产出比：中高 | 优先级：P2（第三类外部工具接入时启动）**

详见 docs/github-issues.md'

gh issue create --title "[架构演进] FSRS v6 自适应间隔复习调度" \
  --label "enhancement" --label "architecture" --label "knowledge-base" \
  --body '## 方案

用 FSRS v6 算法替换当前的固定间隔复习调度。

## 解决

固定间隔不根据题目难度或学生表现自适应

## 要点

1. 新增 `stability` 和 `difficulty` 字段到 `record.json`
2. 用 FSRS v6 替换 `apply_review` 函数（纯 Python ~80 行）
3. 暴露保留率目标参数（默认 0.90）
4. 新增复习历史可视化：按知识点显示稳定性增长曲线

**迁移复杂度：低 | 投入产出比：中 | 优先级：P1（积累足够复习事件后评估）**

详见 docs/github-issues.md'

gh issue create --title "[架构演进] 本地 SLM 离线 AI 推理降级" \
  --label "enhancement" --label "architecture" --label "agent-gateway" \
  --body '## 方案

为现有 Agent Gateway 增加 Ollama 等本地小模型的自动发现、配置和质量提示，复用既有结构化 provider 协议。

## 解决

本地 SLM 可接入但不够开箱即用，未预配时断网无法生成内容

## 要点

1. 增加 Ollama 运行时、回环端点和模型就绪探测
2. 推荐模型：Qwen2.5-3B-Instruct、Phi-4-mini
3. 通过现有 OpenAI-compatible/JSON adapter 注册，本机 provider 优先于远程 CLI
4. UI 明确“本地模型生成，仍需教师复核”，支持稍后用其他 provider 重跑
5. 本地模型做默认验证器（验证比生成容易）

**迁移复杂度：中 | 投入产出比：中 | 优先级：P2（完成目标设备和弱网评估后启动）**

详见 docs/github-issues.md'

gh issue create --title "[架构演进] BKT 知识追踪 + 薄弱点诊断" \
  --label "enhancement" --label "architecture" --label "knowledge-base" \
  --body '## 方案

为每个物理概念添加基于简化贝叶斯知识追踪（BKT）的掌握度跟踪，实现量化薄弱点诊断。

## 解决

当前薄弱点分析无法区分"真不会"和"粗心错"

## 要点

1. 建立概念-题目映射（依赖概念本体）
2. BKT 参数：P(L₀)=0.3, P(G)=0.2, P(S)=0.1
3. 每次复习后用贝叶斯定理更新
4. 薄弱点 API + 教师工作台热力图
5. 与复习调度联动

**迁移复杂度：中 | 投入产出比：中 | 优先级：P2（概念本体和有效观察就绪后启动）**

详见 docs/github-issues.md'

gh issue create --title "[架构演进] MiniRAG 跨条目主题问答（远期储备）" \
  --label "enhancement" --label "architecture" --label "knowledge-base" --label "future" \
  --body '## 方案

当知识库增长到 100+ 条目后，引入 MiniRAG（港大 2025，SLM 优化的轻量 GraphRAG）做跨条目主题问答。

## 解决

本体增强 BM25 在 100+ 条目时仍难以处理跨条目聚合查询

## 要点

1. 前提：100+ 已审核条目 + 足够使用数据
2. 使用 MiniRAG 而非 Microsoft GraphRAG（支持 1.5B 模型）
3. 相似图社区检测 + 增量更新
4. 离线批处理 + 物理概念本体作为种子实体

**迁移复杂度：高 | 投入产出比：低（<100条目） | 优先级：P3（100+ 已审核条目后再评估）**

详见 docs/github-issues.md'

echo ""
echo "✅ 全部 14 个 issue 创建完成！"
