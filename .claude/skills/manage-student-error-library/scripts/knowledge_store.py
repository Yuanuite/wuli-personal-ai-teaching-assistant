#!/usr/bin/env python3
"""Local derived SQLite knowledge store for Wuli.

The store is a rebuildable retrieval and evidence layer.  Canonical truth stays
in entry Markdown/JSON files, ``evaluation.json``, and ``candidate-archive.jsonl``.
Deleting ``indexes/wuli-memory.db`` must never lose teaching data.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import candidate_archive
import kb

SCHEMA_VERSION = 1
DEFAULT_DB_RELATIVE = Path("indexes") / "wuli-memory.db"
DOCUMENT_KINDS = (
    "problem",
    "solution",
    "student_solution",
    "teacher_solution",
    "source_review",
    "physics_model",
)


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def db_path(root: Path, explicit: Path | None = None) -> Path:
    return explicit.expanduser().resolve() if explicit else (root / DEFAULT_DB_RELATIVE).resolve()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _fts_available(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(content)")
        connection.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def init_schema(connection: sqlite3.Connection) -> bool:
    has_fts = _fts_available(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entry (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          subject TEXT NOT NULL,
          grade TEXT NOT NULL,
          kind TEXT NOT NULL,
          status TEXT NOT NULL,
          library_folder TEXT NOT NULL,
          created_at TEXT,
          updated_at TEXT,
          knowledge_points_json TEXT NOT NULL,
          error_types_json TEXT NOT NULL,
          source_path TEXT,
          has_evaluation INTEGER NOT NULL DEFAULT 0,
          candidate_event_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS document (
          entry_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          path TEXT NOT NULL,
          title TEXT NOT NULL,
          content TEXT NOT NULL,
          token_text TEXT NOT NULL,
          updated_at TEXT,
          PRIMARY KEY (entry_id, kind),
          FOREIGN KEY (entry_id) REFERENCES entry(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS evaluation (
          entry_id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          generated_at TEXT,
          scores_json TEXT NOT NULL,
          summary_json TEXT NOT NULL,
          failure_reasons_json TEXT NOT NULL,
          warning_reasons_json TEXT NOT NULL,
          teacher_review_required INTEGER NOT NULL,
          FOREIGN KEY (entry_id) REFERENCES entry(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS candidate_event (
          event_id TEXT PRIMARY KEY,
          entry_id TEXT NOT NULL,
          task_type TEXT NOT NULL,
          actor TEXT NOT NULL,
          event_type TEXT NOT NULL,
          status TEXT NOT NULL,
          raw_status TEXT NOT NULL,
          created_at TEXT,
          summary TEXT NOT NULL,
          changed_files_json TEXT NOT NULL,
          failure_reasons_json TEXT NOT NULL,
          evaluation_json TEXT NOT NULL,
          FOREIGN KEY (entry_id) REFERENCES entry(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS teaching_memory (
          entry_id TEXT PRIMARY KEY,
          knowledge_points_json TEXT NOT NULL,
          error_types_json TEXT NOT NULL,
          difficulty TEXT,
          methods_json TEXT NOT NULL,
          secondary_conclusions_json TEXT NOT NULL,
          visualizable INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT,
          FOREIGN KEY (entry_id) REFERENCES entry(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS scheduler_benchmark (
          event_id TEXT PRIMARY KEY,
          created_at TEXT,
          task_type TEXT NOT NULL,
          status TEXT NOT NULL,
          summary TEXT NOT NULL,
          request_json TEXT NOT NULL,
          report_json TEXT NOT NULL,
          failure_reasons_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evolve_observation (
          event_id TEXT PRIMARY KEY,
          created_at TEXT,
          observation_type TEXT NOT NULL,
          status TEXT NOT NULL,
          summary TEXT NOT NULL,
          request_json TEXT NOT NULL,
          report_json TEXT NOT NULL,
          failure_reasons_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entry_status ON entry(status);
        CREATE INDEX IF NOT EXISTS idx_entry_subject ON entry(subject);
        CREATE INDEX IF NOT EXISTS idx_event_entry_time ON candidate_event(entry_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_scheduler_benchmark_time ON scheduler_benchmark(created_at);
        CREATE INDEX IF NOT EXISTS idx_evolve_observation_time ON evolve_observation(created_at);
        """
    )
    if has_fts:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS document_fts
            USING fts5(entry_id UNINDEXED, kind UNINDEXED, title, content, token_text)
            """
        )
    connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts5', ?)", ("1" if has_fts else "0",))
    return has_fts


def _document_payloads(entry: Path, record: dict[str, Any]) -> list[dict[str, str]]:
    files = {
        "problem": "problem.md",
        "solution": "solution.md",
        "student_solution": "student-solution.md",
        "teacher_solution": "teacher-solution.md",
        "source_review": "source-review.md",
        "physics_model": "physics-model.json",
    }
    title = str(record.get("title", entry.name))
    payloads: list[dict[str, str]] = []
    for kind, name in files.items():
        path = entry / name
        if not path.exists():
            continue
        content = _read_text(path)
        if not content.strip():
            continue
        tokens = " ".join(kb.tokenize(" ".join([title, content])))
        payloads.append({
            "entry_id": entry.name,
            "kind": kind,
            "path": name,
            "title": title,
            "content": content,
            "token_text": tokens,
            "updated_at": record.get("updated_at") or "",
        })
    return payloads


def _evaluation_summary(report: dict[str, Any]) -> dict[str, Any]:
    checks = report.get("checks", [])
    return {
        "status": report.get("status", "missing"),
        "scores": report.get("scores", {}),
        "failed_checks": [item.get("id") for item in checks if item.get("status") == "failed"],
        "warning_checks": [item.get("id") for item in checks if item.get("status") == "warning"],
        "teacher_review_required": bool(report.get("teacher_review_required", True)),
    }


def rebuild(root: Path, explicit_db: Path | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve()
    target = db_path(root, explicit_db)
    connection = connect(target)
    try:
        has_fts = init_schema(connection)
        connection.execute("DELETE FROM candidate_event")
        connection.execute("DELETE FROM evaluation")
        connection.execute("DELETE FROM teaching_memory")
        connection.execute("DELETE FROM scheduler_benchmark")
        connection.execute("DELETE FROM evolve_observation")
        connection.execute("DELETE FROM document")
        connection.execute("DELETE FROM entry")
        if has_fts:
            connection.execute("DELETE FROM document_fts")

        entry_count = 0
        document_count = 0
        event_count = 0
        evaluation_count = 0
        benchmark_count = 0
        observation_count = 0
        for entry in kb.entry_dirs(root):
            record = kb.load_json(entry / "record.json", {}) or {}
            events = candidate_archive.read_events(entry)
            evaluation = kb.load_json(entry / "evaluation.json", {}) or {}
            stored_files = record.get("source", {}).get("stored_files", []) or []
            source_path = str(stored_files[0]) if stored_files else ""
            connection.execute(
                """
                INSERT INTO entry(
                  id, title, subject, grade, kind, status, library_folder,
                  created_at, updated_at, knowledge_points_json, error_types_json,
                  source_path, has_evaluation, candidate_event_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.name,
                    str(record.get("title", entry.name)),
                    str(record.get("subject", "")),
                    str(record.get("grade", "")),
                    str(record.get("kind", "error")),
                    str(record.get("status", "needs-review")),
                    str(record.get("library_folder", kb.default_library_folder(record, entry.name))),
                    record.get("created_at"),
                    record.get("updated_at"),
                    _json(record.get("knowledge_points", [])),
                    _json(record.get("error_types", [])),
                    source_path,
                    1 if evaluation else 0,
                    len(events),
                ),
            )
            entry_count += 1

            for document in _document_payloads(entry, record):
                connection.execute(
                    """
                    INSERT INTO document(entry_id, kind, path, title, content, token_text, updated_at)
                    VALUES (:entry_id, :kind, :path, :title, :content, :token_text, :updated_at)
                    """,
                    document,
                )
                if has_fts:
                    connection.execute(
                        """
                        INSERT INTO document_fts(entry_id, kind, title, content, token_text)
                        VALUES (:entry_id, :kind, :title, :content, :token_text)
                        """,
                        document,
                    )
                document_count += 1

            if evaluation:
                connection.execute(
                    """
                    INSERT INTO evaluation(
                      entry_id, status, generated_at, scores_json, summary_json,
                      failure_reasons_json, warning_reasons_json, teacher_review_required
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.name,
                        str(evaluation.get("status", "unknown")),
                        evaluation.get("generated_at"),
                        _json(evaluation.get("scores", {})),
                        _json(_evaluation_summary(evaluation)),
                        _json(evaluation.get("failure_reasons", [])),
                        _json(evaluation.get("warning_reasons", [])),
                        1 if evaluation.get("teacher_review_required", True) else 0,
                    ),
                )
                evaluation_count += 1

            for event in events:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO candidate_event(
                      event_id, entry_id, task_type, actor, event_type, status, raw_status,
                      created_at, summary, changed_files_json, failure_reasons_json, evaluation_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("event_id"),
                        entry.name,
                        str(event.get("task_type", "")),
                        str(event.get("actor", "")),
                        str(event.get("event_type", "")),
                        str(event.get("status", "")),
                        str(event.get("raw_status", "")),
                        event.get("created_at"),
                        str(event.get("summary", "")),
                        _json(event.get("changed_files", [])),
                        _json(event.get("failure_reasons", [])),
                        _json(event.get("evaluation", {})),
                    ),
                )
                event_count += 1

            model = kb.load_json(entry / "physics-model.json", {}) or {}
            model_teaching = model.get("teaching", {}) if isinstance(model, dict) else {}
            connection.execute(
                """
                INSERT INTO teaching_memory(
                  entry_id, knowledge_points_json, error_types_json, difficulty,
                  methods_json, secondary_conclusions_json, visualizable, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.name,
                    _json(record.get("knowledge_points", [])),
                    _json(record.get("error_types", [])),
                    str(record.get("difficulty", "")),
                    _json(record.get("methods", model_teaching.get("methods", []))),
                    _json(record.get("secondary_conclusions", model_teaching.get("secondary_conclusions", []))),
                    1 if (entry / "physics-model.json").exists() else 0,
                    record.get("updated_at"),
                ),
            )

        for event in candidate_archive.read_library_events(root):
            if event.get("entry_id") != candidate_archive.LIBRARY_ENTRY_ID:
                continue
            if event.get("task_type") == "scheduler.benchmark":
                connection.execute(
                    """
                    INSERT OR REPLACE INTO scheduler_benchmark(
                      event_id, created_at, task_type, status, summary,
                      request_json, report_json, failure_reasons_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("event_id"),
                        event.get("created_at"),
                        str(event.get("task_type", "")),
                        str(event.get("status", "")),
                        str(event.get("summary", "")),
                        _json(event.get("request", {})),
                        _json(event.get("result", {})),
                        _json(event.get("failure_reasons", [])),
                    ),
                )
                benchmark_count += 1
                continue
            if str(event.get("task_type", "")).startswith("evolve.observation."):
                connection.execute(
                    """
                    INSERT OR REPLACE INTO evolve_observation(
                      event_id, created_at, observation_type, status, summary,
                      request_json, report_json, failure_reasons_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.get("event_id"),
                        event.get("created_at"),
                        str(event.get("task_type", "")),
                        str(event.get("status", "")),
                        str(event.get("summary", "")),
                        _json(event.get("request", {})),
                        _json(event.get("result", {})),
                        _json(event.get("failure_reasons", [])),
                    ),
                )
                observation_count += 1

        generated_at = kb.now_iso()
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('generated_at', ?)", (generated_at,))
        connection.commit()
    finally:
        connection.close()

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "rebuilt",
        "generated_at": generated_at,
        "database": str(target),
        "fts5": has_fts,
        "entries": entry_count,
        "documents": document_count,
        "candidate_events": event_count,
        "evaluations": evaluation_count,
        "scheduler_benchmarks": benchmark_count,
        "evolve_observations": observation_count,
    }


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _fts_query(query: str) -> str:
    tokens = list(dict.fromkeys(kb.tokenize(query)))
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens[:24])


def _snippet(text: str, query: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    for token in kb.tokenize(query):
        index = compact.lower().find(token.lower())
        if index >= 0:
            start = max(0, index - limit // 3)
            return compact[start : start + limit] + ("…" if start + limit < len(compact) else "")
    return compact[:limit] + ("…" if len(compact) > limit else "")


def _recent_events(connection: sqlite3.Connection, entry_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT event_id, task_type, actor, event_type, status, raw_status, created_at,
               summary, changed_files_json, failure_reasons_json, evaluation_json
        FROM candidate_event
        WHERE entry_id = ?
        ORDER BY COALESCE(created_at, '') DESC, event_id DESC
        LIMIT ?
        """,
        (entry_id, limit),
    ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "task_type": row["task_type"],
            "actor": row["actor"],
            "event_type": row["event_type"],
            "status": row["status"],
            "raw_status": row["raw_status"],
            "created_at": row["created_at"],
            "summary": row["summary"],
            "changed_files": _loads(row["changed_files_json"], []),
            "failure_reasons": _loads(row["failure_reasons_json"], []),
            "evaluation": _loads(row["evaluation_json"], {}),
        }
        for row in rows
    ]


def _recent_scheduler_benchmarks(connection: sqlite3.Connection, limit: int = 5) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT event_id, created_at, task_type, status, summary,
               request_json, report_json, failure_reasons_json
        FROM scheduler_benchmark
        ORDER BY COALESCE(created_at, '') DESC, event_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "task_type": row["task_type"],
            "status": row["status"],
            "summary": row["summary"],
            "request": _loads(row["request_json"], {}),
            "report": _loads(row["report_json"], {}),
            "failure_reasons": _loads(row["failure_reasons_json"], []),
        }
        for row in rows
    ]


def _recent_evolve_observations(connection: sqlite3.Connection, limit: int = 5) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT event_id, created_at, observation_type, status, summary,
               request_json, report_json, failure_reasons_json
        FROM evolve_observation
        ORDER BY COALESCE(created_at, '') DESC, event_id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "created_at": row["created_at"],
            "observation_type": row["observation_type"],
            "status": row["status"],
            "summary": row["summary"],
            "request": _loads(row["request_json"], {}),
            "report": _loads(row["report_json"], {}),
            "failure_reasons": _loads(row["failure_reasons_json"], []),
        }
        for row in rows
    ]


def query(
    root: Path, text: str, *, mode: str = "auto", top_k: int = 5, explicit_db: Path | None = None
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    target = db_path(root, explicit_db)
    if not target.exists():
        rebuild(root, target)
    fts = _fts_query(text)
    connection = connect(target)
    try:
        # A derived database may predate the current additive schema. Ensure
        # missing tables/indexes exist without deleting or rebuilding its data.
        init_schema(connection)
        connection.commit()
        has_fts = connection.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()
        use_fts = bool(fts and has_fts and has_fts["value"] == "1")
        if use_fts:
            rows = connection.execute(
                """
                SELECT d.entry_id, d.kind, d.path, d.title, d.content,
                       bm25(document_fts, 8.0, 1.0, 1.0) AS rank
                FROM document_fts
                JOIN document d
                  ON d.entry_id = document_fts.entry_id AND d.kind = document_fts.kind
                WHERE document_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts, max(top_k * 4, top_k)),
            ).fetchall()
        else:
            pattern = f"%{text}%"
            rows = connection.execute(
                """
                SELECT entry_id, kind, path, title, content, 0.0 AS rank
                FROM document
                WHERE content LIKE ? OR token_text LIKE ?
                LIMIT ?
                """,
                (pattern, pattern, max(top_k * 4, top_k)),
            ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                row["entry_id"],
                {"score": 0.0, "matched_documents": [], "best_title": row["title"]},
            )
            score = 1.0 / (1.0 + max(float(row["rank"] or 0.0), 0.0)) if use_fts else 0.5
            item["score"] += score
            item["matched_documents"].append({
                "kind": row["kind"],
                "path": row["path"],
                "snippet": _snippet(row["content"], text),
            })

        ranked = sorted(grouped.items(), key=lambda pair: (-pair[1]["score"], pair[0]))[:top_k]
        results: list[dict[str, Any]] = []
        for entry_id, match in ranked:
            entry_row = connection.execute("SELECT * FROM entry WHERE id = ?", (entry_id,)).fetchone()
            if not entry_row:
                continue
            eval_row = connection.execute("SELECT * FROM evaluation WHERE entry_id = ?", (entry_id,)).fetchone()
            teaching_row = connection.execute(
                "SELECT * FROM teaching_memory WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            evaluation = {}
            if eval_row:
                evaluation = {
                    "status": eval_row["status"],
                    "generated_at": eval_row["generated_at"],
                    "scores": _loads(eval_row["scores_json"], {}),
                    "summary": _loads(eval_row["summary_json"], {}),
                    "failure_reasons": _loads(eval_row["failure_reasons_json"], []),
                    "warning_reasons": _loads(eval_row["warning_reasons_json"], []),
                    "teacher_review_required": bool(eval_row["teacher_review_required"]),
                }
            results.append({
                "entry_id": entry_id,
                "title": entry_row["title"],
                "subject": entry_row["subject"],
                "status": entry_row["status"],
                "library_folder": entry_row["library_folder"],
                "score": round(float(match["score"]), 4),
                "path": f"entries/{entry_id}",
                "knowledge_points": _loads(entry_row["knowledge_points_json"], []),
                "error_types": _loads(entry_row["error_types_json"], []),
                "teaching_memory": {
                    "difficulty": teaching_row["difficulty"] if teaching_row else "",
                    "methods": _loads(teaching_row["methods_json"], []) if teaching_row else [],
                    "secondary_conclusions": _loads(teaching_row["secondary_conclusions_json"], [])
                    if teaching_row
                    else [],
                    "visualizable": bool(teaching_row["visualizable"]) if teaching_row else False,
                },
                "evaluation": evaluation,
                "recent_events": _recent_events(connection, entry_id),
                "matched_documents": match["matched_documents"][:3],
            })
        scheduler_benchmarks = _recent_scheduler_benchmarks(connection)
        evolve_observations = _recent_evolve_observations(connection)
    finally:
        connection.close()

    required_checks = [
        "Use matched_documents as citations; do not infer facts beyond evidence.",
        "For teaching analysis, combine knowledge_points/error_types with evaluator warnings.",
        "For project audit/evolve tasks, inspect recent_events before proposing repeated changes.",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "query": text,
        "mode": mode,
        "generated_at": kb.now_iso(),
        "database": str(target),
        "results": results,
        "scheduler_benchmarks": scheduler_benchmarks,
        "evolve_observations": evolve_observations,
        "evidence_sources": sorted({
            f"{result['path']}/{doc['path']}" for result in results for doc in result["matched_documents"]
        }),
        "required_checks": required_checks,
        "notes": [
            "SQLite is a derived local index; canonical truth remains Markdown/JSON/JSONL.",
            "Chinese retrieval uses existing kb.tokenize bigrams plus SQLite FTS5 when available.",
        ],
    }


def build_agent_evidence(
    root: Path,
    entry_id: str,
    text: str,
    *,
    task_type: str,
    top_k: int = 3,
    char_budget: int = 8000,
    explicit_db: Path | None = None,
) -> dict[str, Any]:
    """Build a privacy-minimized, read-only evidence pack for one Agent task.

    This deliberately does not rebuild a missing store: task submission must not
    turn into a whole-library write or block because retrieval is unavailable.
    """
    root = root.expanduser().resolve()
    target = db_path(root, explicit_db)
    base = {
        "schema_version": 1,
        "kind": "agent-evidence",
        "task_type": task_type,
        "status": "ready",
        "references": [],
        "instructions": [
            "当前条目中经教师复核的题干与答案始终优先于历史证据。",
            "历史片段只用于核对方法、易错点和适用条件，不得直接复制答案。",
            "不得在输出中暴露证据编号、内部条目、数据库或本地路径。",
        ],
    }
    if not target.is_file():
        return {**base, "status": "unavailable", "reason": "knowledge-store-missing"}
    try:
        retrieved = query(root, text, mode="teaching", top_k=max(1, top_k + 2), explicit_db=target)
    except (OSError, sqlite3.Error, ValueError):
        return {**base, "status": "unavailable", "reason": "knowledge-store-query-failed"}

    references: list[dict[str, Any]] = []
    budget = max(1000, min(int(char_budget), 20000))
    for result in retrieved.get("results", []):
        if result.get("entry_id") == entry_id:
            continue
        recent_lessons = []
        for event in result.get("recent_events", [])[:3]:
            lesson = {
                "task_type": str(event.get("task_type", "")),
                "status": str(event.get("status", "")),
                "summary": str(event.get("summary", ""))[:240],
                "failure_reasons": [str(item)[:240] for item in event.get("failure_reasons", [])[:3]],
            }
            if any(value for value in lesson.values()):
                recent_lessons.append(lesson)
        reference = {
            "reference": f"similar-{len(references) + 1}",
            "title": str(result.get("title", "相似题"))[:120],
            "knowledge_points": [str(item)[:80] for item in result.get("knowledge_points", [])[:8]],
            "error_types": [str(item)[:80] for item in result.get("error_types", [])[:8]],
            "methods": [str(item)[:160] for item in result.get("teaching_memory", {}).get("methods", [])[:6]],
            "secondary_conclusions": [
                str(item)[:200] for item in result.get("teaching_memory", {}).get("secondary_conclusions", [])[:5]
            ],
            "evaluator_warnings": [
                str(item)[:240] for item in result.get("evaluation", {}).get("warning_reasons", [])[:4]
            ],
            "evaluator_failures": [
                str(item)[:240] for item in result.get("evaluation", {}).get("failure_reasons", [])[:4]
            ],
            "matched_evidence": [
                {"kind": str(doc.get("kind", "")), "snippet": str(doc.get("snippet", ""))[:240]}
                for doc in result.get("matched_documents", [])[:3]
            ],
            "recent_lessons": recent_lessons,
        }
        candidate = {**base, "references": [*references, reference]}
        if len(json.dumps(candidate, ensure_ascii=False)) > budget:
            break
        references.append(reference)
        if len(references) >= top_k:
            break
    return {**base, "references": references}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    parser.add_argument("--db", type=Path, help="Override database path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rebuild", help="Rebuild the derived SQLite store")
    query_parser = commands.add_parser("query", help="Return a JSON evidence pack for RAG/AI audit")
    query_parser.add_argument("text")
    query_parser.add_argument("--mode", choices=("auto", "teaching", "audit"), default="auto")
    query_parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    root = args.library.expanduser().resolve()
    if args.command == "rebuild":
        kb.print_json(rebuild(root, args.db))
    elif args.command == "query":
        kb.print_json(query(root, args.text, mode=args.mode, top_k=args.top_k, explicit_db=args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
