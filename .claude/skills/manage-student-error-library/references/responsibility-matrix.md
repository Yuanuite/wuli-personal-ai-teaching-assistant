# Responsibility matrix

There is one lifecycle owner, one transport/execution boundary, and one optional specialist.

| Capability | `manage-student-error-library` | Agent Gateway | `build-physics-simulator` |
|---|---|---|---|
| Discover uploads, hash, deduplicate, OCR | Owns | Never | Never |
| Inspect/correct question transcription | Owns | Transports a scoped request only; never approves source | Reads final question only |
| Retrieve prior errors and record metadata | Owns | May receive copied, allowlisted context | Never |
| Student/teacher answer Markdown | Owns truth, templates, validation, and review state | Produces validated candidate replacements only | Supplies validated physics model data |
| PDF and student delivery package | Owns | Never | Never |
| `physics-model.json` envelope and teaching fields | Owns `schema_version`, `entry_id`, `title`, `source`, `technique_ids`, `student_solution`, `teacher_audit` | Transports a scoped candidate; cannot redefine ownership | Checks consistency; never creates a second teaching-answer source |
| `physics-model.json` physics/simulation fields | Stores and coordinates | Produces a candidate through the simulator Skill context and validator | Owns `model_type`, `regions`, `facts`, `event_model`, `trajectory`, `simulation` semantics and validation |
| Interactive HTML/ZIP | Calls specialist | Never edits generated HTML/ZIP | Owns |
| Staged visualization lifecycle, teacher approval digest, exact delivery copy | Owns | Never approves or advances lifecycle | Supplies deterministic validated artifacts; never self-approves |
| Provider selection, capability probe, timeout, candidate isolation | Defines privacy/scope policy | Owns execution mechanics | Never |
| Background Agent job status | Interprets result and resumes lifecycle | Owns private queued/running/completed/failed record | Never |
| Date-folder local view and web rename | Owns without moving canonical entries | Never | Never |
| Knowledge index/review schedule | Owns | Never | Never |

`physics-model.json` is the integration contract, not a second knowledge-base record. A validator may inspect fields owned by the other Skill to prove cross-field consistency, but inspection does not transfer write ownership. The Gateway may move a provider's candidate across this boundary only after allowlist and domain validation; it must not approve source/answer/visualization, rebuild retrieval, deliver, or publish. The simulator must not OCR, finalize entries, render PDFs, or maintain answer files. The library must not implement Canvas trajectory code or simulator packaging.
