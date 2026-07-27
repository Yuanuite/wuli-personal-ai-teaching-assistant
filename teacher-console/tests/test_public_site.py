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
        write_json(
            self.entry / "record.json",
            {
                "title": "带电粒子在磁场中的运动",
                "subject": "高中物理",
                "knowledge_points": ["洛伦兹力", "圆周运动"],
                "created_at": "2026-07-19T09:00:00+08:00",
                "source": {"stored_files": ["assets/original.png"]},
            },
        )
        (self.entry / "problem.md").write_text(
            f"# 带电粒子在磁场中的运动\n\n题目编号：`{self.entry_id}`\n\n![原始题图](assets/original.png)\n\n求粒子的运动半径。\n",
            encoding="utf-8",
        )
        (self.entry / "student-solution.md").write_text(
            "## 解答\n\n![受力示意](assets/explanation.svg)\n\n由 $qvB=mv^2/r$ 得 $r=mv/(qB)$。\n",
            encoding="utf-8",
        )
        (self.entry / "teacher-solution.md").write_text("PRIVATE-TEACHER：课堂上追问学生。\n", encoding="utf-8")
        write_json(self.entry / "pipeline.json", {"state": "delivered"})
        write_json(
            self.entry / "delivery.json",
            {
                "status": "delivered",
                "output": str(root / "internal-output"),
                "visualization_review": {"status": "not-required"},
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def approve_public_image(self, include=True):
        snapshot = public_site.public_image_snapshot(self.entry)
        source = snapshot["sources"][0]
        return public_site.save_public_images(
            self.entry,
            [
                {
                    "source_id": source["id"],
                    "include": include,
                    "crop": [0, 0, 1, 1],
                    "redactions": [[0, 0, 0.2, 0.1]],
                }
            ],
            "teacher",
            "已脱敏",
        )

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
        self.assertEqual(catalog["questions"][0]["uploaded_at"], "2026-07-19")

    @mock.patch.object(public_site, "_generate_pdf", return_value={"status": "skipped", "reason": "test"})
    def test_catalog_keeps_default_order_by_upload_time(self, _pdf):
        self.approve_public_image()
        prepared = public_site.prepare_publication(self.library, self.entry_id, self.site)
        write_json(
            self.site / "catalog.json",
            {
                "schema_version": 1,
                "questions": [
                    {
                        "id": "question-newer-upload",
                        "title": "较晚上传、较早发布",
                        "uploaded_at": "2026-07-20T09:00:00+08:00",
                        "published_at": "2026-07-18T09:00:00+08:00",
                    }
                ],
            },
        )
        public_site.publish_prepared(self.library, self.entry_id, "teacher", "隐私已检查", self.site)
        catalog = json.loads((self.site / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [item["id"] for item in catalog["questions"]],
            ["question-newer-upload", prepared["public_id"]],
        )

    def test_sync_published_difficulty_changes_metadata_only(self):
        public_id = public_site.public_id(self.entry)
        question = self.site / "questions" / public_id
        question.mkdir(parents=True)
        (question / "content.md").write_text("公开内容保持不变。\n", encoding="utf-8")
        write_json(
            self.entry / public_site.REVIEW_RECORD,
            {"status": "published-local", "public_id": public_id, "reviewer": "teacher"},
        )
        record = json.loads((self.entry / "record.json").read_text(encoding="utf-8"))
        record["difficulty_assessment"] = {
            "score": 78,
            "level": "较难",
            "summary": "较难：需要多过程建模。",
            "dimensions": [
                {
                    "id": "process_state_complexity",
                    "label": "过程与状态复杂度",
                    "score": 4.2,
                    "core_judgment": "需要组织多个连续状态。",
                    "private_evidence": "不得公开",
                }
            ],
            "standard_path_digest": "private-digest",
        }
        write_json(self.entry / "record.json", record)
        write_json(
            self.site / "catalog.json",
            {
                "schema_version": 1,
                "generated_at": "old",
                "questions": [
                    {
                        "id": public_id,
                        "title": "公开标题",
                        "content": f"questions/{public_id}/content.md",
                        "published_at": "2026-07-20T09:00:00+08:00",
                        "difficulty": {"score": 46, "level": "中等"},
                    }
                ],
            },
        )

        result = public_site.sync_published_difficulties(self.library, self.site)

        self.assertEqual(result["updated"], 1)
        catalog = json.loads((self.site / "catalog.json").read_text(encoding="utf-8"))
        item = catalog["questions"][0]
        self.assertEqual(item["difficulty"]["score"], 78)
        self.assertEqual(item["published_at"], "2026-07-20T09:00:00+08:00")
        self.assertEqual((question / "content.md").read_text(encoding="utf-8"), "公开内容保持不变。\n")
        self.assertNotIn("private_evidence", json.dumps(item, ensure_ascii=False))
        self.assertNotIn("private-digest", json.dumps(item, ensure_ascii=False))

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
    def test_public_svg_replaces_internal_entry_id(self, _pdf):
        self.approve_public_image()
        source = self.entry / "assets" / "explanation.svg"
        source.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg"><text>{self.entry_id}</text></svg>',
            encoding="utf-8",
        )
        prepared = public_site.prepare_publication(self.library, self.entry_id, self.site)
        public_id = prepared["public_id"]
        copied = (
            self.entry / public_site.DRAFT_DIR / "questions" / public_id / "assets" / "asset-1.svg"
        ).read_text(encoding="utf-8")

        self.assertNotIn(self.entry_id, copied)
        self.assertIn(public_id, copied)

    def test_svg_allowlist_accepts_passive_local_styles_and_fragments(self):
        source = self.entry / "assets" / "safe.svg"
        source.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
            "<defs><style>.line{stroke:#123456;fill:none}</style>"
            '<marker id="tip"><path d="M0 0L3 1L0 2Z"/></marker></defs>'
            '<line class="line" x1="1" y1="1" x2="18" y2="18" marker-end="url(#tip)"/>'
            "</svg>",
            encoding="utf-8",
        )
        public_site._safe_svg(source)

    def test_svg_allowlist_rejects_active_or_external_content(self):
        malicious = {
            "script.svg": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            "event.svg": '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
            "foreign.svg": (
                '<svg xmlns="http://www.w3.org/2000/svg">'
                '<foreignObject><p xmlns="http://www.w3.org/1999/xhtml">x</p></foreignObject></svg>'
            ),
            "xlink.svg": (
                '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
                '<use xlink:href="https://example.com/a.svg#x"/></svg>'
            ),
            "encoded.svg": (
                '<svg xmlns="http://www.w3.org/2000/svg">'
                '<a href="jav&#x61;script:alert(1)"><text>x</text></a></svg>'
            ),
            "external-css.svg": (
                '<svg xmlns="http://www.w3.org/2000/svg"><style>'
                ".x{fill:url(https://example.com/a.svg#x)}</style></svg>"
            ),
            "entity.svg": (
                '<!DOCTYPE svg [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'
                '<svg xmlns="http://www.w3.org/2000/svg"><text>&leak;</text></svg>'
            ),
            "animation.svg": (
                '<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="href" '
                'values="safe;javascript:alert(1)"/></svg>'
            ),
        }
        for name, content in malicious.items():
            with self.subTest(name=name):
                source = self.entry / "assets" / name
                source.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    public_site._safe_svg(source)

    def test_svg_allowlist_rejects_oversized_and_deep_documents(self):
        oversized = self.entry / "assets" / "oversized.svg"
        oversized.write_bytes(b"<svg>" + b"x" * public_site.SVG_MAX_BYTES + b"</svg>")
        with self.assertRaisesRegex(ValueError, "too large"):
            public_site._safe_svg(oversized)

        deep = self.entry / "assets" / "deep.svg"
        deep.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            + "<g>" * (public_site.SVG_MAX_DEPTH + 1)
            + "</g>" * (public_site.SVG_MAX_DEPTH + 1)
            + "</svg>",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "complexity"):
            public_site._safe_svg(deep)

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

    def test_public_catalog_uses_student_answer_pdf_name(self):
        self.approve_public_image()

        def fake_generate_pdf(question_dir):
            (question_dir / public_site.PUBLIC_PDF_NAME).write_bytes(b"%PDF-1.4\n%test\n")
            return {"status": "generated", "file": public_site.PUBLIC_PDF_NAME, "size": 15}

        with mock.patch.object(public_site, "_generate_pdf", side_effect=fake_generate_pdf):
            prepared = public_site.prepare_publication(self.library, self.entry_id, self.site)
        catalog = json.loads((self.entry / public_site.DRAFT_DIR / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(
            catalog["questions"][0]["pdf"], f"questions/{prepared['public_id']}/{public_site.PUBLIC_PDF_NAME}"
        )
        self.assertTrue(
            (
                self.entry / public_site.DRAFT_DIR / "questions" / prepared["public_id"] / public_site.PUBLIC_PDF_NAME
            ).is_file()
        )

    def test_public_pdf_falls_back_to_reportlab_for_webp_and_svg(self):
        question_dir = self.entry / "public-question"
        assets = question_dir / "assets"
        assets.mkdir(parents=True)
        Image.new("RGB", (320, 180), "white").save(assets / "question-1.webp")
        (assets / "asset-1.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="120">'
            '<rect width="320" height="120" fill="white"/>'
            '<text x="20" y="60" font-size="24">洛伦兹力示意</text>'
            "</svg>",
            encoding="utf-8",
        )
        (question_dir / "content.md").write_text(
            "# 带电粒子题\n\n![公开题图](assets/question-1.webp)\n\n## 详细解答\n\n由 $qvB=mv^2/r$ 得半径。\n\n![示意图](assets/asset-1.svg)\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            public_site.pdf_export, "_try_pandoc", return_value={"status": "skipped", "reason": "forced"}
        ):
            result = public_site._generate_pdf(question_dir)
        self.assertEqual(result["status"], "generated")
        self.assertEqual(result["engine"], "reportlab")
        self.assertEqual(result["file"], public_site.PUBLIC_PDF_NAME)
        self.assertTrue((question_dir / public_site.PUBLIC_PDF_NAME).is_file())
        self.assertFalse((question_dir / "answer.pdf").exists())
        self.assertGreater((question_dir / public_site.PUBLIC_PDF_NAME).stat().st_size, 1000)

    def test_reportlab_fallback_preserves_latex_markdown(self):
        text = public_site.pdf_export._clean_markdown_inline("由 $x=\\frac{mv_0}{qB}$ 得半径。")
        self.assertIn("$x=\\frac{mv_0}{qB}$", text)

    def test_simulator_public_copy_removes_internal_model_metadata(self):
        source = self.entry / "approved-simulator.html"
        source.write_text(
            '<script type="application/json" id="physics-model-data">'
            + json.dumps(
                {
                    "schema_version": 1,
                    "model_type": "planar-magnetic-multi-particle",
                    "entry_id": self.entry_id,
                    "source": {"original_image": "assets/original.png"},
                    "teacher_audit": {"note": "PRIVATE-TEACHER"},
                    "event_model": {"timeline": [{"id": "P", "order": 0}]},
                },
                ensure_ascii=False,
            )
            + "</script><p>答案来自 physics-model.json</p>",
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
