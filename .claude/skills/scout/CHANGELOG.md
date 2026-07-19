# Changelog

## 2.0.0

- 使用 `{baseDir}`，适配 AgentSkills / OpenClaw 路径解析。
- 将 frontmatter `description` 改为单行并加入 Node 运行时门控。
- 新增只读项目探针，默认不执行仓库代码。
- 修正 λ 的时间单位，统一输出为 `/hour`。
- 引入“阻断 + 降风险 + 执行价值”双轨排序模型。
- 新增严格输入校验、结构化警告、JSON/Markdown 输出和稳定排序。
- 新增异常输入、非 Git、空项目、路径错误和 Unicode 测试。
- 新增安全规范、工具编排、评测方案、JSON Schema 和示例。
- 新增可选结果日志与历史校准，不默认写入项目。
