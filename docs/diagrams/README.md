# 图表维护

本目录保存可审查的 JSON 真源与可直接打开的单文件 HTML。HTML 是生成物，不应手工修改；布局、文案和路径都在 JSON 真源中维护。

两个 HTML 生成物会纳入版本控制，并由 `.github/workflows/deploy.yml` 明确复制到
GitHub Pages 的 `diagrams/` 路径。Pages 发布使用临时 `pages-dist/`，不会把架构图写入
`student-site/` 源目录，也不会把 `docs/` 中的其他文件公开。

> 该项目早期使用 `archify` 工具渲染图表。archify 已移除（`.agents/skills/archify/` 已清理），
> 图表改为手工维护或直接用 HTML 编辑。更新图表时直接编辑对应的 `.html` 文件即可。
