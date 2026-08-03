import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_archive  # noqa: E402
import server  # noqa: E402


def _write_event(entry: Path, *, task_type: str, status: str, event_id: str, summary: str = "") -> None:
    event = {
        "schema_version": candidate_archive.SCHEMA_VERSION,
        "event_id": event_id,
        "entry_id": entry.name,
        "task_type": task_type,
        "actor": "agent",
        "event_type": "agent-result",
        "status": status,
        "summary": summary,
        "generated_at": "2026-08-03T21:00:00+08:00",
    }
    with (entry / candidate_archive.ENTRY_ARCHIVE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


class LatestSuccessfulAnalysisEventTest(unittest.TestCase):
    """Work-tree A4: version provenance must separate latest attempt from current answer."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.entry = Path(self.temporary.name) / "entry"
        self.entry.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_returns_most_recent_completed_analysis_event(self):
        _write_event(self.entry, task_type="analysis.generate", status="completed", event_id="ev-old", summary="旧版")
        _write_event(self.entry, task_type="analysis.generate", status="failed", event_id="ev-failed")
        _write_event(self.entry, task_type="answer.revise", status="completed", event_id="ev-other-task")
        _write_event(self.entry, task_type="analysis.generate", status="completed", event_id="ev-new", summary="新版")
        event = server.latest_successful_analysis_event(self.entry)
        self.assertEqual(event.get("event_id"), "ev-new")

    def test_failed_attempts_are_not_current_answer_version(self):
        _write_event(self.entry, task_type="analysis.generate", status="completed", event_id="ev-ok")
        _write_event(self.entry, task_type="analysis.generate", status="failed", event_id="ev-latest-failed")
        event = server.latest_successful_analysis_event(self.entry)
        self.assertEqual(event.get("event_id"), "ev-ok")

    def test_exclude_event_id_skips_current_attempt(self):
        _write_event(self.entry, task_type="analysis.generate", status="completed", event_id="ev-previous")
        _write_event(self.entry, task_type="analysis.generate", status="completed", event_id="ev-current")
        event = server.latest_successful_analysis_event(self.entry, exclude_event_id="ev-current")
        self.assertEqual(event.get("event_id"), "ev-previous")

    def test_archive_normalized_status_counts_as_successful(self):
        # candidate_archive.append_event normalizes "completed" to "succeeded".
        _write_event(self.entry, task_type="analysis.generate", status="succeeded", event_id="ev-normalized")
        event = server.latest_successful_analysis_event(self.entry)
        self.assertEqual(event.get("event_id"), "ev-normalized")

    def test_missing_archive_returns_empty_dict(self):
        self.assertEqual(server.latest_successful_analysis_event(self.entry), {})


if __name__ == "__main__":
    unittest.main()
