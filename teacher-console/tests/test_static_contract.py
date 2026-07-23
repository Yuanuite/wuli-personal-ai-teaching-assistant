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
        self.assertIn("/vendor/marked.min.js", html)
        self.assertIn("/vendor/katex.min.js", html)
        self.assertIn("/vendor/katex.min.css", html)
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
        self.assertIn("Boolean(state.current?.visualization?.has_model)", script)
        self.assertIn('"我想为这道题生成一个可交互的可视化结果。', script)
        self.assertIn("调用 Skill 生成", html)
        self.assertNotIn('visualizationTab.classList.toggle("hidden", !hasModel)', script)
        self.assertNotIn("visualization-static-gallery", css)
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

    def test_retrieval_review_uses_visual_selectable_cards(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        for element_id in (
            "open-retrieval-review",
            "retrieval-review-backdrop",
            "retrieval-case-list",
            "retrieval-candidate-grid",
            "retrieval-selection-count",
            "retrieval-approve",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('api("/api/retrieval-review")', script)
        self.assertIn('api("/api/retrieval-review/save"', script)
        self.assertIn("relevant_entry_ids", script)
        self.assertIn("retrieval-candidate-card", css)
        self.assertIn("retrieval-candidate-visual", css)

    def test_background_agent_job_and_health_contract(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        script = (STATIC / "app.js").read_text(encoding="utf-8")
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        for element_id in (
            "agent-health-detail",
            "agent-model",
            "agent-tier",
            "add-codex-visualization-preset",
            "probe-agent",
            "active-job",
            "active-job-title",
            "active-job-detail",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('role="status" aria-live="polite"', html)
        self.assertIn('result?.status === "queued"', script)
        self.assertIn("function pollActiveJob(jobId, entryId)", script)
        self.assertIn("`/api/jobs/${encodeURIComponent(job.id)}`", script)
        self.assertIn('["completed", "failed"]', script)
        self.assertIn("await refreshAfterJob(finished)", script)
        self.assertIn("data.agent.providers", script)
        self.assertIn("unavailableAgentReason()", script)
        self.assertIn("selectedAgentLocality()", script)
        self.assertIn("数据位置取决于 provider", script)
        self.assertIn('"/api/agent/providers/probe"', script)
        self.assertIn('"/api/agent/model-registry/test"', script)
        self.assertIn("真实连通检测", script)
        self.assertIn("model-probe-status", css)
        self.assertIn("model-untested", css)
        self.assertIn("@keyframes job-spin", css)
        self.assertIn("withRoutingTier", script)
        self.assertIn("selectedAgentModelId", script)
        self.assertIn("applyCodexVisualizationPreset", script)
        self.assertIn("testAgentModel", script)
        self.assertIn("mergeReturnedModelSettings", script)
        self.assertIn("明文 API Key 不回灌到页面", script)
        self.assertIn("codex-visualization", script)
        self.assertIn("model_id", script)
        self.assertIn("routing_tier", script)
        self.assertIn("localStorage.setItem(AGENT_MODEL_KEY", script)
        self.assertIn("localStorage.setItem(AGENT_TIER_KEY", script)
        self.assertIn("validation_errors", script)
        self.assertIn("required_env", script)

    def test_planar_magnetic_renderer_is_registered(self):
        simulator = ROOT / ".claude" / "skills" / "build-physics-simulator"
        builder = (simulator / "scripts" / "build_simulator.py").read_text(encoding="utf-8")
        validator = (simulator / "scripts" / "validate_physics_model.py").read_text(encoding="utf-8")
        skill = (simulator / "SKILL.md").read_text(encoding="utf-8")
        template = simulator / "assets" / "planar-magnetic-template.html"
        self.assertTrue(template.exists())
        self.assertIn('"planar-magnetic-multi-particle"', builder)
        self.assertIn("planar-magnetic-template.html", builder)
        self.assertIn('model_type == "planar-magnetic-multi-particle"', validator)
        self.assertIn("planar-magnetic-multi-particle", skill)

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
