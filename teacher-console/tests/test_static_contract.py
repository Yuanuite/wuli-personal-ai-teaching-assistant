import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "teacher-console" / "static"


class IdCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, _tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])


class StaticWorkbenchContractTest(unittest.TestCase):
    def test_javascript_references_existing_elements(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        parser = IdCollector()
        parser.feed(html)
        referenced = set(re.findall(r'\$\("([a-z0-9-]+)"\)', script))
        self.assertEqual(referenced - parser.ids, set())

    def test_single_viewport_and_local_compilers(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn("height: 100dvh", css)
        self.assertIn("overflow: hidden", css)
        self.assertIn('/vendor/marked.min.js', html)
        self.assertIn('/vendor/katex.min.js', html)
        self.assertIn('/vendor/katex.min.css', html)
        self.assertIn('id="answer-editor"', html)
        self.assertIn('id="solution-view" class="markdown-preview"', html)
        self.assertIn("悟理教师工作台", html)
        for relative in (
            "vendor/marked.min.js",
            "vendor/katex.min.js",
            "vendor/katex.min.css",
            "vendor/fonts/KaTeX_Main-Regular.woff2",
        ):
            self.assertGreater((STATIC / relative).stat().st_size, 1000)

    def test_folder_visualization_and_delivery_contract(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn('id="entry-list"', html)
        self.assertIn('data-tab="visualization"', html)
        self.assertIn('id="visualization-frame"', html)
        self.assertIn('sandbox="allow-scripts"', html)
        self.assertIn('id="visualization-conversation"', html)
        self.assertIn('id="clear-visualization-chat"', html)
        self.assertNotIn('id="visualization-static-gallery"', html)
        self.assertIn('id="delivery-guide-list"', html)
        self.assertIn('"/api/folders/rename"', script)
        self.assertIn('"build-visualization"', script)
        self.assertIn('"approve-visualization"', script)
        self.assertIn('"visualization-chat"', script)
        self.assertIn('"clear-visualization-chat"', script)
        self.assertIn('Boolean(state.current?.visualization?.has_model)', script)
        self.assertIn('"我想为这道题生成一个可交互的可视化结果。', script)
        self.assertIn("调用 Skill 生成", html)
        self.assertNotIn('visualizationTab.classList.toggle("hidden", !hasModel)', script)
        self.assertNotIn('visualization-static-gallery', css)
        self.assertIn("repeat(7, 1fr)", css)

    def test_review_navigation_and_feedback_contract(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("交给大模型修改", html)
        self.assertIn("解释图", html)
        self.assertIn('setTimeout(() => element.classList.add("hidden"), 1700)', script)
        self.assertIn('activateTab("answer", { force: true })', script)
        self.assertIn('activateTab("visualization", { force: true })', script)
        self.assertIn('activateTab("delivery", { force: true })', script)
        self.assertIn("请先在“题干复核”确认题干无误", script)
        self.assertIn("后台还没有完整解析", script)
        self.assertIn("请先在“解析复核”确认答案正确", script)

    def test_publication_gate_and_static_student_site_contract(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        student = ROOT / "student-site"
        student_index = (student / "index.html").read_text(encoding="utf-8")
        student_html = (student / "viewer.html").read_text(encoding="utf-8")
        student_script = (student / "assets" / "site.js").read_text(encoding="utf-8")
        for element_id in (
            "publication-preview-frame",
            "publication-privacy-confirmed",
            "publication-image-canvas",
            "publication-image-confirmed",
            "save-publication-images",
            "prepare-publication",
            "publish-publication",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('sandbox="allow-scripts allow-same-origin allow-popups allow-downloads"', html)
        self.assertIn('"prepare-publication"', script)
        self.assertIn('"save-publication-images"', script)
        self.assertIn('"publish-publication"', script)
        self.assertIn("privacy_confirmed: true", script)
        self.assertIn("不会自动推送 GitHub", html)
        self.assertIn("下载 PDF", student_html)
        self.assertIn("打开交互演示", student_html)
        self.assertIn("悟理学习站", student_index)
        self.assertIn("悟理学习站", student_html)
        self.assertIn("悟理学习站", student_script)
        self.assertIn('fetch("catalog.json"', student_script)
        self.assertNotIn("/api/", student_script)
        self.assertNotIn("teacher-solution", student_script)


if __name__ == "__main__":
    unittest.main()
