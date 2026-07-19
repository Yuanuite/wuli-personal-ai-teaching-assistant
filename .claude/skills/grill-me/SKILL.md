---
name: grill-me
description: Interview the user relentlessly about a plan or design until reaching shared understanding, resolving each branch of the decision tree. Use when user wants to stress-test a plan, get grilled on their design, or mentions "grill me".
license: MIT
metadata:
  derived_from: "https://github.com/mattpocock/skills/tree/main/skills/productivity/grill-me"
  original_author: "Matt Pocock (@mattpocock)"
  original_license: MIT
  voice: "Relentless, one-at-a-time, explores-codebase-first"
  version: 1.0.1
---

# Grill Me

> Derived from [Matt Pocock's grill-me](https://github.com/mattpocock/skills/tree/main/skills/productivity/grill-me) (MIT). The interview discipline is preserved; this local version runs directly with the Agent's available code-inspection and conversation tools.

Interview me relentlessly about every aspect of this plan until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead.

## Rules (preserved + amplified)

1. **One question per turn.** Never bundle.
2. **Provide a recommended answer with each question.** Defaulting to "what do you think?" is lazy.
3. **Explore the codebase before asking.** If `grep` / `Read` resolves it, do that first. Saves a turn.
4. **Walk the tree depth-first.** Finish a branch before opening another.
5. **Track dependencies.** If decision B depends on decision A, ask A first.

## Workflow

1. User provides a plan or design (or path to one).
2. Inspect the codebase and extract the decision branches from the available evidence.
3. Order questions by dependency and prepare a recommended answer for each branch.
4. Walk the tree one question at a time, retaining resolved decisions in the conversation.
5. When all branches are resolved, report "shared understanding reached" and summarize the locked-in decisions.

## Output Pattern

Per question turn:

```
Q[i]/[total]: [question]
Recommended answer: [your call + 1-sentence rationale]

(Or: I explored the codebase and found [evidence]. Confirm?)
```

---

**Version:** 1.0.1
**Derived:** Matt Pocock (MIT), adapted for direct cross-platform Agent use
