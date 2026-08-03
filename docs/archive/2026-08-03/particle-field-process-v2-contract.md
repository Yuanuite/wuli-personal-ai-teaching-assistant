# `particle-field-process.v2` 模型合同

## 1. 定位

`particle-field-process.v2` 是带电粒子在分段电场、磁场、复合场和无场区域中运动的统一过程模型。它统一表达二维/三维、单粒子/多粒子、空间分区/时间切换和多阶段状态交接，但不试图成为所有物理题型的万能模型。

它是“8 个稳定模型族 + 1 个原图兜底通道”中的第一个模型族。其余电磁感应、电路、力学约束、碰撞、转动轨道、实验装置和函数图像模型另行定义，不能通过给本合同增加大量可空字段来替代。

合同归属于 `build-physics-simulator` 专家层：

- `physics-model.json` 仍是答案、事件、仿真和静态示意图共享的物理真源；
- `visualization.model` 可以由 DeepSeek V4 Flash API 在 Agent Gateway 隔离候选区生成；
- MiMo 只提供经教师复核的原图视觉事实，不补写物理推导；
- Agent、MiMo 和 DeepSeek 都不能批准模型、替换 canonical、完成交付或发布；
- 几何编译器只能从已验证过程模型生成派生几何，不能引入新的物理声明。

## 2. 本阶段范围

第一阶段要求覆盖当前题库已有的 12 个 `physics-model.json`。现有六个类型为兼容输入：

```text
concentric-radial-multi-field
opposite-circular-magnetic
electric-to-bounded-magnetic
planar-magnetic-multi-particle
piecewise-field-particle-2d
piecewise-field-particle-3d
```

第一阶段不要求：

- 自动改写正式题库中的旧模型；
- 用一个新 HTML 模板立即替换全部旧渲染器；
- 求解任意非均匀场的微分方程；
- 根据一张图反推缺失的物理量；
- 用流程图或自由绘制曲线作为不支持情况的 fallback；
- 自动批准迁移结果。

## 3. 核心状态机

每个粒子状态使用同一组可传递变量：

```text
ParticleState = {
  particle_id,
  position,
  velocity,
  time,
  kinetic_energy,
  phase,
  active_stage_id
}
```

内部物理计算中的 `position`、`velocity`、`E`、`B` 和坐标基向量统一使用三分量。`space.dimension=2` 表示运动受限于一个显式二维物理平面，而不是把向量缩成两分量；这样洛伦兹力、任意截面法向和 2D/3D 迁移始终共用同一右手系。`kinetic_energy` 可以由质量和速度确定性复算；如果模型同时提供两者，数值必须一致。`phase` 只存放周期场、转动圈数等离散或连续相位，不得替代绝对时间。

一个阶段的执行语义为：

```text
输入 ParticleState
→ 读取该阶段的区域、场计划和传播规律
→ 传播到最早终止事件
→ 复算事件位置、速度、时间和能量
→ 形成不可变 EventState
→ 按 transition.carry 交给下一阶段
```

不得用“下一段曲线的起点看起来接得上”代替状态交接。

## 4. 顶层结构

```json
{
  "schema_version": "2.0",
  "model_type": "particle-field-process.v2",
  "entry_id": "stable-entry-id",
  "title": "题目标题",
  "space": {},
  "normalization": {},
  "particles": [],
  "regions": [],
  "field_programs": [],
  "stages": [],
  "events": [],
  "transitions": [],
  "cases": [],
  "stop_policy": {},
  "views": [],
  "technique_ids": [],
  "student_solution": {},
  "teacher_audit": {},
  "simulation": {}
}
```

原有生命周期拥有的 `source`、`student_solution`、`teacher_audit` 等字段继续保留。模型迁移不能静默改写这些字段。

## 5. 坐标系和归一化

### 5.1 `space`

```json
{
  "dimension": 3,
  "frame": {
    "id": "world",
    "handedness": "right",
    "origin": [0, 0, 0],
    "basis": {
      "x": [1, 0, 0],
      "y": [0, 1, 0],
      "z": [0, 0, 1]
    },
    "axis_labels": ["x", "y", "z"]
  },
  "motion_plane": null
}
```

要求：

- `dimension` 只允许 `2` 或 `3`；
- 所有内部物理向量始终为有限三分量；
- 基向量必须为有限数、单位化并相互正交；
- 三维内部物理计算统一使用右手系；
- 屏幕坐标、Canvas 的 y 轴方向和旧三维视口约定只能由 `views` 负责转换，不能污染物理坐标；
- 二维模型必须提供 `motion_plane.origin/u/v/normal`；`u`、`v`、`normal` 构成右手正交基；
- 二维粒子的位置必须位于 `motion_plane`，速度不得含法向分量；磁场可以沿法向，电场若含法向分量则必须由阶段合同明确其是否使粒子离开二维模型，否则拒绝候选。

### 5.2 `normalization`

```json
{
  "length_unit": {"symbol": "L", "si_scale": null},
  "time_unit": {"symbol": "T", "si_scale": null},
  "mass_unit": {"symbol": "m", "si_scale": null},
  "charge_unit": {"symbol": "q", "si_scale": null}
}
```

允许采用题目归一化单位。只要声明了 `si_scale`，所有维度检查都必须使用换算后的值；没有给出 SI 数值时，不得伪造单位精度。

## 6. 粒子、区域和场计划

### 6.1 `particles`

每个粒子至少包含：

```json
{
  "id": "P1",
  "label": "粒子1",
  "charge": {"value": 1, "unit": "q"},
  "mass": {"value": 1, "unit": "m"},
  "initial_state": {
    "position": [0, 0, 0],
    "velocity": [1, 0, 0],
    "time": 0,
    "phase": 0
  },
  "style": {"color": "#2563eb"}
}
```

粒子 ID 必须唯一。多粒子之间默认互不作用；若题目要求碰撞或相互作用，必须由显式事件和支持的传播规律表达，不能暗含在渲染器中。

### 6.2 `regions`

首版统一支持当前题库已经出现的几何：

```text
rect, circle, half-plane, polygon,
plane, box, cylinder, wireframe
```

区域只描述空间边界和可见样式，不直接携带随时间突变的场。场的时间行为放在 `field_programs`，由阶段引用。

### 6.3 `field_programs`

```json
{
  "id": "field-1",
  "region_id": "R1",
  "kind": "uniform",
  "schedule": [
    {
      "interval": {"start": 0, "end": 0.5, "closure": "left-closed"},
      "E": [0, 1, 0],
      "B": [0, 0, 0]
    },
    {
      "interval": {"start": 0.5, "end": 1.0, "closure": "left-closed"},
      "E": [0, 0, 0],
      "B": [0, 0, 1]
    }
  ],
  "period": null
}
```

`E` 和 `B` 始终使用物理坐标中的向量。周期场必须显式声明周期、相位原点和端点闭合规则，避免切换时刻同时属于两个阶段或哪个阶段都不属于。

首版场类型：

- `none`：无场；
- `uniform`：分段常量电场、磁场或二者共存；
- `radial-qualitative`：只允许题目未给场强函数时的定性径向电场；
- `sampled`：由已审核的数值采样描述，不声称解析解。

非均匀场若没有足够定律和验证器，必须返回 `unsupported`。

## 7. 阶段、传播规律和事件

### 7.1 `stages`

```json
{
  "id": "S1",
  "particle_ids": ["P1"],
  "region_id": "R1",
  "field_program_id": "field-1",
  "propagator": {
    "kind": "magnetic-orbit",
    "parameters": {}
  },
  "entry_event_id": "E0",
  "exit_event_ids": ["E1"],
  "priority": 10
}
```

首版传播规律：

| `kind` | 物理语义 | 派生几何 |
|---|---|---|
| `uniform-motion` | 无合力匀速运动 | line |
| `uniform-electric` | `a=qE/m` | quadratic/parabola |
| `magnetic-orbit` | 匀强磁场中分解平行与垂直速度 | arc/arc3d/helix |
| `uniform-crossed-field` | 同时存在常量 E、B | analytic-supported 或 sampled |
| `reflection` | 经审核边界反射规律 | line/arc continuation |
| `collision` | 经审核碰撞规律 | piecewise continuation |
| `sampled-motion` | 数值点及误差界 | points/polyline |

`uniform-crossed-field` 只有在实现存在明确解析器和验证夹具时才允许使用；否则返回 `unsupported`，不能把它降级为手绘 polyline。

### 7.2 `events`

```json
{
  "id": "E1",
  "kind": "boundary-crossing",
  "particle_id": "P1",
  "stage_id": "S1",
  "state": {
    "position": [1, 0, 0],
    "velocity": [0, 1, 0],
    "time": 1,
    "kinetic_energy": 0.5,
    "phase": 0.25
  },
  "surface_id": "L1",
  "terminal": false,
  "pause": true
}
```

首版事件类型：

```text
initial, boundary-crossing, field-switch, time-reached,
collision, reflection, impact, capture, extremum, stop
```

一个阶段若可能触发多个事件，必须计算最早事件；仅画出最终命中点不算满足事件合同。

### 7.3 `transitions`

```json
{
  "id": "T-S1-S2",
  "from_stage_id": "S1",
  "event_id": "E1",
  "to_stage_id": "S2",
  "carry": ["position", "velocity", "time", "kinetic_energy", "phase"],
  "transform": null
}
```

默认要求位置、时间和速度连续。只有 `collision`、`reflection` 或题目明确的瞬时作用事件可以改变速度；改变规则必须记录在事件中，并由独立校验器验证。

## 8. 案例与停止策略

`cases` 只选择参数、有效阶段和结论，不复制整套事件账本：

```json
{
  "id": "case-a",
  "label": "情况一",
  "parameter_bindings": {"B0": 1, "E0": 2},
  "active_stage_ids": ["S1", "S2"],
  "stop_event_id": "E-final",
  "conclusion": "到达下极板"
}
```

`stop_policy` 明确：

- 顶层默认停止事件；
- 每个 case 的覆盖事件；
- “首次到达”“截止时刻之前”“第 N 次进入”等计数语义；
- 同时事件的优先级和容差。

首次事件和截止事件必须由事件顺序检查证明，不能由描述文字声明。

## 9. 几何编译与二维截面

### 9.1 派生关系

```text
过程模型（真源）
→ 传播器求得分段状态
→ 几何 IR（派生，可复算）
→ view 投影/截面
→ SVG、Canvas 或交互 HTML
```

几何 IR 不写回 canonical `physics-model.json`。它至少保存：

- 输入模型 fingerprint；
- case ID、粒子 ID、stage ID；
- primitive 类型和解析参数；
- 起止状态；
- 误差界；
- 生成器版本。

解析圆弧必须保存圆心、半径、平面基向量和角区间。圆心使用洛伦兹力方向确定，二维屏幕方向不得参与计算。

### 9.2 `views`

```json
{
  "id": "front-section",
  "kind": "orthographic-section",
  "origin": [0, 0, 0],
  "normal": [0, 0, 1],
  "u": [1, 0, 0],
  "v": [0, 1, 0],
  "include_stage_ids": ["S1", "S2"],
  "annotations": ["velocity", "force", "orbit-center"]
}
```

三维题生成二维 SVG 时必须显式选择截面或投影。不得把三维螺旋线直接压扁后称为圆周运动截面。圆心、半径和切线先在物理平面中计算，再投影到视图。

## 10. 验证义务

### 10.1 硬门控

以下任一失败都必须拒绝候选：

1. **结构与引用**：ID 唯一；引用存在；维度一致；数值有限。
2. **坐标系**：基向量正交；二维法向明确；投影基有效。
3. **阶段局部规律**：
   - 无场阶段速度恒定；
   - 纯电场满足 `a=qE/m` 和功—能关系；
   - 纯磁场速率不变，半径满足 `r=mv_perp/(|q|B)`，磁力指向圆心；
   - 螺旋运动的轴向速度恒定；
   - 电磁共存时只有电场做功。
4. **状态交接**：除允许的瞬时事件外，位置、速度和时间连续。
5. **几何一致**：端点、切线、圆心、半径、旋向、截面投影一致。
6. **事件顺序**：每阶段选择真实最早事件；停止事件可达；case 不越过自己的截止事件。
7. **兼容安全**：旧模型仍由旧渲染器构建；新模型失败时不得静默回退为旧模板、流程图或自由曲线。

### 10.2 软评分

以下只形成质量报告，不单独阻断物理正确的候选：

- 标签疏密和避碰；
- 配色、线宽、留白；
- 教学辅助标注数量；
- 默认视角美观性；
- 场纹理强度。

关键物理元素完全不可读时，应由人工审核把结果退回，但不要把近似字体包围盒提升为物理真值门控。

## 11. 旧模型兼容策略

采用并行兼容，不做原地大迁移：

1. 旧六类 `model_type` 的 builder、validator 和 HTML 模板保持可用；
2. 新增 `particle-field-process.v2` schema、validator、compiler 和 renderer；
3. 为六类旧模型提供只读 `legacy → normalized-v2` 适配器，用于语义对照和迁移预检；
4. 适配结果只能进入临时候选区，不写回正式条目；
5. 每题比较事件、案例、停止条件、关键圆心/半径/端点和截图；
6. 教师批准迁移前，交付继续使用已经批准的旧模型和旧仿真字节；
7. 12/12 回放通过后，才允许新题默认生成 v2；旧模型的删除是独立后续决策。

为避免“大爆炸迁移”，新构建器分派规则为：

```text
old model_type → unchanged old renderer
particle-field-process.v2 → new deterministic compiler/renderer
unknown model_type → unsupported
```

## 12. Flash 的授权边界

DeepSeek V4 Flash API 可以：

- 根据冻结合同提出 JSON Schema、类型、纯函数、适配器和测试候选；
- 在允许路径内实现一个原子任务；
- 输出结构化补丁和测试说明。

DeepSeek V4 Flash API 不可以：

- 修改正式题库条目；
-修改教师批准记录；
- 放宽 hard gate 以迁就候选；
- 引入新的 `model_type`；
- 把不支持的场或轨迹伪装为 sampled/polyline；
- 修改 `server.py` 中的 provider 参数；
- 批准、finish 或发布。

Codex 审核每个候选的物理语义、接口影响和测试证据；教师保留最终迁移与图像质量批准权。

## 13. 首阶段完成条件

只有同时满足下列条件，`particle-field-process.v2` 才可用于新题默认生成：

- 新 schema 和确定性 validator 通过故障注入；
- 二维和三维几何编译器通过圆心、半径、切线、截面和状态连续性夹具；
- 六类旧模型适配器对当前 12 个模型全部生成可比较的 normalized-v2；
- 12/12 的案例、事件、停止条件和核心物理不变量等价；
- 旧模型构建路径和已批准交付字节不受影响；
- 两道代表题完成教师看图审核：至少一题二维多阶段、一题三维周期/螺旋；
- Agent Gateway 仍保持隔离候选和人工批准边界；
- 未支持情况明确返回 `unsupported`。

任何一项不足都只能标记 `provisional`，不能把 v2 设为默认。
