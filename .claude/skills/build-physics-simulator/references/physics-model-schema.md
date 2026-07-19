# `physics-model.json` contract

The machine-readable contract is [physics-model.schema.json](physics-model.schema.json). The Markdown below explains semantic constraints that JSON Schema alone cannot prove.

Use one model file as the source of truth for the student Markdown, teacher Markdown, simulator labels, event timing, and answer cases.

Required top-level keys:

- `schema_version`, `model_type`, `entry_id`, `title`;
- `regions`, `facts`, `technique_ids`;
- `event_model.timeline`, `event_model.cases`, `event_model.stop_event_id`;
- `student_solution`, `teacher_audit`, `simulation`.

Each timeline event stores its boundary angle as

$$
\theta=c+k\delta,
$$

using `constant_deg=c` and `delta_coefficient=k`. Set `p_candidate=true` only for an event on the inner boundary that may coincide with P. The initial P event is never a repeat crossing.

Each case must declare `delta_deg`, `b3_ratio`, `b3_label`, and `first_p_event_id`. The model validator recomputes all P crossings before the stop event and confirms that `first_p_event_id` is the earliest one.

Put student-facing shortest-path content in `student_solution`. Put redundancy checks, excluded branches, and independent verification in `teacher_audit`; do not duplicate them into the student main route.

## Field ownership

- The lifecycle owner writes the envelope and teaching fields: `schema_version`, `entry_id`, `title`, `source`, `technique_ids`, `student_solution`, and `teacher_audit`.
- The simulator specialist writes the physics and interaction fields: `model_type`, `regions`, `facts`, `event_model`, optional `trajectory`, and `simulation`.
- Either side may validate the complete document, but must not silently rewrite fields owned by the other side.
