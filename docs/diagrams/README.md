# Archify 图表维护

本目录保存可审查的 JSON 真源与可直接打开的单文件 HTML。项目采用组合方式：

- `.agents/skills/archify/` 提供 Agent 的布局规范、Schema、示例和迭代流程；
- 同一目录内的零依赖 CLI 负责确定性渲染、校验和制品检查。

更新错题处理 Pipeline：

```bash
node .agents/skills/archify/bin/archify.mjs render workflow \
  docs/diagrams/pipeline.workflow.json \
  docs/diagrams/pipeline.workflow.html

node .agents/skills/archify/bin/archify.mjs validate workflow \
  docs/diagrams/pipeline.workflow.json \
  --quality standard --json

node .agents/skills/archify/bin/archify.mjs check \
  docs/diagrams/pipeline.workflow.html
```

更新系统架构图时，把 `workflow` 替换为 `architecture`，输入改为
`system.architecture.json`。HTML 是生成物，不应手工修改；布局、文案和路径都在 JSON 真源中维护。

两个 HTML 生成物会纳入版本控制，并由 `.github/workflows/deploy.yml` 明确复制到
GitHub Pages 的 `diagrams/` 路径。Pages 发布使用临时 `pages-dist/`，不会把架构图写入
`student-site/` 源目录，也不会把 `docs/` 中的其他文件公开。

## Story Follow 对焦契约

带 `meta.views` 的图表会把有序 `focus` 列表变成可播放的 Story。每一步以当前节点为优先锚点，同时保留前一步和下一步作为上下文。相机不要求把节点放到正中央，但必须把当前节点完整放进浏览器实际可见的图表区域；当图表容器只露出较小切片时，可以自适应缩小上下留白，不应通过页面级 `scrollIntoView()` 抢走读者的滚动控制。

修改共享 Story Camera 后，至少运行：

```bash
node --test \
  .agents/skills/archify/test/story-follow-camera.test.mjs \
  .agents/skills/archify/test/semantic-camera.test.mjs
```

随后重新生成 HTML，并在浏览器中检查播放的中间步骤和每章最后一步：活动节点的边界必须完整落在当前 viewport 内，手动选择 Story Beat 与自动播放应复用同一套对焦行为。
