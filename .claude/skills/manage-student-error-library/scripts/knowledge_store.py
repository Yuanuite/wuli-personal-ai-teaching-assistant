#!/usr/bin/env python3
"""Local derived SQLite knowledge store for Wuli.

The store is a rebuildable retrieval and evidence layer.  Canonical truth stays
in entry Markdown/JSON files, ``evaluation.json``, and ``candidate-archive.jsonl``.
Deleting ``indexes/wuli-memory.db`` must never lose teaching data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import candidate_archive
import kb

PROJECT_ROOT = Path(__file__).resolve().parents[4]
TEACHER_CONSOLE = PROJECT_ROOT / "teacher-console"
if str(TEACHER_CONSOLE) not in sys.path:
    sys.path.insert(0, str(TEACHER_CONSOLE))

import evidence_contract  # noqa: E402

SCHEMA_VERSION = 2
EVIDENCE_UNIT_PROJECTION_VERSION = "wuli-evidence-unit-shadow-v1"
DEFAULT_DB_RELATIVE = Path("indexes") / "wuli-memory.db"
DIRTY_MARKER_RELATIVE = Path("indexes") / "wuli-memory.dirty.json"
CURATED_TECHNIQUES_PATH = (
    PROJECT_ROOT
    / ".claude"
    / "skills"
    / "build-physics-simulator"
    / "references"
    / "secondary-conclusions.json"
)
DOCUMENT_KINDS = (
    "metadata",
    "problem",
    "solution",
    "student_solution",
    "teacher_solution",
    "source_review",
    "physics_model",
)
TEACHING_QUERY_EXPANSIONS = (
    ("多阶段", ("分段运动", "多过程", "多区域", "过程衔接", "多过程计时")),
    ("方向性", ("方向判断", "符号方向", "空间想象")),
    ("粒子", ("带电粒子",)),
)
TEACHING_QUERY_NOISE = (
    "帮我找一道",
    "检索关于",
    "我想看一些",
    "想找一些",
    "找一些",
    "容易错的题",
    "容易出现",
    "相关的题目",
    "相关的题",
    "相关题目",
    "相关",
    "有哪些",
    "检索",
    "关于",
    "题目",
)
TEACHING_INTENT_EXPANSIONS = (
    ("反复进出", ("多区域", "周期轨迹", "圆形有界磁场", "轨迹衔接")),
)
RRF_K = 60
RETRIEVAL_ROUTES = (
    {
        "id": "metadata",
        "label": "标签与教学元数据",
        "kinds": ("metadata",),
        "weight": 1.0,
    },
    {
        "id": "problem",
        "label": "题干与情境",
        "kinds": ("problem", "source_review"),
        "weight": 1.0,
    },
    {
        "id": "solution",
        "label": "解析与方法",
        "kinds": ("solution", "student_solution", "teacher_solution", "physics_model"),
        "weight": 1.0,
    },
)
EVIDENCE_FACETS = {
    "domain": (
        ("charged-particle", ("带电粒子", "粒子", "洛伦兹力", "回旋", "磁偏转")),
        ("circuit-induction", ("线框", "导线框", "电路", "电动势", "端电压", "电磁感应")),
        ("conductor-force", ("安培力", "导体棒", "导体轨道")),
        ("mechanics", ("碰撞", "动量", "机械能", "斜面", "抛体")),
    ),
    "field-sequence": (
        (
            "alternating-electric-magnetic",
            ("交替电场", "交变电场", "电场与磁场交替", "周期电场", "方波电场"),
        ),
        (
            "magnetic-only",
            ("只有磁场", "匀强磁场", "磁场中运动", "分区磁场", "内外反向", "同向分区"),
        ),
        ("electric-only", ("只有电场", "匀强电场", "电场中运动")),
    ),
    "geometry": (
        ("multi-region", ("多区域", "三区域", "分区磁场", "复合场", "两磁场的边界")),
        ("circular-boundary", ("圆形磁场", "圆形有界磁场", "圆形边界", "圆形区域")),
        ("linear-boundary", ("直线边界", "半平面", "宽磁场", "两磁场的边界")),
    ),
    "target": (
        ("average-velocity", ("平均速度",)),
        ("encounter-time", ("相遇", "首次相遇")),
        ("trajectory", ("轨迹", "运动范围", "偏转")),
        ("work-energy", ("做功", "动能", "能量")),
        ("voltage-emf", ("端电压", "感应电动势")),
    ),
}
HARD_CONFLICT_FACETS = {"domain", "geometry", "target"}
EVIDENCE_AUDIT_STOP_TOKENS = {
    "assets",
    "asset",
    "svg",
    "png",
    "jpg",
    "jpeg",
    "解析",
    "学生版",
    "详细解答",
    "答案速览",
}


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


def connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


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
          feedback_json TEXT NOT NULL DEFAULT '{}',
          links_json TEXT NOT NULL DEFAULT '{}',
          result_json TEXT NOT NULL DEFAULT '{}',
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
        CREATE TABLE IF NOT EXISTS evidence_unit (
          evidence_id TEXT PRIMARY KEY,
          entry_id TEXT,
          unit_kind TEXT NOT NULL,
          source_kind TEXT NOT NULL,
          source_path TEXT NOT NULL,
          source_section TEXT NOT NULL,
          start_line INTEGER NOT NULL,
          end_line INTEGER NOT NULL,
          text TEXT NOT NULL,
          physics_facets_json TEXT NOT NULL,
          applicability_json TEXT NOT NULL,
          exceptions_json TEXT NOT NULL,
          authority_level TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          projection_version TEXT NOT NULL,
          FOREIGN KEY (entry_id) REFERENCES entry(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_entry_status ON entry(status);
        CREATE INDEX IF NOT EXISTS idx_entry_subject ON entry(subject);
        CREATE INDEX IF NOT EXISTS idx_event_entry_time ON candidate_event(entry_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_scheduler_benchmark_time ON scheduler_benchmark(created_at);
        CREATE INDEX IF NOT EXISTS idx_evolve_observation_time ON evolve_observation(created_at);
        CREATE INDEX IF NOT EXISTS idx_evidence_unit_entry ON evidence_unit(entry_id);
        CREATE INDEX IF NOT EXISTS idx_evidence_unit_kind ON evidence_unit(unit_kind);
        CREATE INDEX IF NOT EXISTS idx_evidence_unit_content_hash ON evidence_unit(content_hash);
        """
    )
    _ensure_column(connection, "candidate_event", "feedback_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "candidate_event", "links_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "candidate_event", "result_json", "TEXT NOT NULL DEFAULT '{}'")
    if has_fts:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS document_fts
            USING fts5(entry_id UNINDEXED, kind UNINDEXED, title, content, token_text)
            """
        )
    connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts5', ?)", ("1" if has_fts else "0",))
    connection.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('evidence_unit_projection_version', ?)",
        (EVIDENCE_UNIT_PROJECTION_VERSION,),
    )
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
    physics_model = kb.load_json(entry / "physics-model.json", {}) or {}
    model_teaching = physics_model.get("teaching", {}) if isinstance(physics_model, dict) else {}
    metadata_sections = (
        ("知识点", record.get("knowledge_points", [])),
        ("错因", record.get("error_types", [])),
        ("方法", record.get("methods", model_teaching.get("methods", []))),
        (
            "二级结论",
            record.get("secondary_conclusions", model_teaching.get("secondary_conclusions", [])),
        ),
    )
    metadata_lines = [
        f"{label}：" + "；".join(str(item) for item in values if str(item).strip())
        for label, values in metadata_sections
        if isinstance(values, list) and any(str(item).strip() for item in values)
    ]
    payloads: list[dict[str, str]] = []
    if metadata_lines:
        metadata_content = "\n".join(metadata_lines)
        payloads.append({
            "entry_id": entry.name,
            "kind": "metadata",
            "path": "record.json",
            "title": title,
            "content": metadata_content,
            "token_text": " ".join(kb.tokenize(" ".join([title, metadata_content]))),
            "updated_at": record.get("updated_at") or "",
        })
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


def _approved_answer_is_current(entry: Path, record: dict[str, Any]) -> bool:
    review = record.get("answer_review")
    if not isinstance(review, dict):
        review = kb.load_json(entry / "answer-review.json", {}) or {}
    if review.get("status") != "passed":
        return False
    expected = str(review.get("answer_digest", "")).strip()
    return bool(expected and expected == kb.answer_artifact_digest(entry))


def _approved_model_is_current(entry: Path, record: dict[str, Any]) -> bool:
    review = record.get("visualization_review")
    if not isinstance(review, dict):
        review = kb.load_json(entry / "visualization-review.json", {}) or {}
    model_path = entry / "physics-model.json"
    if review.get("status") != "passed" or not model_path.exists():
        return False
    expected = str(review.get("model_digest", "")).strip()
    return bool(expected and expected == kb.sha256_file(model_path))


def _evidence_id(locator_key: str) -> str:
    digest = hashlib.sha256(locator_key.encode("utf-8")).hexdigest()[:24]
    return f"EU-{digest}"


def _make_evidence_unit(
    *,
    locator_key: str,
    unit_kind: str,
    source_kind: str,
    source_path: str,
    source_section: str,
    start_line: int,
    end_line: int,
    text: str,
    physics_facets: list[str],
    applicability: list[str],
    exceptions: list[str],
    authority_level: str,
) -> dict[str, Any]:
    return evidence_contract.normalize_evidence_unit(
        {
            "schema": evidence_contract.EVIDENCE_UNIT_SCHEMA,
            "evidence_id": _evidence_id(locator_key),
            "unit_kind": unit_kind,
            "source_kind": source_kind,
            "source_locator": {
                "path": source_path,
                "section": source_section,
                "start_line": start_line,
                "end_line": end_line,
            },
            "text": text,
            "physics_facets": physics_facets,
            "applicability": applicability,
            "exceptions": exceptions,
            "authority_level": authority_level,
        }
    )


def _markdown_section_bullets(
    path: Path, section_name: str
) -> list[tuple[int, int, str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    in_section = False
    bullets: list[tuple[int, int, str]] = []
    current_start = 0
    current_lines: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal current_start, current_lines
        text = " ".join(part.strip() for part in current_lines if part.strip()).strip()
        if text:
            bullets.append((current_start, max(current_start, end_line), text))
        current_start = 0
        current_lines = []

    for line_number, line in enumerate(lines, 1):
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            if in_section:
                flush(line_number - 1)
                break
            in_section = heading.group(1).strip() == section_name
            continue
        if not in_section:
            continue
        bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet:
            flush(line_number - 1)
            current_start = line_number
            current_lines = [bullet.group(1)]
        elif current_lines and line.strip():
            current_lines.append(line.strip())
        elif current_lines:
            flush(line_number - 1)
        elif line.strip() and not line.lstrip().startswith(("![", "---")):
            current_start = line_number
            current_lines = [line.strip()]
    if in_section:
        flush(len(lines))
    return bullets


def _record_facets(record: dict[str, Any]) -> list[str]:
    facets = [
        str(item).strip()
        for item in record.get("knowledge_points", [])
        if str(item).strip()
    ]
    title = str(record.get("title", "")).strip()
    if title and title not in facets:
        facets.append(title)
    return facets[:16] or ["高中物理"]


def _structured_units(
    *,
    entry: Path,
    record: dict[str, Any],
    values: Any,
    field: str,
    unit_kind: str,
    source_kind: str,
    source_path: str,
    authority_level: str,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    units: list[dict[str, Any]] = []
    for index, raw in enumerate(values):
        if not isinstance(raw, dict):
            continue
        text = str(
            raw.get("text")
            or raw.get("conclusion")
            or raw.get("method")
            or ""
        ).strip()
        applicability = raw.get("applicability", raw.get("conditions", []))
        exceptions = raw.get("exceptions", raw.get("forbidden", []))
        facets = raw.get("physics_facets", raw.get("triggers", []))
        if not text or not isinstance(applicability, list) or not applicability:
            continue
        normalized_facets = [
            str(item).strip() for item in facets if str(item).strip()
        ] if isinstance(facets, list) else []
        units.append(
            _make_evidence_unit(
                locator_key=f"{entry.name}:{source_path}:{field}:{index}",
                unit_kind=unit_kind,
                source_kind=source_kind,
                source_path=f"entries/{entry.name}/{source_path}",
                source_section=field,
                start_line=1,
                end_line=1,
                text=text,
                physics_facets=normalized_facets or _record_facets(record),
                applicability=[
                    str(item).strip() for item in applicability if str(item).strip()
                ],
                exceptions=[
                    str(item).strip()
                    for item in exceptions
                    if str(item).strip()
                ] if isinstance(exceptions, list) else [],
                authority_level=authority_level,
            )
        )
    return units


def _entry_evidence_units(entry: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
    if not _approved_answer_is_current(entry, record):
        return []
    units: list[dict[str, Any]] = []
    facets = _record_facets(record)
    title = str(record.get("title", entry.name)).strip()
    applicability = [
        "来源条目的答案摘要已由教师批准",
        f"迁移前必须重新核对当前题是否满足“{title}”中的对应物理条件",
    ]
    solution_path = entry / "teacher-solution.md"
    if not solution_path.exists():
        solution_path = entry / "solution.md"
    source_name = solution_path.name

    recognition_bullets = _markdown_section_bullets(solution_path, "一眼识别")
    recognition_context = next(
        (
            re.sub(r"^题型识别[：:]\s*", "", text).strip()
            for _, _, text in recognition_bullets
            if re.match(r"^题型识别[：:]", text)
        ),
        "",
    )
    for start_line, end_line, text in recognition_bullets:
        if re.match(r"^最短主线[：:]", text):
            method_text = re.sub(r"^最短主线[：:]\s*", "", text).strip()
            if method_text:
                units.append(
                    _make_evidence_unit(
                        locator_key=f"{entry.name}:{source_name}:一眼识别:method:{start_line}",
                        unit_kind="method_applicability",
                        source_kind="approved_solution",
                        source_path=f"entries/{entry.name}/{source_name}",
                        source_section="一眼识别/最短主线",
                        start_line=start_line,
                        end_line=end_line,
                        text=method_text,
                        physics_facets=facets,
                        applicability=[
                            *(
                                [f"题型识别：{recognition_context}"]
                                if recognition_context
                                else []
                            ),
                            *applicability,
                        ],
                        exceptions=["只能迁移方法结构，不能复制来源题的数值或最终结论"],
                        authority_level="B",
                    )
                )
            continue
        if re.match(r"^可用二级结论[：:]", text):
            raw_conclusion = re.sub(r"^可用二级结论[：:]\s*", "", text).strip()
            condition_match = re.search(
                r"(?:\*\*)?适用条件(?:\*\*)?[：:]\s*(.+)$",
                raw_conclusion,
            )
            if not condition_match:
                continue
            conclusion = raw_conclusion[: condition_match.start()].strip("；;。 ")
            condition = condition_match.group(1).strip()
            if conclusion and condition:
                units.append(
                    _make_evidence_unit(
                        locator_key=f"{entry.name}:{source_name}:一眼识别:conclusion:{start_line}",
                        unit_kind="secondary_conclusion",
                        source_kind="approved_solution",
                        source_path=f"entries/{entry.name}/{source_name}",
                        source_section="一眼识别/可用二级结论",
                        start_line=start_line,
                        end_line=end_line,
                        text=conclusion,
                        physics_facets=facets,
                        applicability=[condition],
                        exceptions=["若当前题条件不同，不得直接套用该二级结论"],
                        authority_level="B",
                    )
                )

    for start_line, end_line, text in _markdown_section_bullets(
        solution_path, "易错点"
    ):
        error_match = re.search(
            r"(?:\*\*)?错误表现(?:\*\*)?[：:]\s*(.+?)(?=(?:\*\*)?纠正策略|$)",
            text,
        )
        exceptions = [
            error_match.group(1).strip("。；; ")
        ] if error_match else ["不得把来源题的结论脱离条件直接迁移到当前题"]
        units.append(
            _make_evidence_unit(
                locator_key=f"{entry.name}:{source_name}:易错点:{start_line}",
                unit_kind="false_friend_warning",
                source_kind="approved_solution",
                source_path=f"entries/{entry.name}/{source_name}",
                source_section="易错点",
                start_line=start_line,
                end_line=end_line,
                text=text,
                physics_facets=facets,
                applicability=applicability,
                exceptions=exceptions,
                authority_level="B",
            )
        )

    for start_line, end_line, text in _markdown_section_bullets(
        solution_path, "教师审计"
    ):
        units.append(
            _make_evidence_unit(
                locator_key=f"{entry.name}:{source_name}:教师审计:{start_line}",
                unit_kind="verification_rule",
                source_kind="approved_solution",
                source_path=f"entries/{entry.name}/{source_name}",
                source_section="教师审计",
                start_line=start_line,
                end_line=end_line,
                text=text,
                physics_facets=facets,
                applicability=applicability,
                exceptions=["迁移后必须由当前题 W3 verifier 重新执行，不得直接晋升为 Claim"],
                authority_level="B",
            )
        )

    units.extend(
        _structured_units(
            entry=entry,
            record=record,
            values=record.get("methods", []),
            field="methods",
            unit_kind="method_applicability",
            source_kind="approved_solution",
            source_path="record.json",
            authority_level="B",
        )
    )
    units.extend(
        _structured_units(
            entry=entry,
            record=record,
            values=record.get("secondary_conclusions", []),
            field="secondary_conclusions",
            unit_kind="secondary_conclusion",
            source_kind="approved_solution",
            source_path="record.json",
            authority_level="B",
        )
    )

    if _approved_model_is_current(entry, record):
        model = kb.load_json(entry / "physics-model.json", {}) or {}
        teaching = model.get("teaching", {}) if isinstance(model, dict) else {}
        if isinstance(teaching, dict):
            units.extend(
                _structured_units(
                    entry=entry,
                    record=record,
                    values=teaching.get("methods", []),
                    field="teaching.methods",
                    unit_kind="method_applicability",
                    source_kind="approved_physics_model",
                    source_path="physics-model.json",
                    authority_level="B",
                )
            )
            units.extend(
                _structured_units(
                    entry=entry,
                    record=record,
                    values=teaching.get("secondary_conclusions", []),
                    field="teaching.secondary_conclusions",
                    unit_kind="secondary_conclusion",
                    source_kind="approved_physics_model",
                    source_path="physics-model.json",
                    authority_level="B",
                )
            )
    return units


def _curated_evidence_units() -> list[dict[str, Any]]:
    if not CURATED_TECHNIQUES_PATH.exists():
        return []
    raw_text = CURATED_TECHNIQUES_PATH.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        return []
    id_lines: dict[str, int] = {}
    for line_number, line in enumerate(raw_text.splitlines(), 1):
        match = re.search(r'"id"\s*:\s*"([^"]+)"', line)
        if match:
            id_lines[match.group(1)] = line_number
    source_path = str(CURATED_TECHNIQUES_PATH.relative_to(PROJECT_ROOT))
    units: list[dict[str, Any]] = []
    for raw in payload:
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("id", "")).strip()
        conclusion = str(raw.get("conclusion", "")).strip()
        conditions = raw.get("conditions", [])
        if not source_id or not conclusion or not isinstance(conditions, list) or not conditions:
            continue
        line_number = id_lines.get(source_id, 1)
        module = str(raw.get("module", "")).strip()
        unit_kind = (
            "verification_rule"
            if module in {"通用检验", "审题"} or source_id.endswith("-check")
            else "secondary_conclusion"
        )
        triggers = raw.get("triggers", [])
        facets = [module] if module else []
        if isinstance(triggers, list):
            facets.extend(str(item).strip() for item in triggers if str(item).strip())
        units.append(
            _make_evidence_unit(
                locator_key=f"curated-technique:{source_id}",
                unit_kind=unit_kind,
                source_kind="curated_technique",
                source_path=source_path,
                source_section=source_id,
                start_line=line_number,
                end_line=line_number,
                text=conclusion,
                physics_facets=facets,
                applicability=[
                    str(item).strip() for item in conditions if str(item).strip()
                ],
                exceptions=[
                    str(item).strip()
                    for item in raw.get("forbidden", [])
                    if str(item).strip()
                ] if isinstance(raw.get("forbidden", []), list) else [],
                authority_level="A",
            )
        )
    return units


def _insert_evidence_unit(
    connection: sqlite3.Connection,
    unit: dict[str, Any],
    *,
    entry_id: str | None,
) -> None:
    locator = unit["source_locator"]
    connection.execute(
        """
        INSERT INTO evidence_unit(
          evidence_id, entry_id, unit_kind, source_kind, source_path,
          source_section, start_line, end_line, text, physics_facets_json,
          applicability_json, exceptions_json, authority_level, content_hash,
          projection_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit["evidence_id"],
            entry_id,
            unit["unit_kind"],
            unit["source_kind"],
            locator["path"],
            locator["section"],
            locator["start_line"],
            locator["end_line"],
            unit["text"],
            _json(unit["physics_facets"]),
            _json(unit["applicability"]),
            _json(unit["exceptions"]),
            unit["authority_level"],
            unit["content_hash"],
            EVIDENCE_UNIT_PROJECTION_VERSION,
        ),
    )


def rebuild(root: Path, explicit_db: Path | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve()
    target = db_path(root, explicit_db)
    connection = connect(target)
    try:
        has_fts = init_schema(connection)
        connection.execute("DELETE FROM evidence_unit")
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
        evidence_unit_count = 0
        evidence_unit_projection_errors = 0
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
                      created_at, summary, changed_files_json, failure_reasons_json, evaluation_json,
                      feedback_json, links_json, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        _json(event.get("feedback", {})),
                        _json(event.get("links", {})),
                        _json(event.get("result", {})),
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
            try:
                entry_units = _entry_evidence_units(entry, record)
            except (ValueError, OSError, json.JSONDecodeError):
                entry_units = []
                evidence_unit_projection_errors += 1
            for unit in entry_units:
                try:
                    _insert_evidence_unit(connection, unit, entry_id=entry.name)
                except (ValueError, sqlite3.DatabaseError):
                    evidence_unit_projection_errors += 1
                    continue
                evidence_unit_count += 1

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

        try:
            curated_units = _curated_evidence_units()
        except (ValueError, OSError, json.JSONDecodeError):
            curated_units = []
            evidence_unit_projection_errors += 1
        for unit in curated_units:
            try:
                _insert_evidence_unit(connection, unit, entry_id=None)
            except (ValueError, sqlite3.DatabaseError):
                evidence_unit_projection_errors += 1
                continue
            evidence_unit_count += 1

        generated_at = kb.now_iso()
        library_events = candidate_archive.read_library_events(root)
        last_event_id = str(library_events[-1].get("event_id", "")) if library_events else ""
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('generated_at', ?)", (generated_at,))
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('last_archive_event_id', ?)", (last_event_id,))
        connection.commit()
    finally:
        connection.close()
    (root / DIRTY_MARKER_RELATIVE).unlink(missing_ok=True)

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
        "evidence_units": evidence_unit_count,
        "evidence_unit_projection_version": EVIDENCE_UNIT_PROJECTION_VERSION,
        "evidence_unit_projection_errors": evidence_unit_projection_errors,
    }


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def load_evidence_unit_projection(
    root: Path,
    *,
    exclude_entry_id: str = "",
    unit_kinds: tuple[str, ...] = (),
    limit: int = 500,
    explicit_db: Path | None = None,
) -> dict[str, Any]:
    """Read the Phase-B shadow projection without affecting production query()."""
    root = root.expanduser().resolve()
    target = db_path(root, explicit_db)
    if not target.exists():
        return {
            "status": "unavailable",
            "reason": "knowledge-store-missing",
            "units": [],
        }
    if (root / DIRTY_MARKER_RELATIVE).exists():
        return {
            "status": "unavailable",
            "reason": "knowledge-store-stale",
            "units": [],
        }
    normalized_kinds = tuple(
        kind for kind in unit_kinds if kind in evidence_contract.UNIT_KINDS
    )
    if len(normalized_kinds) != len(unit_kinds):
        return {
            "status": "unavailable",
            "reason": "invalid-unit-kind",
            "units": [],
        }
    bounded_limit = max(1, min(int(limit), 2000))
    connection = connect_readonly(target)
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "evidence_unit" not in tables:
            return {
                "status": "unavailable",
                "reason": "evidence-unit-projection-missing",
                "units": [],
            }
        version_row = connection.execute(
            "SELECT value FROM meta WHERE key='evidence_unit_projection_version'"
        ).fetchone()
        if not version_row or version_row["value"] != EVIDENCE_UNIT_PROJECTION_VERSION:
            return {
                "status": "unavailable",
                "reason": "evidence-unit-projection-version-mismatch",
                "units": [],
            }
        clauses: list[str] = []
        parameters: list[Any] = []
        if exclude_entry_id:
            clauses.append("(entry_id IS NULL OR entry_id != ?)")
            parameters.append(exclude_entry_id)
        if normalized_kinds:
            placeholders = ", ".join("?" for _ in normalized_kinds)
            clauses.append(f"unit_kind IN ({placeholders})")
            parameters.extend(normalized_kinds)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(bounded_limit)
        rows = connection.execute(
            f"""
            SELECT evidence_id, unit_kind, source_kind, source_path,
                   source_section, start_line, end_line, text,
                   physics_facets_json, applicability_json, exceptions_json,
                   authority_level, content_hash
            FROM evidence_unit
            {where}
            ORDER BY
              CASE authority_level
                WHEN 'A' THEN 4 WHEN 'B' THEN 3 WHEN 'C' THEN 2
                WHEN 'D' THEN 1 ELSE 0
              END DESC,
              evidence_id
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    finally:
        connection.close()

    units = [
        evidence_contract.normalize_evidence_unit(
            {
                "schema": evidence_contract.EVIDENCE_UNIT_SCHEMA,
                "evidence_id": row["evidence_id"],
                "unit_kind": row["unit_kind"],
                "source_kind": row["source_kind"],
                "source_locator": {
                    "path": row["source_path"],
                    "section": row["source_section"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                },
                "text": row["text"],
                "physics_facets": _loads(row["physics_facets_json"], []),
                "applicability": _loads(row["applicability_json"], []),
                "exceptions": _loads(row["exceptions_json"], []),
                "authority_level": row["authority_level"],
                "content_hash": row["content_hash"],
            }
        )
        for row in rows
    ]
    return {
        "status": "ready",
        "projection_version": EVIDENCE_UNIT_PROJECTION_VERSION,
        "unit_count": len(units),
        "excluded_current_entry": bool(exclude_entry_id),
        "units": units,
    }


def _fts_query(query: str) -> str:
    tokens = list(dict.fromkeys(kb.tokenize(query)))
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"' for token in tokens[:24])


def _expanded_teaching_query(query: str) -> tuple[str, list[str]]:
    """Expand common teacher phrasing into canonical library vocabulary."""
    normalized = query.strip()
    additions: list[str] = []
    for trigger, aliases in TEACHING_QUERY_EXPANSIONS:
        if trigger in query:
            additions.extend(alias for alias in aliases if alias not in normalized and alias not in additions)
    return " ".join([normalized, *additions]).strip(), additions


def _teaching_intent_query(query: str) -> tuple[str, list[str], list[str]]:
    """Remove task phrasing and add only reviewed, deterministic physics aliases."""
    normalized = query
    removed: list[str] = []
    for phrase in TEACHING_QUERY_NOISE:
        if phrase in normalized:
            normalized = normalized.replace(phrase, " ")
            removed.append(phrase)
    additions: list[str] = []
    for trigger, aliases in TEACHING_QUERY_EXPANSIONS:
        if trigger in query:
            additions.extend(alias for alias in aliases if alias not in normalized and alias not in additions)
    for trigger, aliases in TEACHING_INTENT_EXPANSIONS:
        if trigger in query:
            additions.extend(alias for alias in aliases if alias not in normalized and alias not in additions)
    normalized = " ".join(normalized.split()).strip() or query.strip()
    return " ".join([normalized, *additions]).strip(), removed, additions


def _snippet(text: str, query: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    for token in kb.tokenize(query):
        index = compact.lower().find(token.lower())
        if index >= 0:
            start = max(0, index - limit // 3)
            return compact[start : start + limit] + ("…" if start + limit < len(compact) else "")
    return compact[:limit] + ("…" if len(compact) > limit else "")


def _query_plan(
    text: str,
    retrieval_text: str,
    query_expansions: list[str],
    intent_text: str,
    removed_phrases: list[str],
    intent_expansions: list[str],
    mode: str,
) -> dict[str, Any]:
    """Return the deterministic, auditable query plan used by every route."""
    return {
        "raw_query": text,
        "retrieval_text": retrieval_text,
        "intent_query": intent_text,
        "mode": mode,
        "expansions": query_expansions,
        "removed_task_phrases": removed_phrases,
        "intent_expansions": intent_expansions,
        "tokens": list(dict.fromkeys(kb.tokenize(retrieval_text)))[:24],
        "routes": [
            {
                "id": str(route["id"]),
                "label": str(route["label"]),
                "document_kinds": list(route["kinds"]),
                "weight": float(route["weight"]),
            }
            for route in RETRIEVAL_ROUTES
        ],
    }


def _evidence_coverage(
    route_matches: list[dict[str, Any]], matched_documents: list[dict[str, Any]]
) -> dict[str, Any]:
    slot_by_route = {
        "metadata": "concepts-and-labels",
        "problem": "problem-context",
        "solution": "solution-method",
    }
    required = list(slot_by_route.values())
    routes = {
        str(item.get("route", ""))
        for item in [*route_matches, *matched_documents]
    }
    covered = [slot for route, slot in slot_by_route.items() if route in routes]
    return {
        "required_slots": required,
        "covered_slots": covered,
        "missing_slots": [slot for slot in required if slot not in covered],
        "coverage_ratio": round(len(covered) / len(required), 4),
        "route_diversity": len(routes & set(slot_by_route)),
    }


def _evidence_set_diagnostics(results: list[dict[str, Any]]) -> dict[str, Any]:
    required = ["concepts-and-labels", "problem-context", "solution-method"]
    covered = {
        slot
        for result in results
        for slot in result.get("evidence_coverage", {}).get("covered_slots", [])
    }
    documents = [document for result in results for document in result.get("matched_documents", [])]
    fingerprints = {
        (str(document.get("kind", "")), str(document.get("snippet", "")).strip())
        for document in documents
    }
    return {
        "candidate_entry_count": len(results),
        "document_count": len(documents),
        "unique_document_count": len(fingerprints),
        "exact_duplicate_count": max(0, len(documents) - len(fingerprints)),
        "required_slots": required,
        "covered_slots": [slot for slot in required if slot in covered],
        "missing_slots": [slot for slot in required if slot not in covered],
        "coverage_ratio": round(len(covered) / len(required), 4),
        "traceable_document_count": sum(
            1 for document in documents if document.get("path") and document.get("kind")
        ),
        "precision_audit": {
            "policy": "deterministic-condition-audit-v1",
            "accepted_count": sum(
                1
                for result in results
                if result.get("evidence_audit", {}).get("decision") == "accepted"
            ),
            "rejected_count": sum(
                1
                for result in results
                if result.get("evidence_audit", {}).get("decision") != "accepted"
            ),
        },
    }


def _reference_set_diagnostics(references: list[dict[str, Any]]) -> dict[str, Any]:
    required = ["concepts-and-labels", "problem-context", "solution-method"]
    covered = {
        slot
        for reference in references
        for slot in reference.get("coverage", {}).get("covered_slots", [])
    }
    return {
        "kind": "candidate-evidence-set",
        "reference_count": len(references),
        "required_slots": required,
        "covered_slots": [slot for slot in required if slot in covered],
        "missing_slots": [slot for slot in required if slot not in covered],
        "coverage_ratio": round(len(covered) / len(required), 4),
    }


def _condition_profile(text: str) -> dict[str, list[str]]:
    compact = " ".join(str(text).split())
    return {
        facet: [
            label
            for label, phrases in options
            if any(phrase in compact for phrase in phrases)
        ]
        for facet, options in EVIDENCE_FACETS.items()
    }


def _evidence_relevance_audit(
    query_plan: dict[str, Any],
    *,
    title: str,
    knowledge_points: list[str],
    error_types: list[str],
    matched_documents: list[dict[str, Any]],
    route_matches: list[dict[str, Any]],
) -> dict[str, Any]:
    """Explain why one result matches and conservatively flag incompatible evidence."""
    query_text = str(query_plan.get("retrieval_text", ""))
    candidate_text = " ".join([
        title,
        *knowledge_points,
        *error_types,
        *(str(document.get("snippet", "")) for document in matched_documents),
    ])
    query_tokens = list(dict.fromkeys(
        str(item).strip()
        for item in query_plan.get("tokens", [])
        if len(str(item).strip()) >= 2
        and str(item).strip().lower() not in EVIDENCE_AUDIT_STOP_TOKENS
    ))
    candidate_tokens = set(kb.tokenize(candidate_text))
    shared_terms = [token for token in query_tokens if token in candidate_tokens]
    token_coverage = len(shared_terms) / len(query_tokens) if query_tokens else 0.0
    title_tokens = set(kb.tokenize(title))
    title_coverage = (
        len(set(query_tokens) & title_tokens) / len(query_tokens)
        if query_tokens
        else 0.0
    )
    query_profile = _condition_profile(query_text)
    candidate_profile = _condition_profile(candidate_text)
    shared_conditions: list[str] = []
    applicability_conditions: list[str] = []
    conflict_conditions: list[str] = []
    hard_conflict = False
    query_condition_count = 0
    for facet in EVIDENCE_FACETS:
        expected = set(query_profile.get(facet, []))
        observed = set(candidate_profile.get(facet, []))
        query_condition_count += len(expected)
        shared = sorted(expected & observed)
        shared_conditions.extend(f"{facet}:{label}" for label in shared)
        applicability_conditions.extend(
            f"{facet} 同为 {label}" for label in shared
        )
        if expected and observed and not shared:
            conflict_conditions.append(
                f"{facet} 不相容：目标题={','.join(sorted(expected))}；"
                f"历史证据={','.join(sorted(observed))}"
            )
            if facet in HARD_CONFLICT_FACETS:
                hard_conflict = True
    condition_coverage = (
        len(shared_conditions) / query_condition_count
        if query_condition_count
        else 0.0
    )
    route_diversity = len({
        str(item.get("route", ""))
        for item in route_matches
        if str(item.get("route", ""))
    })
    precision_score = round(
        min(
            1.0,
            0.55 * token_coverage
            + 0.25 * condition_coverage
            + 0.10 * min(route_diversity / 3.0, 1.0)
            + 0.10 * title_coverage,
        ),
        4,
    )
    accepted = not hard_conflict and (
        precision_score >= 0.30
        or (bool(shared_conditions) and token_coverage >= 0.12)
        or (len(shared_conditions) >= 2 and condition_coverage >= 0.50)
        or (route_diversity >= 3 and token_coverage >= 0.20)
    )
    if not applicability_conditions:
        applicability_conditions.append("未识别出可审计的共同物理条件")
    return {
        "policy": "deterministic-condition-audit-v1",
        "match_basis": {
            "shared_terms": shared_terms[:12],
            "shared_conditions": shared_conditions,
            "route_ids": sorted({
                str(item.get("route", ""))
                for item in route_matches
                if str(item.get("route", ""))
            }),
            "token_coverage": round(token_coverage, 4),
            "condition_coverage": round(condition_coverage, 4),
        },
        "applicability_conditions": applicability_conditions,
        "conflict_conditions": conflict_conditions,
        "condition_profile": candidate_profile,
        "precision_score": precision_score,
        "decision": "accepted" if accepted else "rejected-low-precision",
    }


def _evidence_result_tokens(result: dict[str, Any]) -> set[str]:
    text = " ".join([
        str(result.get("title", "")),
        *(str(item) for item in result.get("knowledge_points", [])),
        *(str(item) for item in result.get("error_types", [])),
        *(
            str(document.get("snippet", ""))
            for document in result.get("matched_documents", [])
        ),
    ])
    return {
        token
        for token in kb.tokenize(text)
        if len(token.strip()) >= 2
        and token.strip().lower() not in EVIDENCE_AUDIT_STOP_TOKENS
    }


def _evidence_results_duplicate(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_title = "".join(str(left.get("title", "")).lower().split())
    right_title = "".join(str(right.get("title", "")).lower().split())
    if left_title and left_title == right_title:
        return True
    left_tokens = _evidence_result_tokens(left)
    right_tokens = _evidence_result_tokens(right)
    union = left_tokens | right_tokens
    if len(union) < 8:
        return False
    return len(left_tokens & right_tokens) / len(union) >= 0.88


def _evidence_results_conflict(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    left_profile = left.get("evidence_audit", {}).get("condition_profile", {})
    right_profile = right.get("evidence_audit", {}).get("condition_profile", {})
    conflicts: list[str] = []
    for facet in HARD_CONFLICT_FACETS:
        left_values = set(left_profile.get(facet, []))
        right_values = set(right_profile.get(facet, []))
        if left_values and right_values and not left_values & right_values:
            conflicts.append(
                f"{facet}: {','.join(sorted(left_values))} <> "
                f"{','.join(sorted(right_values))}"
            )
    return conflicts


def select_evidence_results(
    results: list[dict[str, Any]],
    *,
    selection_policy: str,
    limit: int,
) -> dict[str, Any]:
    """Select an auditable, non-contradictory evidence set from retrieved results."""
    if selection_policy not in {"baseline", "precision-gated-v1", "evidence-set-v2"}:
        raise ValueError(f"unsupported evidence selection policy: {selection_policy}")
    limit = max(1, int(limit))
    if selection_policy == "baseline":
        selected = [dict(result) for result in results[:limit]]
        return {
            "selected_results": selected,
            "trace": {
                "policy": selection_policy,
                "candidate_count": len(results),
                "selected_count": len(selected),
                "rejected_low_precision_count": 0,
                "rejected_duplicate_count": 0,
                "rejected_conflict_count": 0,
                "selection_steps": [],
            },
        }

    accepted = [
        result
        for result in results
        if result.get("evidence_audit", {}).get("decision") == "accepted"
    ]
    rejected_low_precision = len(results) - len(accepted)
    if selection_policy == "precision-gated-v1":
        selected = [dict(result) for result in accepted[:limit]]
        return {
            "selected_results": selected,
            "trace": {
                "policy": selection_policy,
                "candidate_count": len(results),
                "selected_count": len(selected),
                "rejected_low_precision_count": rejected_low_precision,
                "rejected_duplicate_count": 0,
                "rejected_conflict_count": 0,
                "selection_steps": [],
            },
        }

    remaining = list(accepted)
    selected: list[dict[str, Any]] = []
    selection_steps: list[dict[str, Any]] = []
    rejected_duplicate_count = 0
    rejected_conflict_count = 0
    covered_slots: set[str] = set()
    while remaining and len(selected) < limit:
        ranked: list[tuple[float, int, dict[str, Any], list[str]]] = []
        for result in remaining:
            conflicts = [
                conflict
                for chosen in selected
                for conflict in _evidence_results_conflict(chosen, result)
            ]
            new_slots = set(
                result.get("evidence_coverage", {}).get("covered_slots", [])
            ) - covered_slots
            precision = float(
                result.get("evidence_audit", {}).get("precision_score", 0.0)
            )
            route_diversity = len(
                result.get("evidence_audit", {})
                .get("match_basis", {})
                .get("route_ids", [])
            )
            utility = precision + 0.08 * len(new_slots) + 0.02 * min(route_diversity, 3)
            ranked.append((
                utility,
                -int(result.get("selected_rank", 9999)),
                result,
                conflicts,
            ))
        ranked.sort(key=lambda item: (-item[0], -item[1], str(item[2].get("title", ""))))
        _, _, candidate, conflicts = ranked[0]
        remaining.remove(candidate)
        duplicate = next(
            (
                chosen
                for chosen in selected
                if _evidence_results_duplicate(chosen, candidate)
            ),
            None,
        )
        if duplicate is not None:
            rejected_duplicate_count += 1
            selection_steps.append({
                "title": str(candidate.get("title", ""))[:120],
                "decision": "rejected-duplicate",
                "reason": f"near-duplicate-of:{str(duplicate.get('title', ''))[:120]}",
            })
            continue
        if conflicts:
            rejected_conflict_count += 1
            selection_steps.append({
                "title": str(candidate.get("title", ""))[:120],
                "decision": "rejected-inter-reference-conflict",
                "reason": "; ".join(conflicts)[:300],
            })
            continue
        chosen = dict(candidate)
        new_slots = sorted(
            set(chosen.get("evidence_coverage", {}).get("covered_slots", []))
            - covered_slots
        )
        chosen["evidence_selection"] = {
            "policy": selection_policy,
            "precision_score": float(
                chosen.get("evidence_audit", {}).get("precision_score", 0.0)
            ),
            "new_slots": new_slots,
            "reason": "accepted-highest-utility-compatible",
        }
        selected.append(chosen)
        covered_slots.update(
            chosen.get("evidence_coverage", {}).get("covered_slots", [])
        )
        selection_steps.append({
            "title": str(chosen.get("title", ""))[:120],
            "decision": "selected",
            "new_slots": new_slots,
        })
    return {
        "selected_results": selected,
        "trace": {
            "policy": selection_policy,
            "candidate_count": len(results),
            "selected_count": len(selected),
            "rejected_low_precision_count": rejected_low_precision,
            "rejected_duplicate_count": rejected_duplicate_count,
            "rejected_conflict_count": rejected_conflict_count,
            "covered_slots": sorted(covered_slots),
            "selection_steps": selection_steps,
        },
    }


def _fallback_lexical_score(content: str, title: str, retrieval_text: str) -> float:
    tokens = list(dict.fromkeys(kb.tokenize(retrieval_text)))
    if not tokens:
        return 0.0
    lowered_content = content.lower()
    lowered_title = title.lower()
    return float(sum(lowered_content.count(token) + 4 * lowered_title.count(token) for token in tokens))


def _route_rows(
    connection: sqlite3.Connection,
    *,
    fts: str,
    retrieval_text: str,
    kinds: tuple[str, ...],
    limit: int,
    use_fts: bool,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in kinds)
    if use_fts:
        rows = connection.execute(
            f"""
            SELECT d.entry_id, d.kind, d.path, d.title, d.content,
                   bm25(document_fts, 0.0, 0.0, 1.0, 1.0, 1.0) AS rank
            FROM document_fts
            JOIN document d
              ON d.entry_id = document_fts.entry_id AND d.kind = document_fts.kind
            WHERE document_fts MATCH ? AND d.kind IN ({placeholders})
            ORDER BY rank
            LIMIT ?
            """,
            (fts, *kinds, limit),
        ).fetchall()
        return [
            {
                "entry_id": row["entry_id"],
                "kind": row["kind"],
                "path": row["path"],
                "title": row["title"],
                "content": row["content"],
                "raw_score": max(-float(row["rank"] or 0.0), 0.0),
            }
            for row in rows
        ]

    rows = connection.execute(
        f"""
        SELECT entry_id, kind, path, title, content
        FROM document
        WHERE kind IN ({placeholders})
        """,
        kinds,
    ).fetchall()
    scored = [
        {
            "entry_id": row["entry_id"],
            "kind": row["kind"],
            "path": row["path"],
            "title": row["title"],
            "content": row["content"],
            "raw_score": _fallback_lexical_score(row["content"], row["title"], retrieval_text),
        }
        for row in rows
    ]
    return sorted(
        (row for row in scored if row["raw_score"] > 0),
        key=lambda row: (-float(row["raw_score"]), str(row["entry_id"]), str(row["kind"])),
    )[:limit]


def _retrieve_routes(
    connection: sqlite3.Connection,
    *,
    fts: str,
    retrieval_text: str,
    top_k: int,
    use_fts: bool,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Run independent lexical routes, then fuse their entry ranks with RRF."""
    fused: dict[str, dict[str, Any]] = {}
    diagnostics: list[dict[str, Any]] = []
    route_limit = max(top_k * 4, top_k)
    for route in RETRIEVAL_ROUTES:
        rows = _route_rows(
            connection,
            fts=fts,
            retrieval_text=retrieval_text,
            kinds=tuple(route["kinds"]),
            limit=route_limit,
            use_fts=use_fts,
        )
        route_entries: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = route_entries.setdefault(
                str(row["entry_id"]),
                {"raw_score": 0.0, "documents": [], "title": str(row["title"])},
            )
            entry["raw_score"] += float(row["raw_score"])
            entry["documents"].append(row)
        ranked_route = sorted(
            route_entries.items(),
            key=lambda pair: (-float(pair[1]["raw_score"]), pair[0]),
        )
        diagnostics.append({
            "id": route["id"],
            "label": route["label"],
            "document_kinds": list(route["kinds"]),
            "weight": float(route["weight"]),
            "candidate_count": len(ranked_route),
            "top_entry_ids": [entry_id for entry_id, _ in ranked_route[:top_k]],
        })
        for rank, (entry_id, route_match) in enumerate(ranked_route, 1):
            contribution = float(route["weight"]) / (RRF_K + rank)
            item = fused.setdefault(
                entry_id,
                {
                    "score": 0.0,
                    "lexical_score": 0.0,
                    "rrf_score": 0.0,
                    "matched_documents": [],
                    "best_title": route_match["title"],
                    "route_matches": [],
                },
            )
            item["lexical_score"] += float(route_match["raw_score"])
            item["rrf_score"] += contribution
            # BM25 magnitude preserves strong lexical evidence inside a route;
            # RRF adds a small, stable reward for agreement across independent routes.
            item["score"] = item["lexical_score"] + item["rrf_score"]
            item["route_matches"].append({
                "route": route["id"],
                "rank": rank,
                "raw_score": round(float(route_match["raw_score"]), 6),
                "rrf_contribution": round(contribution, 8),
            })
            for document in route_match["documents"]:
                item["matched_documents"].append({
                    "kind": document["kind"],
                    "path": document["path"],
                    "route": route["id"],
                    "raw_score": round(float(document["raw_score"]), 6),
                    "snippet": _snippet(document["content"], retrieval_text),
                })
    return fused, diagnostics


def _route_id_for_kind(kind: str) -> str:
    for route in RETRIEVAL_ROUTES:
        if kind in route["kinds"]:
            return str(route["id"])
    return "unknown"


def _retrieve_global(
    connection: sqlite3.Connection,
    *,
    fts: str,
    retrieval_text: str,
    top_k: int,
    use_fts: bool,
) -> dict[str, dict[str, Any]]:
    """Preserve the proven single-pool BM25 baseline as the production gate."""
    limit = max(top_k * 4, top_k)
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
            (fts, limit),
        ).fetchall()
        scored_rows = [
            {
                "entry_id": row["entry_id"],
                "kind": row["kind"],
                "path": row["path"],
                "title": row["title"],
                "content": row["content"],
                "raw_score": max(-float(row["rank"] or 0.0), 0.0),
            }
            for row in rows
        ]
    else:
        pattern = f"%{retrieval_text}%"
        rows = connection.execute(
            """
            SELECT entry_id, kind, path, title, content
            FROM document
            WHERE content LIKE ? OR token_text LIKE ?
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()
        scored_rows = [
            {
                "entry_id": row["entry_id"],
                "kind": row["kind"],
                "path": row["path"],
                "title": row["title"],
                "content": row["content"],
                "raw_score": 0.5,
            }
            for row in rows
        ]

    grouped: dict[str, dict[str, Any]] = {}
    for row in scored_rows:
        item = grouped.setdefault(
            str(row["entry_id"]),
            {
                "score": 0.0,
                "lexical_score": 0.0,
                "rrf_score": 0.0,
                "matched_documents": [],
                "best_title": str(row["title"]),
                "route_matches": [],
            },
        )
        item["score"] += float(row["raw_score"])
        item["lexical_score"] += float(row["raw_score"])
        item["matched_documents"].append({
            "kind": row["kind"],
            "path": row["path"],
            "route": _route_id_for_kind(str(row["kind"])),
            "raw_score": round(float(row["raw_score"]), 6),
            "snippet": _snippet(str(row["content"]), retrieval_text),
        })
    return grouped


def _rank_grouped(grouped: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(grouped.items(), key=lambda pair: (-float(pair[1]["score"]), pair[0]))


def _intent_augmented_ranking(
    baseline_grouped: dict[str, dict[str, Any]],
    intent_grouped: dict[str, dict[str, Any]],
    top_k: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Keep the stable head, then reserve up to two slots for normalized intent."""
    baseline = _rank_grouped(baseline_grouped)
    intent = _rank_grouped(intent_grouped)
    reserve = min(2, max(1, top_k // 2))
    keep_count = max(1, top_k - reserve)
    selected: list[tuple[str, dict[str, Any]]] = [
        (entry_id, {**match, "selection_origin": "baseline"})
        for entry_id, match in baseline[:keep_count]
    ]
    selected_ids = {entry_id for entry_id, _ in selected}
    for entry_id, match in intent:
        if entry_id in selected_ids:
            continue
        selected.append((entry_id, {**match, "selection_origin": "intent"}))
        selected_ids.add(entry_id)
        if len(selected) >= top_k:
            break
    for entry_id, match in baseline[keep_count:]:
        if entry_id in selected_ids:
            continue
        selected.append((entry_id, {**match, "selection_origin": "baseline"}))
        selected_ids.add(entry_id)
        if len(selected) >= top_k:
            break
    return selected[:top_k]


def _recent_events(connection: sqlite3.Connection, entry_id: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT event_id, task_type, actor, event_type, status, raw_status, created_at,
               summary, changed_files_json, failure_reasons_json, evaluation_json,
               feedback_json, links_json, result_json
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
            "feedback": _loads(row["feedback_json"], {}),
            "links": _loads(row["links_json"], {}),
            "result": _loads(row["result_json"], {}),
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
    root: Path,
    text: str,
    *,
    mode: str = "auto",
    top_k: int = 5,
    explicit_db: Path | None = None,
    ranking_policy: str = "baseline",
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    target = db_path(root, explicit_db)
    if not target.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "unavailable",
            "reason": "knowledge-store-missing",
            "query": text,
            "mode": mode,
            "results": [],
            "freshness": {"status": "missing", "database": str(target)},
        }
    retrieval_text, query_expansions = (
        _expanded_teaching_query(text) if mode in {"auto", "teaching"} else (text, [])
    )
    intent_text, removed_phrases, intent_expansions = (
        _teaching_intent_query(text)
        if mode in {"auto", "teaching"}
        else (text, [], [])
    )
    query_plan = _query_plan(
        text,
        retrieval_text,
        query_expansions,
        intent_text,
        removed_phrases,
        intent_expansions,
        mode,
    )
    fts = _fts_query(retrieval_text)
    intent_fts = _fts_query(intent_text)
    connection = connect_readonly(target)
    try:
        schema_row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if not schema_row or int(schema_row["value"]) < SCHEMA_VERSION:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "unavailable",
                "reason": "knowledge-store-schema-stale",
                "query": text,
                "mode": mode,
                "results": [],
                "freshness": {"status": "schema-stale", "database": str(target)},
            }
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()
        }
        required_tables = {"meta", "entry", "document", "candidate_event", "scheduler_benchmark", "evolve_observation"}
        if not required_tables.issubset(tables):
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "unavailable",
                "reason": "knowledge-store-incomplete",
                "query": text,
                "mode": mode,
                "results": [],
                "freshness": {"status": "schema-stale", "database": str(target)},
            }
        generated_row = connection.execute("SELECT value FROM meta WHERE key='generated_at'").fetchone()
        indexed_event_row = connection.execute(
            "SELECT value FROM meta WHERE key='last_archive_event_id'"
        ).fetchone()
        dirty = kb.load_json(root / DIRTY_MARKER_RELATIVE, {})
        freshness = {
            "status": "stale" if dirty else "current",
            "indexed_at": generated_row["value"] if generated_row else "",
            "indexed_event_id": indexed_event_row["value"] if indexed_event_row else "",
            "latest_event_id": dirty.get("last_event_id") if isinstance(dirty, dict) else "",
            "database": str(target),
        }
        has_fts = connection.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()
        fts_available = bool(has_fts and has_fts["value"] == "1")
        use_fts = bool(fts and fts_available)
        multi_route_grouped, route_diagnostics = _retrieve_routes(
            connection,
            fts=fts,
            retrieval_text=retrieval_text,
            top_k=top_k,
            use_fts=use_fts,
        )
        baseline_grouped = _retrieve_global(
            connection,
            fts=fts,
            retrieval_text=retrieval_text,
            top_k=top_k,
            use_fts=use_fts,
        )
        intent_grouped = (
            baseline_grouped
            if intent_text == retrieval_text
            else _retrieve_global(
                connection,
                fts=intent_fts,
                retrieval_text=intent_text,
                top_k=top_k,
                use_fts=bool(intent_fts and fts_available),
            )
        )
        if ranking_policy not in {"baseline", "multi-route", "intent-augmented"}:
            raise ValueError(f"unsupported ranking_policy: {ranking_policy}")
        grouped = multi_route_grouped if ranking_policy == "multi-route" else baseline_grouped
        if ranking_policy in {"baseline", "intent-augmented"}:
            for entry_id, match in grouped.items():
                shadow = multi_route_grouped.get(entry_id, {})
                match["route_matches"] = shadow.get("route_matches", [])
                match["rrf_score"] = float(shadow.get("rrf_score", 0.0))
        if ranking_policy == "intent-augmented":
            ranked = _intent_augmented_ranking(baseline_grouped, intent_grouped, top_k)
        else:
            ranked = _rank_grouped(grouped)[:top_k]
        shadow_ranked = _rank_grouped(multi_route_grouped)[:top_k]
        intent_ranked = _rank_grouped(intent_grouped)[:top_k]
        results: list[dict[str, Any]] = []
        for selected_rank, (entry_id, match) in enumerate(ranked, 1):
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
            route_matches = sorted(
                match.get("route_matches", []),
                key=lambda item: (item["rank"], item["route"]),
            )
            matched_documents = sorted(
                match["matched_documents"],
                key=lambda item: (-float(item["raw_score"]), item["route"], item["kind"]),
            )[:3]
            knowledge_points = _loads(entry_row["knowledge_points_json"], [])
            error_types = _loads(entry_row["error_types_json"], [])
            evidence_audit = _evidence_relevance_audit(
                query_plan,
                title=str(entry_row["title"]),
                knowledge_points=knowledge_points,
                error_types=error_types,
                matched_documents=matched_documents,
                route_matches=route_matches,
            )
            results.append({
                "entry_id": entry_id,
                "title": entry_row["title"],
                "subject": entry_row["subject"],
                "status": entry_row["status"],
                "library_folder": entry_row["library_folder"],
                "score": round(float(match["score"]), 4),
                "lexical_score": round(float(match["lexical_score"]), 4),
                "rrf_score": round(float(match["rrf_score"]), 6),
                "selected_rank": selected_rank,
                "selection_origin": str(match.get("selection_origin", ranking_policy)),
                "path": f"entries/{entry_id}",
                "knowledge_points": knowledge_points,
                "error_types": error_types,
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
                "route_matches": route_matches,
                "matched_documents": matched_documents,
                "evidence_coverage": _evidence_coverage(route_matches, matched_documents),
                "evidence_audit": evidence_audit,
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
        "query_expansions": query_expansions,
        "query_plan": query_plan,
        "mode": mode,
        "status": "ok",
        "generated_at": kb.now_iso(),
        "database": str(target),
        "freshness": freshness,
        "results": results,
        "retrieval": {
            "strategy": {
                "baseline": "baseline-bm25-with-shadows-v2",
                "multi-route": "multi-route-bm25-rrf-v1",
                "intent-augmented": "stable-head-intent-augmentation-v1",
            }[ranking_policy],
            "selected_policy": ranking_policy,
            "activation_status": "active" if ranking_policy == "baseline" else "experimental",
            "lexical_backend": "sqlite-fts5-bm25" if use_fts else "deterministic-local-scan",
            "rrf_k": RRF_K,
            "candidate_count": len(grouped),
            "routes": route_diagnostics,
            "shadow_top_entry_ids": [entry_id for entry_id, _ in shadow_ranked],
            "intent_query": intent_text,
            "intent_candidate_count": len(intent_grouped),
            "intent_top_entry_ids": [entry_id for entry_id, _ in intent_ranked],
            "intent_reserved_slots": min(2, max(1, top_k // 2)),
        },
        "evidence_set": _evidence_set_diagnostics(results),
        "scheduler_benchmarks": scheduler_benchmarks,
        "evolve_observations": evolve_observations,
        "evidence_sources": sorted({
            f"{result['path']}/{doc['path']}" for result in results for doc in result["matched_documents"]
        }),
        "required_checks": required_checks,
        "notes": [
            "SQLite is a derived local index; canonical truth remains Markdown/JSON/JSONL.",
            "Independent metadata/problem/solution routes are fused at entry level with auditable RRF.",
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
    selection_policy: str = "baseline",
) -> dict[str, Any]:
    """Build a privacy-minimized, read-only evidence pack for one Agent task.

    This deliberately does not rebuild a missing store: task submission must not
    turn into a whole-library write or block because retrieval is unavailable.
    """
    root = root.expanduser().resolve()
    if selection_policy not in {"baseline", "precision-gated-v1", "evidence-set-v2"}:
        raise ValueError(f"unsupported evidence selection policy: {selection_policy}")
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
    if retrieved.get("status") != "ok":
        return {**base, "status": "unavailable", "reason": retrieved.get("reason", "knowledge-store-unavailable")}
    if retrieved.get("freshness", {}).get("status") != "current":
        return {
            **base,
            "status": "unavailable",
            "reason": "knowledge-store-stale",
            "freshness": retrieved.get("freshness", {}),
        }

    references: list[dict[str, Any]] = []
    budget = max(1000, min(int(char_budget), 20000))
    candidate_results = [
        result for result in retrieved.get("results", [])
        if result.get("entry_id") != entry_id
    ]
    selection = select_evidence_results(
        candidate_results,
        selection_policy=selection_policy,
        limit=max(1, int(top_k)),
    )
    selection_trace = selection["trace"]
    eligible_results = selection["selected_results"]
    for result in eligible_results:
        if result.get("entry_id") == entry_id:
            continue
        recent_lessons = []
        for event in result.get("recent_events", [])[:3]:
            lesson = {
                "task_type": str(event.get("task_type", "")),
                "status": str(event.get("status", "")),
                "summary": str(event.get("summary", ""))[:240],
                "failure_reasons": [str(item)[:240] for item in event.get("failure_reasons", [])[:3]],
                "feedback": event.get("feedback", {}),
                "adoption": event.get("feedback", {}).get("adoption", {}),
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
            "coverage": result.get("evidence_coverage", {}),
            "evidence_audit": result.get("evidence_audit", {}),
            "evidence_selection": result.get("evidence_selection", {}),
            "recent_lessons": recent_lessons,
        }
        reference_bytes = json.dumps(reference, ensure_ascii=False, sort_keys=True).encode("utf-8")
        reference["content_hash"] = f"sha256:{hashlib.sha256(reference_bytes).hexdigest()}"
        candidate = {**base, "references": [*references, reference]}
        # Reserve a small, fixed envelope for the audit metadata appended below.
        if len(json.dumps(candidate, ensure_ascii=False)) > max(400, budget - 600):
            break
        references.append(reference)
        if len(references) >= top_k:
            break
    def materialized_selection_steps() -> list[dict[str, Any]]:
        materialized_titles = Counter(
            str(reference.get("title", "")) for reference in references
        )
        steps: list[dict[str, Any]] = []
        for step in selection_trace["selection_steps"]:
            normalized_step = dict(step)
            title = str(step.get("title", ""))
            if step.get("decision") == "selected":
                if materialized_titles[title] > 0:
                    materialized_titles[title] -= 1
                else:
                    normalized_step["decision"] = "omitted-context-budget"
                    normalized_step["reason"] = (
                        "selected by policy but omitted during serialization budgeting"
                    )
            steps.append(normalized_step)
        return steps
    payload = {
        **base,
        "references": references,
        "evidence_set": {
            **_reference_set_diagnostics(references),
            "selection_policy": selection_policy,
            "selection_status": (
                "degraded-empty-low-precision"
                if selection_policy in {"precision-gated-v1", "evidence-set-v2"}
                and candidate_results
                and not references
                else "selected"
            ),
            "rejected_low_precision_count": selection_trace["rejected_low_precision_count"],
            "rejected_duplicate_count": selection_trace["rejected_duplicate_count"],
            "rejected_conflict_count": selection_trace["rejected_conflict_count"],
            "selection_steps": materialized_selection_steps(),
        },
        "context_budget": {
            "policy": "deterministic-evidence-v1",
            "measurement": "serialized-json-characters",
            "requested_chars": budget,
            "reference_limit": max(1, int(top_k)),
            "section_priority": [
                "title-and-labels",
                "methods",
                "secondary-conclusions",
                "matched-evidence",
                "evaluator-findings",
                "recent-lessons",
            ],
            "protected_scope": "current canonical problem, answer, and teacher instruction are outside this pack",
            "candidate_reference_count": len(candidate_results),
            "eligible_reference_count": len(eligible_results),
            "included_reference_count": len(references),
            "omitted_reference_count": max(0, len(candidate_results) - len(references)),
            "truncated": len(references) < len(candidate_results),
            "serialized_chars": 0,
        },
    }
    for _ in range(3):
        payload["context_budget"]["serialized_chars"] = len(json.dumps(payload, ensure_ascii=False))
    while payload["context_budget"]["serialized_chars"] > budget and payload["references"]:
        payload["references"].pop()
        payload["evidence_set"] = {
            **_reference_set_diagnostics(payload["references"]),
            "selection_policy": selection_policy,
            "selection_status": (
                "degraded-empty-low-precision"
                if selection_policy in {"precision-gated-v1", "evidence-set-v2"}
                and candidate_results
                and not payload["references"]
                else "selected"
            ),
            "rejected_low_precision_count": selection_trace["rejected_low_precision_count"],
            "rejected_duplicate_count": selection_trace["rejected_duplicate_count"],
            "rejected_conflict_count": selection_trace["rejected_conflict_count"],
            "selection_steps": materialized_selection_steps(),
        }
        payload["context_budget"]["included_reference_count"] = len(payload["references"])
        payload["context_budget"]["omitted_reference_count"] = max(
            0, len(candidate_results) - len(payload["references"])
        )
        payload["context_budget"]["truncated"] = True
        payload["context_budget"]["serialized_chars"] = len(json.dumps(payload, ensure_ascii=False))
    return payload


def build_blueprint_evidence(
    root: Path,
    entry_id: str,
    blueprint: dict[str, Any],
    *,
    task_type: str = "analysis.generate",
    default_need_limit: int = 3,
    max_need_limit: int = 5,
    top_k: int = 4,
    char_budget: int = 8000,
    explicit_db: Path | None = None,
) -> dict[str, Any]:
    """Retrieve by blueprint need clusters, then run one W2 evidence-set selection.

    The first three highest-priority clusters are the default budget. Clusters four
    and five run only while they add a previously uncovered target or a new
    candidate entry. Model context remains bounded independently of query count.
    """
    root = root.expanduser().resolve()
    base = {
        "schema_version": 1,
        "kind": "agent-evidence",
        "task_type": task_type,
        "status": "ready",
        "references": [],
        "instructions": [
            "当前题干和双层蓝图优先于历史证据。",
            "历史片段只能提供可迁移方法、适用条件和风险提示。",
            "不得复制历史答案或暴露内部条目、数据库和本地路径。",
        ],
    }
    target = db_path(root, explicit_db)
    if not target.is_file():
        return {**base, "status": "unavailable", "reason": "knowledge-store-missing"}
    raw_needs = blueprint.get("retrieval_needs")
    if not isinstance(raw_needs, list) or not raw_needs:
        return {**base, "status": "unavailable", "reason": "blueprint-has-no-retrieval-needs"}
    needs = sorted(
        [item for item in raw_needs if isinstance(item, dict) and str(item.get("query", "")).strip()],
        key=lambda item: (-int(item.get("priority", 1)), str(item.get("id", ""))),
    )[:max(1, min(int(max_need_limit), 5))]
    default_limit = max(1, min(int(default_need_limit), len(needs), 3))
    pooled: dict[str, dict[str, Any]] = {}
    executed: list[dict[str, Any]] = []
    covered_targets: set[str] = set()
    no_gain_streak = 0
    stop_reason = "need-limit-reached"
    for index, need in enumerate(needs):
        if index >= default_limit and no_gain_streak >= 2:
            stop_reason = "two-consecutive-needs-without-positive-marginal-gain"
            break
        need_id = str(need.get("id", f"need-{index + 1}"))
        query_text = str(need.get("query", "")).strip()
        target_ids = {str(item) for item in need.get("target_ids", []) if str(item)}
        try:
            retrieved = query(
                root,
                query_text,
                mode="teaching",
                top_k=max(3, int(top_k) + 2),
                explicit_db=target,
                ranking_policy="baseline",
            )
        except (OSError, sqlite3.Error, ValueError):
            retrieved = {"status": "unavailable", "results": []}
        before_entries = set(pooled)
        for result in retrieved.get("results", []) if retrieved.get("status") == "ok" else []:
            result_id = str(result.get("entry_id", ""))
            if not result_id or result_id == entry_id:
                continue
            existing = pooled.get(result_id)
            if existing is None:
                existing = dict(result)
                existing["retrieval_need_ids"] = []
                existing["retrieval_target_ids"] = []
                pooled[result_id] = existing
            if need_id not in existing["retrieval_need_ids"]:
                existing["retrieval_need_ids"].append(need_id)
            existing["retrieval_target_ids"] = sorted(
                set(existing["retrieval_target_ids"]) | target_ids
            )
        new_entries = len(set(pooled) - before_entries)
        new_targets = target_ids - covered_targets
        positive_gain = bool(new_entries or new_targets)
        no_gain_streak = 0 if positive_gain else no_gain_streak + 1
        covered_targets.update(target_ids)
        executed.append({
            "need_id": need_id,
            "query": query_text[:300],
            "priority": int(need.get("priority", 1)),
            "candidate_count": len(retrieved.get("results", [])),
            "new_candidate_count": new_entries,
            "new_target_ids": sorted(new_targets),
            "marginal_gain_positive": positive_gain,
        })
        if index + 1 >= len(needs):
            stop_reason = "all-blueprint-needs-executed"

    candidate_results = list(pooled.values())
    candidate_results.sort(
        key=lambda item: (
            -len(item.get("retrieval_need_ids", [])),
            int(item.get("selected_rank", 9999)),
            str(item.get("title", "")),
        )
    )
    selection = select_evidence_results(
        candidate_results,
        selection_policy="evidence-set-v2",
        limit=max(1, int(top_k)),
    )
    selected = selection["selected_results"]
    references: list[dict[str, Any]] = []
    budget = max(1000, min(int(char_budget), 20000))
    for result in selected:
        reference = {
            "reference": f"similar-{len(references) + 1}",
            "title": str(result.get("title", "相似题"))[:120],
            "knowledge_points": [str(item)[:80] for item in result.get("knowledge_points", [])[:8]],
            "methods": [str(item)[:160] for item in result.get("teaching_memory", {}).get("methods", [])[:6]],
            "secondary_conclusions": [
                str(item)[:200] for item in result.get("teaching_memory", {}).get("secondary_conclusions", [])[:5]
            ],
            "matched_evidence": [
                {"kind": str(doc.get("kind", "")), "snippet": str(doc.get("snippet", ""))[:240]}
                for doc in result.get("matched_documents", [])[:3]
            ],
            "coverage": result.get("evidence_coverage", {}),
            "evidence_audit": result.get("evidence_audit", {}),
            "retrieval_need_ids": result.get("retrieval_need_ids", []),
            "retrieval_target_ids": result.get("retrieval_target_ids", []),
        }
        reference_bytes = json.dumps(reference, ensure_ascii=False, sort_keys=True).encode("utf-8")
        reference["content_hash"] = f"sha256:{hashlib.sha256(reference_bytes).hexdigest()}"
        candidate = {**base, "references": [*references, reference]}
        if len(json.dumps(candidate, ensure_ascii=False)) > max(400, budget - 1000):
            break
        references.append(reference)
    payload = {
        **base,
        "references": references,
        "retrieval_plan": {
            "policy": "blueprint-needs-adaptive-v1",
            "default_need_limit": default_limit,
            "max_need_limit": min(max(1, int(max_need_limit)), 5),
            "available_need_count": len(needs),
            "executed_need_count": len(executed),
            "executed_needs": executed,
            "covered_target_ids": sorted(covered_targets),
            "stop_reason": stop_reason,
        },
        "evidence_set": {
            **_reference_set_diagnostics(references),
            "selection_policy": "evidence-set-v2",
            "selection_status": (
                "degraded-empty-low-precision"
                if candidate_results and not references
                else "selected"
            ),
            **{
                key: selection["trace"][key]
                for key in (
                    "rejected_low_precision_count",
                    "rejected_duplicate_count",
                    "rejected_conflict_count",
                    "selection_steps",
                )
            },
        },
        "context_budget": {
            "policy": "deterministic-evidence-v1",
            "measurement": "serialized-json-characters",
            "requested_chars": budget,
            "included_reference_count": len(references),
            "candidate_reference_count": len(candidate_results),
            "truncated": len(references) < len(candidate_results),
            "serialized_chars": 0,
        },
    }
    for _ in range(3):
        payload["context_budget"]["serialized_chars"] = len(json.dumps(payload, ensure_ascii=False))
    while payload["context_budget"]["serialized_chars"] > budget and payload["references"]:
        payload["references"].pop()
        payload["context_budget"]["included_reference_count"] = len(payload["references"])
        payload["context_budget"]["truncated"] = True
        payload["context_budget"]["serialized_chars"] = len(json.dumps(payload, ensure_ascii=False))
    return payload


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
