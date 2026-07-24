import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "teacher-console" / "scripts" / "pipeline_quality_eval.py"
SPEC = importlib.util.spec_from_file_location("pipeline_quality_eval_test", SCRIPT)
assert SPEC is not None
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)


class PipelineQualityEvalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        self.entries = self.library / "entries"
        self.entries.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_cli_library_argument_controls_the_scanned_entries(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                sys,
                "argv",
                ["pipeline_quality_eval.py", "--library", str(self.library)],
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(quality.main(), 0)

        report = json.loads(stdout.getvalue())
        self.assertEqual(report["evaluated_entries"], 0)
        self.assertIn("Auto-scan: 0 delivered entries found", stderr.getvalue())

    def test_perfect_dimensions_normalize_to_one_hundred(self):
        entry = self.entries / "perfect-entry"
        entry.mkdir()
        solution = (
            "# 解析\n\n"
            "## 答案速览\n应用动量守恒。\n\n"
            "## 详细解答\n先选系统，再列动量守恒方程，最后检查方向和单位。\n\n"
            "## 易错点\n不要忽略方向。\n"
        )
        (entry / "problem.md").write_text("# 题目\n\n求碰撞后的共同速度。", encoding="utf-8")
        (entry / "solution.md").write_text(solution, encoding="utf-8")
        (entry / "student-solution.md").write_text(solution, encoding="utf-8")
        (entry / "pipeline.json").write_text(
            json.dumps({"state": "delivered"}, ensure_ascii=False),
            encoding="utf-8",
        )

        report = quality.evaluate_entry(entry.name, entries_root=self.entries)

        self.assertEqual(report["score"], 100)
        self.assertTrue(report["pass"])


if __name__ == "__main__":
    unittest.main()
