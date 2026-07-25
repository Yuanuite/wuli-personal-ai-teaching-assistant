# Layered answer artifact template

Generate both layers from the same structured answer or `physics-model.json`. `student-solution.md` minimizes working-memory load; `teacher-solution.md` adds audit evidence. Keep `solution.md` synchronized with the teacher version for knowledge-base validation.

## Student layer

```markdown
# 解析（学生版）

![关键关系示意图](assets/explanatory.svg)

## 答案速览

- （1）结论；
- （2）结论；
- （3）所有可能值，并标出各值的首次命中事件。

## 一眼识别

- 题型识别：……
- 最短主线：……
- 可用二级结论：……；**适用条件**：……

## 详细解答

### 第 1 步：……

说明依据，定义符号，给出必要公式。

### 第 2 步：……

代数化简，给出带单位、方向或定义域的结果。主线原则上不超过 5 步。

## 易错点

- **错误表现**：……；**纠正策略**：……

## 30 秒自测

遮住答案后回答一个能暴露关键误区的问题：……
```

## Teacher layer

The teacher file contains the full student layer, followed by:

```markdown
## 教师审计

- 符号/方向复核：……
- 量纲、代回或边界情形：……
- 独立几何/代数检查：……
- 枚举完备性与重复情况：……
```

## Quality gates

- Before drafting, compare at least two viable approaches and choose the shortest path that stays inside the high-school syllabus. Record this private method audit in the structured generation result; do not expose discarded chains to students.
- Evidence takes precedence over filling the template. The detailed solution must contain at least one checkable equation, geometric relation, or event chain for every sub-question and every option verdict. Never replace a key step with “并不对应”, “显然可得”, “不难得到”, or “角度账本可得”. If deleting a line would make the conclusion a guess, keep that line.
- Keep supporting sections compact: `一眼识别` has at most three short bullets, `易错点` has at most three items, and `30 秒自测` has one question. They must not repeat the detailed solution or displace its proof.
- In the student layer, prefer geometry, conservation laws, $F-x$ or other graph areas, average-value relations, and standard secondary conclusions over calculus or coordinate expansion. Do not use integrals, derivatives, differential equations, matrices, complex-number methods, or university mechanics. If the only known solution needs one of these, return for teacher review instead of disguising it as a normal high-school derivation.
- Make the quick answer, event table, derivation, and simulation agree exactly.
- State sign conventions such as $U_{ab}=\varphi_a-\varphi_b$ before use.
- Use `$...$` inline and `$$...$$` for display math; never use `\(...\)`. Do not use `\dfrac` or `\tfrac` inline. Put `\boxed` only in display math.
- Do not use `\notag`, `\tag`, or equation-number controls in generated Markdown. Before submission, reject broken escape remnants such as a standalone `otag` line or `aqquad`.
- Show all physically or geometrically distinct cases and explain why no others exist.
- Keep the student main line to at most 5 numbered steps when possible. Move alternate derivations and exhaustive checks to the teacher layer.
- Treat five steps as a ceiling, not a target. Merge mechanical substitutions into one relation, and keep only steps that introduce a new physical idea or necessary state transition.
- State every secondary conclusion's applicability condition next to its first use. Do not use it if a condition fails.
- Define symbols before use. Prefer one compact event/angle table to repeated prose.
- Do not infer the student's actual misconception from markings alone.
- Ensure every Markdown image target exists inside the entry directory.
- Use images to clarify reasoning, not decorate.
- **Do not repeat `![题目原图](assets/original.jpg)` in the solution layer.** The original question image is already shown in `problem.md`; the combined output would display the same image twice. Only include *new* diagrams (SVG trajectory, force analysis, etc.) that did not appear in the problem statement.
