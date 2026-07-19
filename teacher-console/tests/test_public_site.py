import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import public_site  # noqa: E402


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class PublicSiteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.library = root / "library"
        self.site = root / "student-site"
        self.entry_id = "20260719-张同学带电粒子题-private-id"
        self.entry = self.library / "entries" / self.entry_id
        (self.entry / "assets").mkdir(parents=True)
        Image.new("RGB", (320, 220), "white").save(self.entry / "assets" / "original.png")
        (self.entry / "assets" / "explanation.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><text x="1" y="12">受力方向</text></svg>',
            encoding="utf-8",
        )
        write_json(self.entry / "record.json", {
            "title": "带电粒子在磁场中的运动",
            "subject": "高中物理",
            "knowledge_points": ["洛伦兹力", "圆周运动"],
            "source": {"stored_files": ["assets/original.png"]},
        })
        (self.entry / "problem.md").write_text(
            "# 带电粒子在磁场中的运动\n\n![原始题图](assets/original.png)\n\n求粒子的运动半径。\n",
            encoding="utf-8",
        )
        (self.entry / "student-solution.md").write_text(
            "## 解答\n\n![受力示意](assets/explanation.svg)\n\n由 $qvB=mv^2/r$ 得 $r=mv/(qB)$。\n",
            encoding="utf-8",
        )
        (self.entry / "teacher-solution.md").write_text(
            "PRIVATE-TEACHER：课堂上追问学生。\n", encoding="utf-8"
        )
        write_json(self.entry / "pipeline.json", {"state": "delivered"})
        write_json(self.entry / "delivery.json", {
            "status": "delivered",
            "output": str(root / "internal-output"),
            "visualization_review": {"status": "not-required"},
        })

    def tearDown(self):
        self.temp.cleanup()

    def approve_public_image(self, include=True):
        snapshot = public_site.public_image_snapshot(self.entry)
        source = snapshot["sources"][0]
        return public_site.save_public_images(self.entry, [{
            "source_id": source["id"],
            "include": include,
            "crop": [0, 0, 1, 1],
            "redactions": [[0, 0, 0.2, 0.1]],
        }], "teacher", "已脱敏")

    @mock.patch.object(public_site, "_generate_pdf", return_value={"status": "skipped", "reason": "test"})
    def test_prepare_and_publish_excludes_private_material(self, _pdf):
        self.approve_public_image()
        prepared = public_site.prepare_publication(self.library, self.entry_id, self.site)
        public_id = prepared["public_id"]
        draft = self.entry / public_site.DRAFT_DIR
        content = (draft / "questions" / public_id / "content.md").read_text(encoding="utf-8")

        self.assertNotIn("original.png", content)
        self.assertNotIn(self.entry_id, content)
        self.assertNotIn("PRIVATE-TEACHER", content)
        self.assertIn("assets/asset-1.svg", content)
        self.assertIn("assets/question-1.webp", content)
        self.assertFalse((draft / "questions" / public_id / "assets" / "original.png").exists())
        self.assertTrue((draft / "questions" / public_id / "assets" / "question-1.webp").is_file())
        self.assertTrue((draft / "questions" / public_id / "assets" / "asset-1.svg").is_file())
        self.assertTrue(public_id.startswith("question-"))

        review = public_site.publish_prepared(self.library, self.entry_id, "teacher", "隐私已检查", self.site)
        self.assertEqual(review["status"], "published-local")
        self.assertEqual(review["git_status"], "not-pushed")
        self.assertTrue((self.site / "questions" / public_id / "content.md").is_file())
        catalog = json.loads((self.site / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in catalog["questions"]], [public_id])

    @mock.patch.object(public_site, "_generate_pdf", return_value={"status": "skipped", "reason": "test"})
    def test_changed_preview_must_be_prepared_again(self, _pdf):
        self.approve_public_image()
        prepared = public_site.prepare_publication(self.library, self.entry_id, self.site)
        content = self.entry / public_site.DRAFT_DIR / "questions" / prepared["public_id"] / "content.md"
        content.write_text(content.read_text(encoding="utf-8") + "\n被修改\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "重新生成并复核"):
            public_site.publish_prepared(self.library, self.entry_id, "teacher", "", self.site)

    @mock.patch.object(public_site, "_generate_pdf", return_value={"status": "skipped", "reason": "test"})
    def test_private_reference_fails_closed(self, _pdf):
        self.approve_public_image()
        (self.entry / "student-solution.md").write_text(
            "内部文件位于 student-error-library/entries/record.json。\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "private reference"):
            public_site.prepare_publication(self.library, self.entry_id, self.site)

    @mock.patch.object(public_site, "_generate_pdf", return_value={"status": "skipped", "reason": "test"})
    def test_publication_requires_public_image_review(self, _pdf):
        with self.assertRaisesRegex(ValueError, "裁剪、脱敏并确认"):
            public_site.prepare_publication(self.library, self.entry_id, self.site)
        result = self.approve_public_image(include=False)
        self.assertEqual(result["included_count"], 0)
        prepared = public_site.prepare_publication(self.library, self.entry_id, self.site)
        self.assertEqual(prepared["public_question_images"], 0)

    def test_source_image_change_invalidates_public_image_review(self):
        self.approve_public_image()
        self.assertEqual(public_site.public_image_snapshot(self.entry)["status"], "passed")
        Image.new("RGB", (320, 220), "gray").save(self.entry / "assets" / "original.png")
        self.assertEqual(public_site.public_image_snapshot(self.entry)["status"], "stale")

    def test_simulator_public_copy_removes_internal_model_metadata(self):
        source = self.entry / "approved-simulator.html"
        source.write_text(
            '<script type="application/json" id="physics-model-data">'
            + json.dumps({
                "entry_id": self.entry_id,
                "source": {"original_image": "assets/original.png"},
                "teacher_audit": {"note": "PRIVATE-TEACHER"},
                "event_model": {"timeline": [{"id": "P", "order": 0}]},
            }, ensure_ascii=False)
            + '</script><p>答案来自 physics-model.json</p>',
            encoding="utf-8",
        )
        destination = self.entry / "public-simulator.html"
        public_site._copy_public_simulator(source, destination, "question-safe")
        output = destination.read_text(encoding="utf-8")
        self.assertIn('"entry_id":"question-safe"', output)
        self.assertIn('"event_model"', output)
        self.assertNotIn(self.entry_id, output)
        self.assertNotIn("original.png", output)
        self.assertNotIn("teacher_audit", output)
        self.assertNotIn("physics-model.json", output)


if __name__ == "__main__":
    unittest.main()
