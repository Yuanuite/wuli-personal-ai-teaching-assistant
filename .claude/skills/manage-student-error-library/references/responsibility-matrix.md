# Responsibility matrix

There is one lifecycle owner and one optional specialist.

| Capability | `manage-student-error-library` | `build-physics-simulator` |
|---|---|---|
| Discover uploads, hash, deduplicate, OCR | Owns | Never |
| Inspect/correct question transcription | Owns | Reads final question only |
| Retrieve prior errors and record metadata | Owns | Never |
| Student/teacher answer Markdown | Owns | Supplies validated physics model data |
| PDF and student delivery package | Owns | Never |
| `physics-model.json` envelope and teaching fields | Owns `schema_version`, `entry_id`, `title`, `source`, `technique_ids`, `student_solution`, `teacher_audit` | Checks consistency; never creates a second teaching-answer source |
| `physics-model.json` physics/simulation fields | Stores and coordinates | Owns `model_type`, `regions`, `facts`, `event_model`, `trajectory`, `simulation` semantics and validation |
| Interactive HTML/ZIP | Calls specialist | Owns |
| Staged visualization lifecycle, teacher approval digest, exact delivery copy | Owns | Supplies deterministic validated artifacts; never self-approves |
| Date-folder local view and web rename | Owns without moving canonical entries | Never |
| Knowledge index/review schedule | Owns | Never |

`physics-model.json` is the integration contract, not a second knowledge-base record. A validator may inspect fields owned by the other Skill to prove cross-field consistency, but inspection does not transfer write ownership. The simulator must not OCR, finalize entries, render PDFs, or maintain answer files. The library must not implement Canvas trajectory code or simulator packaging.
