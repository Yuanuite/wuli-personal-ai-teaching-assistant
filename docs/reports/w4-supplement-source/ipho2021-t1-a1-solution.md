# 解析（学生版）

![油水压力分布示意图](assets/explanatory.svg)

## 答案速览

- 油柱高度为 $h'=\rho_0h/\rho_{\mathrm{oil}}$；
- 右板所受水平合力为

$$
\boxed{
F_x=\frac{\rho_0gh^2w}{2}
\left(\frac{\rho_0}{\rho_{\mathrm{oil}}}-1\right)
}
$$

方向水平向右。

## 一眼识别

- 最短主线：先用板下边缘等压求油柱高度，再比较两侧三角形压强分布的面积。
- 竖直平面受到的静水合力等于压强-深度图像面积乘以宽度。

## 详细解答

### 第 1 步：求油柱高度

板下边缘内外两侧压强相等：

$$
\rho_{\mathrm{oil}}gh'=\rho_0gh,
$$

所以 $h'=\rho_0h/\rho_{\mathrm{oil}}$。

### 第 2 步：比较两侧压力

油对右板向右的力与水对右板向左的力分别为

$$
F_{\mathrm{oil}}=\frac12\rho_{\mathrm{oil}}gh'^2w,\qquad
F_{\mathrm{water}}=\frac12\rho_0gh^2w.
$$

故

$$
F_x=F_{\mathrm{oil}}-F_{\mathrm{water}}
=\frac{\rho_0gh^2w}{2}
\left(\frac{\rho_0}{\rho_{\mathrm{oil}}}-1\right)>0.
$$

## 易错点

- 油的密度较小，但油柱高度大于 $h$，不能只比较密度。
- 大气压在两侧产生的作用相互抵消。

## 30 秒自测

为什么两侧在板下边缘等压，却仍会产生非零合力？

## 教师审计

- 量纲为 $\mathrm{Pa}\cdot\mathrm{m}^2=\mathrm N$。
- 当 $\rho_{\mathrm{oil}}\to\rho_0$ 时，油柱高度趋于 $h$，合力趋于零。
- 与 IPhO 2021 官方解答 T1-A1 一致。

