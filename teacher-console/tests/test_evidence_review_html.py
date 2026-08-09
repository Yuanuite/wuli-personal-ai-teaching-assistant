import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "output" / "evidence-calibration-review.html"
HOLDOUT_HTML = ROOT / "output" / "evidence-holdout-review.html"
CURRENT_HOLDOUT_HTML = ROOT / "output" / "evidence-holdout-review-v3.html"
FRESH_V2_HTML = ROOT / "output" / "evidence-holdout-review-v5.html"
FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "evidence-agent" / "calibration-curated-v1.json"
HOLDOUT_FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "evidence-agent" / "holdout-curated-v1.json"
CURRENT_HOLDOUT_FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "evidence-agent" / "holdout-curated-v3.json"
FRESH_V2_FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "evidence-agent" / "fresh-holdout-v2-v2.json"
FRESH_V2_OVERLAY = (
    ROOT / "teacher-console" / "tests" / "fixtures" / "evidence-agent" / "fresh-holdout-v2-evidence-overlay.json"
)
FRESH_V3_HTML = ROOT / "output" / "evidence-holdout-review-v6.html"
FRESH_V3_FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "evidence-agent" / "fresh-holdout-v3.json"
FRESH_V3_OVERLAY = (
    ROOT / "teacher-console" / "tests" / "fixtures" / "evidence-agent" / "fresh-holdout-v3-evidence-overlay.json"
)


def _read_output_artifact(path: Path, label: str) -> str:
    """输出 HTML 由本地渲染脚本生成（output/ 被 gitignore）；CI 干净检出时跳过。"""
    if not path.exists():
        raise unittest.SkipTest(f"缺少本地生成产物 {path.relative_to(ROOT)}（{label}）；本地渲染后运行")
    return path.read_text(encoding="utf-8")


class EvidenceReviewHtmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read_output_artifact(HTML, "evidence-calibration-review.html")
        cls.dataset = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_page_embeds_exact_dataset_fingerprint_and_every_case(self):
        self.assertIn(self.dataset["dataset_fingerprint"], self.html)
        for item in self.dataset["cases"]:
            self.assertIn(item["gold_case"]["case_id"], self.html)

    def test_page_has_review_controls_and_no_network_calls(self):
        for identifier in (
            "approveBtn",
            "changeBtn",
            "approveAllBtn",
            "downloadBtn",
            "reviewer",
        ):
            self.assertIn(f'id="{identifier}"', self.html)
        self.assertNotRegex(self.html, r"\bfetch\s*\(")
        self.assertNotIn("XMLHttpRequest", self.html)
        self.assertNotIn("WebSocket", self.html)

    def test_inline_javascript_is_syntactically_valid(self):
        scripts = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertEqual(len(scripts), 1)
        completed = subprocess.run(
            ["node", "--check", "-"],
            input=scripts[0],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_revised_page_carries_only_unchanged_approvals(self):
        html = _read_output_artifact(CURRENT_HOLDOUT_HTML, "evidence-holdout-review-v3.html")
        dataset = json.loads(CURRENT_HOLDOUT_FIXTURE.read_text(encoding="utf-8"))
        self.assertIn(dataset["dataset_fingerprint"], html)
        self.assertIn("已结转 19 条", html)
        self.assertIn("只需复核其余 1 条", html)
        self.assertEqual(html.count("从上一版逐条审核结转；本条内容未变化。"), 19)
        initial_decisions = html.split("const INITIAL_DECISIONS = ", 1)[1].split(";\n    let decisions =", 1)[0]
        for changed_id in ("holdout-lorentz-positive-charge",):
            self.assertNotIn(f'"{changed_id}": {{', initial_decisions)

        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertEqual(len(scripts), 1)
        completed = subprocess.run(
            ["node", "--check", "-"],
            input=scripts[0],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_fresh_holdout_page_is_local_and_embeds_all_twenty_cases(self):
        html = _read_output_artifact(HOLDOUT_HTML, "evidence-holdout-review.html")
        dataset = json.loads(HOLDOUT_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(dataset["cases"]), 20)
        self.assertIn(dataset["dataset_fingerprint"], html)
        self.assertIn("fresh holdout 候选", html)
        for item in dataset["cases"]:
            self.assertIn(item["gold_case"]["case_id"], html)
        self.assertNotRegex(html, r"\bfetch\s*\(")
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotIn("WebSocket", html)

        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertEqual(len(scripts), 1)
        completed = subprocess.run(
            ["node", "--check", "-"],
            input=scripts[0],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_second_fresh_holdout_page_embeds_overlay_and_all_cases(self):
        html = _read_output_artifact(FRESH_V2_HTML, "evidence-holdout-review-v5.html")
        dataset = json.loads(FRESH_V2_FIXTURE.read_text(encoding="utf-8"))
        overlay = json.loads(FRESH_V2_OVERLAY.read_text(encoding="utf-8"))
        self.assertEqual(len(dataset["cases"]), 21)
        self.assertIn(dataset["dataset_fingerprint"], html)
        self.assertIn("这 21 条是 fresh holdout 候选", html)
        self.assertIn("已结转 19 条", html)
        self.assertIn("只需复核其余 2 条", html)
        for item in dataset["cases"]:
            self.assertIn(item["gold_case"]["case_id"], html)
        for unit in overlay["units"]:
            self.assertIn(unit["evidence_id"], html)
            self.assertIn(unit["text"], html)
        initial_decisions = html.split("const INITIAL_DECISIONS = ", 1)[1].split(";\n    let decisions =", 1)[0]
        for changed_id in (
            "fresh2-interference-frequency-conflict",
            "fresh2-photo-below-threshold-conflict",
        ):
            self.assertNotIn(f'"{changed_id}": {{', initial_decisions)
        self.assertNotRegex(html, r"\bfetch\s*\(")
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotIn("WebSocket", html)

        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertEqual(len(scripts), 1)
        completed = subprocess.run(
            ["node", "--check", "-"],
            input=scripts[0],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_third_fresh_holdout_page_shows_diagnostics_and_stays_local(self):
        html = _read_output_artifact(FRESH_V3_HTML, "evidence-holdout-review-v6.html")
        dataset = json.loads(FRESH_V3_FIXTURE.read_text(encoding="utf-8"))
        overlay = json.loads(FRESH_V3_OVERLAY.read_text(encoding="utf-8"))
        self.assertEqual(len(dataset["cases"]), 21)
        self.assertIn(dataset["dataset_fingerprint"], html)
        self.assertIn("这 21 条是 fresh holdout 候选", html)
        self.assertIn("纠错对象（出现不代表证据不适用）", html)
        for item in dataset["cases"]:
            self.assertIn(item["gold_case"]["case_id"], html)
            for target in item["gold_case"]["retrieval_need"].get("diagnostic_targets", []):
                self.assertIn(target, html)
        for unit in overlay["units"]:
            self.assertIn(unit["evidence_id"], html)
            self.assertIn(unit["text"], html)
        self.assertNotRegex(html, r"\bfetch\s*\(")
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotIn("WebSocket", html)
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertEqual(len(scripts), 1)
        completed = subprocess.run(
            ["node", "--check", "-"],
            input=scripts[0],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
