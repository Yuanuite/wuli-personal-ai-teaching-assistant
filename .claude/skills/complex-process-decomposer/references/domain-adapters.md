# Domain Adapter Guide

Use a domain adapter to decide what counts as evidence and verification. The common decomposition protocol organizes work; it does not supply domain truth.

## Selection Table

| Family | Typical objects | Required validity standard | Common verifier routes |
|---|---|---|---|
| Formal | proofs, equations, algorithms, type systems | explicit premises and valid derivation | symbolic checks, theorem tools, invariant tests |
| Empirical | measurements, surveys, observed systems | measurement quality, sampling, uncertainty | statistical analysis, replication, provenance review |
| Causal | interventions, mechanisms, policy effects | identification assumptions and alternatives | causal design review, counterfactual tests |
| Engineering | software, machines, workflows, architecture | requirements, interfaces, failure modes, executable tests | unit/integration tests, simulation, inspection |
| Normative | policy, ethics, prioritization | explicit value premises and stakeholder tradeoffs | consistency checks, scenario comparison, human decision |
| Procedural | operations, craft, laboratory work | observed performance, repeatability, safety | runbooks, demonstrations, recovery drills |
| Hybrid | real projects and research programs | separate standards per claim type | several adapters with explicit boundaries |

## Physics Adapter

Add:

- objects, reference frames, directions, regions, forces, fields, and state variables;
- physical stages and boundary events;
- carried position, velocity, energy, momentum, charge, or circuit state;
- dimensions, units, signs, limits, uniqueness, first-event, and complete-solution obligations;
- deterministic arithmetic, dimensional, interval, and event-order checks;
- independent semantic verification for claims that cannot be formalized.

Do not generalize a relation beyond its stated initial conditions or phase. Distinguish an instantaneous relation from a whole-process invariant.

## Software Debugging Adapter

Map:

- observed symptom;
- minimal reproduction;
- expected and actual state transitions;
- candidate root causes;
- diagnostic probes;
- patch impact cone;
- unit, integration, regression, security, and performance checks.

A hypothesis may schedule a probe but cannot justify a patch. Do not declare success from one passing test when adjacent behavior could regress.

## Research and Argument Audit Adapter

Atomize:

- formal claims;
- empirical findings;
- causal interpretations;
- engineering or scaling claims;
- speculative extensions.

Attach provenance, assumptions, alternatives, scope, and uncertainty to each claim. A citation is not automatically support; verify that the source entails the claim.

## Engineering and Assembly Adapter

Separate:

- component correctness;
- interface compatibility;
- assembly order;
- system integration;
- operational validation;
- rollback or replacement decisions.

When integration fails, diagnose the smallest incompatible interface before rebuilding unrelated components.

## Decision Adapter

Represent:

- options;
- hard constraints;
- value premises;
- affected stakeholders;
- evidence and uncertainty;
- reversible and irreversible consequences.

Do not label a value-dependent choice “verified.” Aggregate only after the decision owner supplies or accepts the weighting rule.

## Hybrid Rule

Split hybrid statements whenever their parts require different warrants. For example:

```text
the algorithm outputs a network
→ formal or engineering check

the network matches observed data
→ empirical check

the network identifies causal targets
→ causal check
```

Strength in one family does not compensate for missing evidence in another.
