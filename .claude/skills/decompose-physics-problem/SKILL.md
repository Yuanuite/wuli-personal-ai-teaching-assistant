---
name: decompose-physics-problem
description: Decompose a reviewed complex high-school physics question into a bounded physical-process graph, reasoning-plan graph, retrieval needs, and verification obligations. Use inside the wrong-question analysis pipeline after deterministic complexity screening, especially for multi-stage, multi-region, multi-object, uniqueness, first-event, extremum, or classification problems. Do not solve the final answer, write teaching Markdown, retrieve historical answers, or approve artifacts.
---

# Decompose Physics Problem

Produce a concise planning artifact for another Agent. Do not produce the final numerical answer.

## Workflow

1. Read only the reviewed question and an existing `physics-model.json` when supplied.
2. List every explicit sub-question as a stable target.
3. Build the physical-process graph: stages, state carried between stages, transition event, and entry/exit conditions.
4. Build the reasoning graph: the shortest high-school-level operations needed to answer each target.
5. Link reasoning steps to physical stages and targets.
6. Group retrieval needs by transferable method or condition; merge overlap and rank by importance.
7. State verification obligations for completeness, uniqueness, boundary cases, directions, dimensions, or option verdicts.
8. Return only the structured object required by the caller.

## Boundaries

- Keep physical stages distinct from reasoning steps.
- Use 1–12 targets, 0–16 stages, 1–16 reasoning steps, 1–8 retrieval needs, and 1–16 obligations.
- A static one-stage problem may use no physical stage, but it still needs reasoning steps.
- Do not expose hidden chain-of-thought. Store only short operation labels, decisive relations, dependencies, and conditions.
- Do not copy or infer a historical answer.
- Do not force the final solver to follow a mistaken plan. The solver may report an equivalent revision while preserving all targets and obligations.
- If the question cannot be decomposed from the reviewed text, return `unsupported` with a short reason.

Read [references/examples.md](references/examples.md) when a multi-stage or uniqueness problem needs a concrete shape example.
