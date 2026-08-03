# Shape Examples and Anti-Patterns

## Contents

1. Assembly process
2. Software incident
3. Research audit
4. Anti-patterns

## 1. Assembly Process

Request:

> Break down assembling a vehicle from verified components. A module may pass alone but fail during whole-system integration.

Correct separation:

```text
Process model
parts verified
→ module assembled
→ module test
→ system integration
→ compatibility test
→ operational acceptance

Task DAG
extract interface contracts
→ verify each module
→ verify cross-module interfaces
→ integrate
→ run system tests
→ aggregate acceptance evidence
```

Feedback:

```text
compatibility failure
→ issue a challenge bound to the failing interface
→ diagnose the smallest dependency cone
→ revise the component, adapter, or assembly order
→ invalidate only affected tests
→ rerun bounded verification
```

Do not send every failure back to “assemble the vehicle again.”

## 2. Software Incident

Request:

> The upload succeeds but the generated candidate is rejected by the file gateway. Decompose the diagnosis.

Useful atomic tasks:

```text
A1 reproduce the rejection with frozen inputs
A2 extract allowed paths
A3 extract denied paths
A4 compare the two contracts
A5 trace the candidate write
A6 classify the failure as orchestration, provider, or content
A7 verify the smallest configuration repair
A8 run regression cases
```

`A2`, `A3`, and `A5` may run in parallel after `A1`. A provider retry is not justified until contract incompatibility is ruled out.

## 3. Research Audit

Request:

> Audit whether a paper's model supports its causal conclusion and proposed deployment.

Targets:

```text
T1 formal model is internally coherent
T2 empirical findings match the reported data
T3 causal interpretation is warranted
T4 deployment claim survives operational constraints
```

These targets require different verifier routes. Do not aggregate them into “the paper is correct.” A failure in T3 does not erase a valid T1 result, and a valid T2 result does not prove T3.

## 4. Anti-Patterns

### One giant task

```text
BAD: Analyze everything and give the correct answer.
GOOD: Separate target extraction, modeling, verification, conflict diagnosis, and aggregation.
```

### Fake atomicity

```text
BAD: Verify the model, fix all errors, and rewrite the report.
GOOD: One task produces one checkable artifact.
```

### Unbounded cognitive loop

```text
BAD: Keep questioning previous work until confident.
GOOD: Bind a challenge to a target, require new evidence, retry a named task, and stop on no progress.
```

### Majority truth

```text
BAD: Three Agents agree, therefore the claim is correct.
GOOD: Agreement may prioritize review; acceptance requires a decisive verifier or explicit human judgment.
```

### Domain-free verification

```text
BAD: The generic orchestrator marks a physics, causal, or legal claim verified.
GOOD: The orchestrator routes the claim to an appropriate adapter or preserves it as untested.
```
