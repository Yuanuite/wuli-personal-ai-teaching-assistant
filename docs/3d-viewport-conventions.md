# 三维仿真视口约定（Agent 参考）

`piecewise-field-particle-3d` 模型类型在生成交互式三维 HTML 时，必须遵守以下视口约定。违反约定会导致坐标轴指向混乱、视角预设失效。

## 坐标系与 handedness

| 轴 | 颜色 | 方向 |
|----|------|------|
| x | 红 | 水平向右 |
| y | 绿 | 垂直于屏幕，**+y 向屏幕内** |
| z | 蓝 | 垂直向上 |

**Handedness：左手系。** 在 `project()` 函数中通过 `z = -q[2]` 实现：

```javascript
function project(p, w, h) {
    const q = sub(p, center),
          cy = Math.cos(yaw), sy = Math.sin(yaw),
          cp = Math.cos(pitch), sp = Math.sin(pitch);
    const x = cy * q[0] - sy * q[1],
          y = sy * q[0] + cy * q[1],
          z = -q[2],                    // ← 翻转 z = 左手系
          yy = cp * y - sp * z,
          zz = sp * y + cp * z;
    const scale = Math.min(w, h) * 0.16 * zoom / (camera.scale || 1);
    return { x: w / 2 + x * scale, y: h / 2 - zz * scale, depth: yy };
}
```

关键行 `z = -q[2]` 不可遗漏；遗漏将导致 z 轴方向与约定相反。

## 预设视角

| 名称 | yaw_deg | pitch_deg | 视线方向 | 屏幕显示 |
|------|---------|-----------|---------|----------|
| iso（立体） | -38 | 28 | 斜前方 | 默认 3D 视角 |
| front（正视） | **0** | **180** | 从 +y 看向原点 | x→右, z→上 |
| top（俯视） | 0 | 88 | 从 +z 向下 | x→右, y→上 |

定义位置为渲染代码中的 `const views` 对象：

```javascript
const views = {
    iso:   [-38, 28],
    front: [0, 180],
    top:   [0, 88],
};
```

`front:[0,180]` 的投影结果（在左手系下）：
- `screen_x ∝ x` → **+x 向右**
- `screen_y ∝ -z` → **+z 向上**
- `depth ∝ -y` → **+y 向屏幕内**

## 拖拽旋转

```javascript
// 第 215 行：pitch 不 clamp，允许 360° 自由旋转
canvas.onpointermove = e => {
    yaw = drag.yaw + (e.clientX - drag.x) * 0.008;
    pitch = drag.pitch - (e.clientY - drag.y) * 0.008;  // 无 clamp
    draw();
};
```

- **yaw**：水平旋转，无约束（可无限转）
- **pitch**：俯仰旋转，**不得 clamp**（去掉 `clamp(..., rad(-89), rad(89))`）
- 否则用户无法翻转到头顶或脚底视角

## 轨迹几何约定

`arc3d` 类型圆弧段渲染公式（渲染代码第 138 行）：

```
point(a) = center + radius × (basis_u · cos(a) + basis_v · sin(a))
```

| 参数 | 含义 |
|------|------|
| `center` | 圆心坐标 |
| `radius` | 半径（标量） |
| `basis_u` | 角度 0° 方向（单位向量） |
| `basis_v` | 角度 90° 方向（单位向量） |
| `start_deg` | 起始角度（度） |
| `end_deg` | 终止角度（度） |

圆弧平面由 `basis_u` 和 `basis_v` 张成。第一段轨迹（区域 I 辐向电场）示例：

```json
{
    "geometry": {
        "path_kind": "arc3d",
        "center": [0, 0, -1],
        "basis_u": [-1, 0, 0],
        "basis_v": [0, 0, 1],
        "radius": 1,
        "start_deg": 0,
        "end_deg": 90
    }
}
```

对应物理：起点 `[-1, 0, -1]`，终点 `[0, 0, 0]`，四分之一圆弧。

## 事件坐标约定

`timeline` 中的 `position` 必须与轨迹段几何一致：

| 事件 | 坐标 | 阶段 |
|------|------|------|
| `enter-radial` | 第一段轨迹起点 | 区域 I 入口 |
| `enter-electric` | `[0, 0, 0]` | 区域 II 入口 |
| `enter-magnetic` | 区域 III 入口 P 点 | 区域 III 入口 |

所有坐标值以 `length_unit = "L"` 为单位。

## 区域形状与场方向

- **区域 I**（辐向电场）：电场方向从起点指向圆心（待定系数法确认方向）
- **区域 II**（匀强电场 E₂）：电场沿 +y
- **区域 III**（匀强磁场 B）：B∥(-x + y)，斜 45°
- **收集板**：z=L 平面

## 测试清单

生成或修改 HTML 后，在浏览器中检查：

- [ ] 坐标轴：x 红向右，y 绿向屏幕内，z 蓝向上
- [ ] 正视按钮：点"正视" → x 向右、z 向上、+y 向屏幕内
- [ ] 俯视按钮：点"俯视" → 从上方看 x-y 平面
- [ ] 拖拽：水平拖动无限旋转，垂直拖动可翻转到头顶和脚底
- [ ] 第一段圆弧：起点、圆心、终点几何关系正确
- [ ] 区域线框与场方向箭头：位置与实际区域匹配
