---
name: build-physics-simulator
description: Create, repair, package, and validate offline interactive HTML simulations for physics teaching from a reviewed question and shared physics-model.json. Own field/event/trajectory semantics, Canvas/SVG interaction, progressive layers, timing, and HTML/ZIP validation. Use for dynamic physics processes such as forces, fields, trajectories, graphs, circuits, and multi-region motion. Do not OCR, manage the knowledge base, write final answer Markdown, or generate PDFs.
---

# Build Physics Simulator

This Skill is an optional specialist invoked by the question lifecycle owner. It owns the physical event/trajectory model and offline simulator only. Read the reviewed source and return model plus simulator artifacts; never finalize a library entry or produce PDF/answer files.

## Explicit request trigger

The normal answer-generation pass must not invoke this Skill automatically. Invoke it when the teacher explicitly requests an interactive result, for example “我想为这道题生成一个可视化结果”, or when repairing an already generated simulator. A missing `physics-model.json` means “not generated yet”, not “unsuitable”. On a first request, independently derive and create the model; on later requests, repair the existing model. If the available deterministic renderer truly cannot represent the requested process, return `unsupported` with the reason instead of substituting a static answer SVG or an incorrect template.

## Inputs and outputs

Required input:

- visually reviewed question and source figure;
- entry directory for assets;
- explicit stopping condition and requested parameters.

Owned outputs:

- physics/simulation fields in `physics-model.json` as the integration contract;
- simulator fragment in entry assets;
- self-contained offline HTML/ZIP;
- simulator validation report.

The lifecycle owner writes `source`, `technique_ids`, `student_solution`, and `teacher_audit`; this Skill writes `model_type`, `regions`, `facts`, `event_model`, optional `trajectory`, and `simulation`. It may validate all fields for consistency but must not silently rewrite fields owned by the lifecycle owner. The library Skill consumes the teaching fields to render answer Markdown. Do not render those Markdown files here.

## Model first

For charged-particle or multi-region field questions, read [references/physics-model-checklist.md](references/physics-model-checklist.md) and [references/physics-model-schema.md](references/physics-model-schema.md).

1. Re-read the source; OCR and handwriting are not ground truth.
2. Independently solve force direction, energy/work, geometry, all answer cases, and the deadline event.
3. Retrieve applicable high-school shortcuts:

```bash
python3 <skill-dir>/scripts/retrieve_techniques.py "<题干与条件>" --top-k 5
```

Use a conclusion only when all `conditions` hold and no `forbidden` condition applies.

4. Write one `physics-model.json`. Answers, cases, event labels, stopping event, trajectory parameters, and UI presets must come from it, never from duplicated HTML constants.
5. Validate before rendering:

```bash
python3 <skill-dir>/scripts/validate_physics_model.py <entry>/physics-model.json
```

“Before the third entry” is a deadline containing every earlier phase, not merely the interval immediately before entry three. Enumerate every candidate crossing and its earliest event.

## Interaction contract

Use Canvas for dense motion and SVG for small exact diagrams. Keep the complete context available but progressively disclosed:

- layer 1: boundaries, particle, traversed trajectory;
- layer 2: fields, charge sign, velocity and force vectors;
- layer 3: circle centers, radii, angles, construction lines;
- play/pause, replay, adjustable duration and scrubber;
- previous/next event and optional auto-pause;
- parameter presets for every valid case;
- zoom, wheel/pinch, drag-to-pan, and fit;
- exact current phase and stopping condition.

Auto-fit after model changes. Do not reset user pan/zoom during normal playback.

## One-command build

Supported model types must use the deterministic builder:

| `model_type` | Renderer | Use when |
|---|---|---|
| `concentric-radial-multi-field` | guided charged-particle renderer | concentric/circular multi-region fields with repeated region crossings |
| `opposite-circular-magnetic` | field trajectory renderer | circular magnetic regions with opposite field directions and return/closure questions |
| `electric-to-bounded-magnetic` | field trajectory renderer | electric-field acceleration followed by bounded magnetic circular motion |
| `planar-magnetic-multi-particle` | planar magnetic multi-particle renderer | adjacent half-plane magnetic regions, multiple charged particles, circular-arc ledgers, and meeting events |

```bash
python3 <skill-dir>/scripts/build_simulator.py \
  <entry>/physics-model.json \
  --entry-dir <entry> \
  --output-dir <staging>/simulation \
  --name physics-simulator --zip --runtime-check auto
```

The builder selects the renderer by `model_type`, embeds the model, exports offline files, runs JSON Schema and physics validation, HTML/JavaScript checks, ZIP integrity checks, and a browser runtime check when available. Do not invent new `model_type` values inside an Agent task. If none of the supported types can represent the requested process, return `unsupported` with the supported list and the missing renderer requirement instead of writing a plausible but unbuildable model. Use `--runtime-check required` when browser validation is a hard gate, or `skip` only when the caller explicitly requests a static preflight.

When invoked by the error-library lifecycle, the lifecycle owner chooses the staging directory, records the teacher's artifact-digest approval, and copies the approved bytes into delivery. This Skill must not rebuild a different last-minute version during `finish`, edit the teacher approval record, or approve itself.

## Portability and validation

Read [references/html-quality-checklist.md](references/html-quality-checklist.md). The final HTML must:

- open directly via `file://`;
- contain no fetch, CDN, remote font, iframe, module, or server dependency;
- use scoped unique IDs and responsive native controls;
- show complete field symbols and essential particle/force elements in the relevant layer;
- use ASCII filenames inside ZIP files.

Before reporting completion, verify:

1. no runtime/console errors;
2. all controls update the scene;
3. every valid case reaches the claimed earliest event;
4. a non-solution case does not report success;
5. the exact question deadline is reached;
6. HTML and ZIP static validation pass.

Use `browser_check.mjs` or an available browser tool for runtime inspection. A browser pass proves implementation health, not physical correctness; both model and runtime gates must pass.

## Failure behavior

- Source/model disagreement: mark unresolved and return both interpretations; never guess.
- Unsupported renderer: return `unsupported` with the supported model types.
- Browser dependency unavailable: keep static validation and report `runtime_check.status=skipped` with a reason.
- Required simulator validation failure: return failure to the lifecycle owner; do not allow delivery to be marked complete.

Report model path, physics conclusion, HTML/ZIP paths, validation results, and unresolved source ambiguity.
