# 物理图质量提升：Flash A1 审核结果

结论：`A1` 未通过，依赖它的 `A2–A7` 按控制策略全部未运行；没有把任何被拒绝的 Flash 补丁应用到正式代码。

第一次 Flash 提案在方向上认可“物理用途 `kind` 与几何类型 `geometry` 分离”，但补丁虚构了仓库中不存在的 `PathPrimitive`、`PathKind` 和 `GeometryType` 类，无法应用。第二次加入真实函数锚点后仍返回相同候选，触发 no-progress fuse。全局第二轮把范围进一步缩到 `SCENE_OUTPUT_SCHEMA` 和 `normalize_scene()`，仍未形成新候选，因此达到两轮上限后停止。

审核接受的是设计判断，不是代码工件：圆弧应由起点、弧上点、终点定义，并用尺度无关的重复点/近共线判据拒绝退化输入。下一轮若继续，不应再让 Flash 自由生成 unified diff；应冻结一个 `anchor_before + anchor_after + replacement` 合同，让本地程序先验证锚点唯一性，再由 Codex 审核替换内容。
