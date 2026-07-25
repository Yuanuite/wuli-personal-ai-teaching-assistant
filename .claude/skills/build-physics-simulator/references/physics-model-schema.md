# `physics-model.json` contract

The machine-readable contract is [physics-model.schema.json](physics-model.schema.json). The Markdown below explains semantic constraints that JSON Schema alone cannot prove.

Use one model file as the source of truth for the student Markdown, teacher Markdown, simulator labels, event timing, and answer cases.

Required top-level keys:

- `schema_version`, `model_type`, `entry_id`, `title`;
- `regions`, `facts`, `technique_ids`;
- `event_model.timeline`, `event_model.cases`, `event_model.stop_event_id`;
- `student_solution`, `teacher_audit`, `simulation`.

For the concentric-field renderer, each timeline event stores its boundary angle as

$$
\theta=c+k\delta,
$$

using `constant_deg=c` and `delta_coefficient=k`. Set `p_candidate=true` only for an event on the inner boundary that may coincide with P. The initial P event is never a repeat crossing.

Each concentric-field case must declare `delta_deg`, `b3_ratio`, `b3_label`, and `first_p_event_id`. The model validator recomputes all P crossings before the stop event and confirms that `first_p_event_id` is the earliest one.

## Piecewise 2D field model

Use `model_type: piecewise-field-particle-2d` for planar charged-particle processes that combine arbitrary field regions and several trajectory primitives.

- `simulation.viewport` declares finite `x_min`, `x_max`, `y_min`, and `y_max` bounds.
- `simulation.particles` declares every particle referenced by a trajectory, including its stable `id`, label, color, and optional charge sign.
- `regions` may use `rect`, `circle`, `half-plane`, or `polygon` geometry. Electric regions declare a direction vector; magnetic regions declare `direction: in|out`.
- `trajectory.segments` uses stable IDs and one of `line`, `arc`, or `polyline`. Every segment declares `particle_id`, `start_time`, and `end_time`; optional `case_ids` restrict it to selected cases.
- Line segments declare `start` and `end`; arc segments declare `center`, positive `radius`, `start_angle_deg`, `end_angle_deg`, and `clockwise`; polylines contain at least two sampled points.
- `event_model.timeline[*].time` may be a single non-negative number or a map from case ID to time. Each case also declares a positive `duration`; referenced segment and event IDs must exist.
- A reflection, field switch, collision, board crossing, capture, or final hit must appear as a timeline event rather than being inferred only from the drawing. Cases that pause automatically declare the event IDs explicitly.

The validator checks geometry references, time ordering, per-case event coverage, particle references, viewport bounds, and stopping-event reachability. It does not infer Lorentz-force correctness from a plausible-looking curve, so the specialist must still independently verify direction, radius, speed, and earliest-event claims.

## Piecewise 3D field model

Use `model_type: piecewise-field-particle-3d` only when a third spatial coordinate materially changes the physics, such as an out-of-plane electric displacement, an oblique magnetic field, a helical orbit, a cylindrical collector, or coupled particle separation in space.

- `facts.axes` declares finite 3D `origin` and positive `lengths`; `facts.particles` declares every particle ID, label, sign, and color.
- Regions use `box`, `plane`, `cylinder`, or generic `wireframe` shapes. Field vectors are finite three-component `direction` arrays and may declare their visible arrow origin.
- Every timeline event declares an exact three-component `position`. Its `time` may be one number or a case-ID map.
- Three-dimensional trajectories keep the schema-level segment `type: polyline`, while `geometry.path_kind` records the analytic source: `line`, `quadratic`, `arc3d`, `helix`, or explicit `points`.
- `quadratic` stores start, velocity, acceleration, and physical duration. `arc3d` stores a center, two in-plane basis vectors, radius, and angular interval. `helix` additionally stores the axis and total axial advance.
- Each case declares a positive `duration` and a conclusion. The camera target, initial yaw/pitch, and scale live under `simulation.camera`; they affect presentation only, never physics.

The validator checks all 3D vectors, region geometry, particle/case/event references, time order, analytic path parameters, and stopping-event reachability. The specialist must still verify the right-hand-rule direction, tangent continuity, field-parallel velocity, endpoint coordinates, impact boundary, and any extremum or first-hit claim independently.

Put student-facing shortest-path content in `student_solution`. Put redundancy checks, excluded branches, and independent verification in `teacher_audit`; do not duplicate them into the student main route.

## Field ownership

- The lifecycle owner writes the envelope and teaching fields: `schema_version`, `entry_id`, `title`, `source`, `technique_ids`, `student_solution`, and `teacher_audit`.
- The simulator specialist writes the physics and interaction fields: `model_type`, `regions`, `facts`, `event_model`, optional `trajectory`, and `simulation`.
- Either side may validate the complete document, but must not silently rewrite fields owned by the other side.
