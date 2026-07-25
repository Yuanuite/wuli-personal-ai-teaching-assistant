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
- `trajectory.segments` uses stable IDs and a trajectory primitive type. Every segment declares `particle_id`, `kinematics.start_time`, `kinematics.end_time`, and optional `case_ids`, `force_direction`, and `label`.
- `event_model.timeline[*].time` may be a single non-negative number or a map from case ID to time. Each case also declares a positive `duration`; referenced segment and event IDs must exist.
- A reflection, field switch, collision, board crossing, capture, or final hit must appear as a timeline event rather than being inferred only from the drawing. Cases that pause automatically declare the event IDs explicitly.

The validator checks geometry references, time ordering, per-case event coverage, particle references, viewport bounds, and stopping-event reachability. It does not infer Lorentz-force correctness from a plausible-looking curve, so the specialist must still independently verify direction, radius, speed, and earliest-event claims.

### 2D trajectory types

| type | required geometry fields | sampler formula | use case |
|------|------------------------|----------------|----------|
| `line` | `start`, `end` | linear interpolation | uniform motion, straight segment |
| `arc` | `center`, `radius`, `start_deg`, `end_deg` | $p(a) = c + r(\hat u\cos a + \hat v\sin a)$ where $a\in[\text{start\_deg},\text{end\_deg}]$ in degrees | circular motion in a magnetic field |
| `polyline` | `points` (array of [x,y]) | pre-sampled points | hand-crafted or numerical trajectory |
| `parabola` | `start`, `velocity` [vx,vy], `acceleration` [ax,ay], `duration` | $p(t) = p_0 + \vec v\,t + \frac12\vec a\,t^2$, $t\in[0,\text{duration}]$ | uniform acceleration / projectile motion in an electric field |

**Parabola example** (charged particle in a uniform electric field):
```json
{
  "id": "projectile",
  "type": "parabola",
  "geometry": {
    "start": [0, 0],
    "velocity": [3.14, 0],
    "acceleration": [0, 8],
    "duration": 0.5
  }
}
```

The renderer samples 120 points from the analytic formula. Velocity and force arrows are computed from the tangent, so they update smoothly throughout playback.

**Arc direction convention**: start_deg and end_deg are in degrees. The arc sweeps from start to end in the **increasing angle** direction (counter-clockwise in screen coordinates). For clockwise arcs, swap start/end or use start_deg > end_deg (e.g., start_deg=180, end_deg=-60 sweeps 240° clockwise).

### Time-dependent field direction

When a region's field direction changes with time (e.g., square-wave electric field), override `region.field.vector` at the start of each render frame:

```javascript
// Inside draw(), before drawShape loop:
const pField = model.regions.find(r => r.id === "plate-field");
if (pField && time >= 1)
    pField.field.vector = ((time - 1) % 1) < 0.5 ? [0, 1] : [0, -1];
```

The pattern is:
1. Identify the region by stable `id`.
2. Check that time has entered the relevant window.
3. Use a periodic condition (`(time - offset) % period < threshold`) to toggle direction.
4. The `fieldSymbol` function reads `region.field.vector` each frame, so the arrow updates visually.
5. Timeline events should mark each switch point (e.g., `mid-plate-1` at the first transition).

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
