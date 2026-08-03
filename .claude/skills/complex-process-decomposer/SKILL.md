---
name: complex-process-decomposer
description: Decompose complex, multi-stage, stateful, cyclic, cross-module, or multi-agent problems into a bounded process model, executable atomic-task DAG, verification obligations, state-transfer contracts, feedback loops, termination rules, and aggregation plan. Use when users ask to analyze or break down a complex process, produce an atomic Work-Tree, allocate work across N agents, control hypothesis-test-revise loops, diagnose where rollback should occur, or turn an ambiguous workflow into auditable execution units. Do not use for simple one-step questions, ordinary explanations, creative brainstorming without execution needs, or as a substitute for domain-specific truth verification.
---

# Complex Process Decomposer

Convert a complex problem into an executable and auditable plan without pretending that decomposition proves the domain conclusions.

## Core Boundary

Keep three layers separate:

```text
process model     = how the target system changes
task DAG          = how workers or agents produce and verify outputs
control loop      = how failures, challenges, and hypotheses trigger bounded revision
```

The final accepted reasoning or delivery graph must be acyclic. Feedback loops may exist only in the control plane and must re-enter through a named task, changed input version, and explicit stop condition.

## Workflow

### 1. Fix Scope and Truth Policy

Record:

- the target object and requested deliverable;
- authoritative inputs and source policy;
- assumptions, constraints, and excluded scope;
- what counts as success;
- what remains a human or domain-expert decision.

Do not silently turn an analysis request into execution. Do not claim domain correctness when no valid verifier or evidence source exists.

### 2. Classify the Problem

Identify:

- process shape: `temporal`, `stateful`, `logical`, `workflow`, or `hybrid`;
- epistemic family: `formal`, `empirical`, `causal`, `engineering`, `normative`, `procedural`, or `hybrid`;
- risk sources: missing inputs, ambiguous boundaries, incompatible interfaces, irreversible actions, uncertain evidence, or interacting constraints.

Read [references/domain-adapters.md](references/domain-adapters.md) whenever validity standards differ across domains or the problem is hybrid.

### 3. Extract Stable Targets

Create one target for every explicit deliverable or decision. Give each target:

- a stable ID;
- a precise description;
- observable success criteria;
- priority and risk;
- required evidence or approval.

Do not merge targets that can fail independently. Add an explicit completeness target when the prompt implies enumeration, coverage, uniqueness, or “all valid cases.”

### 4. Build the Process Model

Model the system being analyzed, not the Agent's thoughts.

For each process node, record:

- incoming state;
- operation or event;
- outgoing state;
- invariants;
- entry and exit conditions.

For each transition, record its trigger and carried state. If the problem has no temporal process, use a logical or workflow model and say so.

### 5. Build the Atomic-Task DAG

An atomic task must:

- use one primary cognitive verb;
- consume a frozen input snapshot;
- emit one independently checkable artifact;
- declare dependencies and affected targets;
- name a verifier route;
- fail without mutating accepted shared state;
- have a maximum attempt count and terminal status;
- be invalidatable without rerunning unrelated work.

Good verbs include:

```text
extract
classify
model
calculate
compare
verify
test
diagnose
challenge
aggregate
render
```

Reject vague tasks such as “think again,” “check everything,” “solve the whole problem,” or “ask another Agent.”

Topologically group independent tasks into execution waves. Assign roles, not personalities. Only delegate or spawn agents when the user authorizes it and the runtime supports it; otherwise output the allocation plan.

### 6. Define State Transfer and Interfaces

For every dependency edge, specify:

- exact artifact passed;
- schema or expected shape;
- version or fingerprint;
- preconditions;
- consumer;
- invalidation rule.

Treat interface compatibility as its own verification obligation. Never rely on prose such as “the next task uses the result.”

### 7. Create Verification Obligations

Attach at least one verification obligation to every target and every high-risk interface.

Each obligation must state:

- the claim or property being checked;
- required evidence;
- verification method;
- acceptable tolerance or decision rule;
- plausible defeaters;
- fallback when verification is unavailable.

Use deterministic checks before semantic review when they are valid. Do not use majority vote as proof. Domain-specific correctness requires a domain adapter or expert; otherwise mark it `untested`, `provisional`, or `unsupported`.

### 8. Design Bounded Feedback

Represent every challenge as:

```text
specific target or interface
→ observed conflict
→ evidence needed
→ smallest affected dependency cone
→ named retry task
```

Keep hypotheses in an isolated pool. A hypothesis must be relevant, novel, and falsifiable before it may schedule a test. It cannot update accepted results directly.

Enforce:

- task fingerprints to suppress duplicates;
- version changes before retry;
- a maximum global round count;
- a maximum attempt count per task;
- a no-progress fuse;
- rollback only over the dependency impact cone;
- explicit terminal states.

Correctness may justify additional work, but it does not justify an unbounded loop. When evidence remains insufficient, preserve the unresolved state instead of manufacturing certainty.

### 9. Define Aggregation

Aggregate only artifacts that passed their declared checks. State:

- how target results combine;
- how conflicts are adjudicated;
- which conditions and uncertainties must survive;
- what blocks finalization;
- what can be delivered as provisional;
- who has final approval authority.

Separate truth-bearing outputs from presentation outputs. A renderer may reorganize accepted content but must not introduce new claims.

### 10. Emit the Work-Tree

Default output:

1. scope and assumptions;
2. process/state model;
3. atomic-task Work-Tree;
4. execution waves and role allocation;
5. state-transfer contracts;
6. verification ledger;
7. feedback and rollback rules;
8. termination conditions;
9. aggregation and unresolved risks.

When the user needs machine execution or handoff, also emit JSON conforming to [references/output-contract.md](references/output-contract.md). Save it to a file only when requested or when producing an implementation artifact, then run:

```bash
python3 scripts/validate_decomposition.py path/to/decomposition.json
```

Read [references/examples.md](references/examples.md) when the boundary between a process node, atomic task, verification obligation, and feedback action is unclear.

## Quality Gate

Before returning, verify:

- every explicit target is present;
- every target has success criteria and a verification obligation;
- every task has one main verb and a checkable output;
- the task graph is acyclic and has no dangling dependency;
- independent tasks are parallelized in the plan;
- every state transfer names an artifact and consumer;
- every feedback edge has a trigger, retry target, and stop rule;
- hypotheses cannot promote themselves;
- unresolved evidence remains visible;
- no domain conclusion is labeled verified without an appropriate verifier;
- the final aggregation preserves conditions, conflicts, and approval boundaries.

If the problem cannot be decomposed from the available material, return `unsupported` with the missing inputs and the smallest next probe.
