# 解析（学生版）

![接触点约束示意图](assets/explanatory.svg)

## 答案速览

- 对接触约束求时间导数，立即得到
  \(\mathbf v_A\cdot\hat{\mathbf z}=0\)。

## 一眼识别

- 最短主线：位置约束 → 对时间求导 → 识别接触点速度。

## 详细解答

地面静止且 \(\hat{\mathbf z}\) 为固定方向，因此对

\[
(\mathbf s+\mathbf a)\cdot\hat{\mathbf z}=0
\]

关于时间求导，得到

\[
(\dot{\mathbf s}+\dot{\mathbf a})\cdot\hat{\mathbf z}=0.
\]

\(\mathbf a\) 固连于刚体，所以在惯性系中

\[
\dot{\mathbf a}=\boldsymbol\omega\times\mathbf a.
\]

于是

\[
(\dot{\mathbf s}+\boldsymbol\omega\times\mathbf a)\cdot\hat{\mathbf z}=0.
\]

括号内正是接触点速度 \(\mathbf v_A\)，故

\[
\boxed{\mathbf v_A\cdot\hat{\mathbf z}=0}.
\]

## 易错点

- \(\mathbf a\) 固连于刚体不等于它在惯性系中的导数为零；应使用
  \(\dot{\mathbf a}=\boldsymbol\omega\times\mathbf a\)。
- 结论只说明接触点没有穿入或离开地面的法向速度，不代表其水平速度为零。

## 30 秒自测

若地面本身以竖直速度 \(u\) 运动，接触约束求导后的右端应改成什么？

## 教师审计

- 与 APhO 2019 Q3-A3 官方评分点一致：对接触条件求导，再识别
  \(\dot{\mathbf s}+\boldsymbol\omega\times\mathbf a=\mathbf v_A\)。
