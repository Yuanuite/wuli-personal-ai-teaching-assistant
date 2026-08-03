# Structured Output Contract

Use this contract when the decomposition will be handed to another Agent, stored, validated, or executed by software. Human-readable Work-Trees may omit JSON but must preserve the same semantics.

## Top-Level Shape

```json
{
  "schema": "complex-process-decomposition.v1",
  "status": "completed",
  "problem_profile": {},
  "targets": [],
  "process_model": {},
  "atomic_tasks": [],
  "state_transfers": [],
  "verification_obligations": [],
  "execution_plan": {},
  "control_policy": {},
  "aggregation": {},
  "unresolved": []
}
```

Allowed statuses:

- `completed`: decomposition is executable;
- `provisional`: executable with named unresolved risks;
- `unsupported`: missing information or no valid decomposition boundary;
- `blocked`: execution requires authority or an external-state change.

## Problem Profile

```json
{
  "summary": "Assemble and validate a multi-module system",
  "process_shape": "temporal|stateful|logical|workflow|hybrid",
  "epistemic_families": ["engineering"],
  "source_policy": "provided-material|general-knowledge|externally-verified",
  "authoritative_inputs": ["requirements-v3"],
  "assumptions": [],
  "constraints": [],
  "excluded_scope": []
}
```

## Targets

```json
{
  "id": "T1",
  "description": "Every required module is compatible with the assembly",
  "success_criteria": ["All interface checks pass"],
  "priority": "critical|high|medium|low",
  "required_evidence": ["interface-test-report"],
  "approval": "system|domain-expert|human-owner"
}
```

IDs must be unique and stable within the decomposition.

## Process Model

```json
{
  "kind": "temporal|stateful|logical|workflow|hybrid|none",
  "nodes": [
    {
      "id": "P1",
      "label": "Module assembled",
      "state_in": ["parts-verified"],
      "state_out": ["module-candidate"],
      "invariants": ["No protected interface changed"],
      "entry_conditions": ["Parts available"],
      "exit_conditions": ["Module test complete"]
    }
  ],
  "transitions": [
    {
      "from": "P1",
      "to": "P2",
      "trigger": "module-test-pass",
      "carried_state": ["module-candidate"],
      "rollback_to": "P1"
    }
  ]
}
```

Process transitions may contain cycles because real processes can revisit states. They do not define task dependencies.

## Atomic Tasks

```json
{
  "id": "A1",
  "verb": "verify",
  "description": "Verify the module interface contract",
  "target_ids": ["T1"],
  "process_node_ids": ["P1"],
  "depends_on": [],
  "input_artifacts": ["module-candidate"],
  "output_artifact": "interface-test-report",
  "output_contract": "interface-test.v1",
  "verifier_route": "deterministic-interface-check",
  "assigned_role": "interface-verifier",
  "max_attempts": 1,
  "terminal_states": ["passed", "failed", "unsupported"]
}
```

`verb` must be a single lowercase action word. The `depends_on` graph must be acyclic. Every target must be covered by at least one task.

## State Transfers

```json
{
  "id": "S1",
  "producer_task_id": "A1",
  "consumer_task_id": "A2",
  "artifact": "interface-test-report",
  "contract": "interface-test.v1",
  "preconditions": ["A1 passed"],
  "invalidation_rule": "Invalidate when module-candidate fingerprint changes"
}
```

Every transfer must bind existing producer and consumer tasks. The consumer must depend directly or transitively on the producer.

## Verification Obligations

```json
{
  "id": "V1",
  "target_ids": ["T1"],
  "task_ids": ["A1"],
  "claim": "The module interface is compatible",
  "required_evidence": ["interface-test-report"],
  "method": "Compare ports, units, ranges, and lifecycle assumptions",
  "decision_rule": "All critical fields match",
  "defeaters": ["Undocumented adapter"],
  "fallback": "provisional",
  "risk": "critical|high|medium|low"
}
```

Every target requires at least one obligation. High-risk interfaces should have their own obligation even when the associated target already has one.

## Execution Plan

```json
{
  "waves": [
    {
      "id": "W1",
      "task_ids": ["A1", "A3"],
      "parallel": true
    },
    {
      "id": "W2",
      "task_ids": ["A2"],
      "parallel": false
    }
  ],
  "max_concurrency": 2,
  "allocation_notes": []
}
```

Every task must appear exactly once. A wave may contain tasks only when all dependencies are in earlier waves or the same wave contains no dependency edge between them.

## Control Policy

```json
{
  "max_global_rounds": 3,
  "max_attempts_per_task": 2,
  "no_progress_limit": 1,
  "duplicate_policy": "reuse-by-fingerprint",
  "challenge_policy": "bound-to-target-or-interface",
  "hypothesis_policy": "isolated-until-tested",
  "rollback_policy": "dependency-impact-cone",
  "allowed_feedback_actions": [
    "retry-task",
    "revise-input",
    "invalidate-impact-cone",
    "mark-provisional",
    "stop"
  ],
  "terminal_states": [
    "completed",
    "provisional",
    "unsupported",
    "blocked"
  ]
}
```

The limits are engineering bounds, not evidence that the conclusion is correct. Choose bounds proportionate to risk and record unresolved correctness when a bound is reached.

## Aggregation

```json
{
  "required_target_ids": ["T1"],
  "required_obligation_ids": ["V1"],
  "acceptance_rule": "All critical obligations pass and every target has an accepted artifact",
  "conflict_policy": "Use a decisive test; do not use majority vote",
  "unresolved_policy": "Preserve conditions and mark provisional",
  "final_artifact": "audited-result",
  "approval_authority": "human-owner"
}
```

## Unsupported Shape

When `status` is `unsupported`, retain `schema`, `status`, `problem_profile`, and `unresolved`. Other collections may be empty. State the missing input and the smallest probe that would make decomposition possible.
