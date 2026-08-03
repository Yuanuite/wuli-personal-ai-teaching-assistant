import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location("teacher_console_server_debounce", ROOT / "teacher-console" / "server.py")
assert SPEC is not None
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class SourceCleanIndexDebounceTest(unittest.TestCase):
    def test_burst_is_coalesced_and_shutdown_flushes_once(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / "library"
            debouncer = server.IndexRebuildDebouncer(60)
            with (
                mock.patch.object(server, "LIBRARY", library),
                mock.patch.object(server.kb, "rebuild_index", return_value={"status": "ok"}) as rebuild,
            ):
                debouncer.schedule()
                debouncer.schedule()
                debouncer.schedule()
                debouncer.flush()
                debouncer.flush()
            rebuild.assert_called_once_with(library)


if __name__ == "__main__":
    unittest.main()
