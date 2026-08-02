import json
import tempfile
import unittest
from pathlib import Path

import diagram_plugins
import physics_diagram
import physics_diagram_assets
import svg_collaboration
from visual_facts import normalize_payload


def visual_facts():
    raw = {
        "schema": "wuli.visual-facts.v1",
        "source_fingerprint": "sha256:" + "a" * 64,
        "reviewed_text": "带电粒子进入垂直纸面向里的匀强磁场",
        "printed_facts": ["粒子从 P 点射入磁场"],
        "diagram_facts": [
            {"id": "field", "kind": "region", "statement": "右侧为垂直纸面向里的磁场", "confidence": 0.95},
            {"id": "point-p", "kind": "object", "statement": "P 为入射点", "confidence": 0.9},
        ],
        "handwriting": [],
        "uncertainties": [],
        "model_identity": {"model_id": "mimo-v2.5-flash", "provider": "openai-compatible"},
    }
    return normalize_payload(raw, raw["source_fingerprint"])


def scene_payload():
    return {
        "status": "completed", "message": "ok", "title": "带电粒子在磁场中的偏转",
        "panels": [{"id": "main", "title": "俯视图"}],
        "regions": [{"id": "b", "panel_id": "main", "kind": "magnetic", "x": 30, "y": 12, "width": 62, "height": 72, "label": "B 垂直纸面向里", "fact_ids": ["field"], "direction": "into-page"}],
        "objects": [{"id": "p", "panel_id": "main", "kind": "point", "x": 27, "y": 62, "width": 4, "height": 5, "label": "P", "fact_ids": ["point-p"], "polarity": "none"}],
        "paths": [{"id": "track", "panel_id": "main", "kind": "trajectory", "geometry": "smooth", "points": [{"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 48, "y": 55}, {"x": 58, "y": 35}], "label": "轨迹", "direction": "up", "fact_ids": []}],
        "annotations": [], "omissions": [],
    }


class PhysicsDiagramTests(unittest.TestCase):
    def test_obligation_compiler_reserves_three_views_for_periodic_spatial_problem(self):
        obligations = physics_diagram.build_obligations(
            visual_facts(),
            "带电粒子在周期性交替电场和磁场中运动，二者都垂直纸面向里并同时作用。",
        )
        self.assertEqual([view["id"] for view in obligations["views"]], [
            "motion", "b-time", "e-time"
        ])
        self.assertIn(
            "spatial-projection-label",
            obligations["views"][0]["required_content"],
        )
        self.assertEqual(obligations["max_panels"], 3)
        self.assertIn("trajectory-segment", obligations["component_catalog"]["selected_component_ids"])
        self.assertIn("square-wave", obligations["component_catalog"]["selected_component_ids"])
        self.assertEqual(
            obligations["responsibility_split"]["compiler"],
            ["physics-model-trajectories", "event-continuity", "boundary-order", "coordinates"],
        )
        self.assertTrue(obligations["fingerprint"].startswith("sha256:"))

    def test_typed_scene_passes_semantic_gate_and_renders_safe_svg(self):
        scene = physics_diagram.normalize_scene(scene_payload())
        gate = physics_diagram.semantic_gate(scene, visual_facts(), "带电粒子进入磁场")
        self.assertEqual(gate["status"], "passed")
        svg = physics_diagram.render_svg(scene)
        self.assertIn("field-cross", svg)
        self.assertIn("偏转", scene["title"])
        self.assertEqual(svg_collaboration.validate_svg_safety(svg)["status"], "passed")

    def test_gate_rejects_missing_high_confidence_fact_and_trajectory(self):
        raw = scene_payload()
        raw["regions"][0]["fact_ids"] = []
        raw["objects"][0]["fact_ids"] = []
        raw["paths"][0]["kind"] = "connector"
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(raw), visual_facts(), "带电粒子进入磁场"
        )
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(any("not drawn" in error for error in gate["errors"]))
        self.assertTrue(any("requires a trajectory" in error for error in gate["errors"]))

    def test_renderer_escapes_model_text_and_is_deterministic(self):
        raw = scene_payload()
        raw["annotations"] = [{"id": "a", "panel_id": "main", "x": 50, "y": 90, "text": "<script>alert(1)</script>", "fact_ids": []}]
        scene = physics_diagram.normalize_scene(raw)
        first = physics_diagram.render_svg(scene)
        second = physics_diagram.render_svg(scene)
        self.assertEqual(first, second)
        self.assertNotIn("<script>", first)
        self.assertIn("&lt;script&gt;", first)

    def test_materializer_writes_scene_gate_svg_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory)
            (entry / "problem.md").write_text("带电粒子进入磁场并发生偏转。", encoding="utf-8")
            (entry / "visual-facts.json").write_text(json.dumps(visual_facts(), ensure_ascii=False), encoding="utf-8")
            result = physics_diagram.materialize(
                entry,
                scene_payload(),
                model_config={"id": "deepseek-v4-flash-api", "provider": "openai-compatible"},
            )
            self.assertEqual(result["gate"]["status"], "passed")
            for relative in (physics_diagram.SCENE_PATH, physics_diagram.GATE_PATH, physics_diagram.SVG_PATH, physics_diagram.PROVENANCE_PATH):
                self.assertTrue((entry / relative).is_file())

    def test_materializer_preserves_rejected_candidate_with_structured_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory)
            (entry / "problem.md").write_text("带电粒子进入磁场。", encoding="utf-8")
            (entry / "visual-facts.json").write_text(
                json.dumps(visual_facts(), ensure_ascii=False), encoding="utf-8"
            )
            raw = scene_payload()
            raw["panels"].extend([
                {"id": "extra-1", "title": "附图一"},
                {"id": "extra-2", "title": "附图二"},
                {"id": "extra-3", "title": "附图三"},
            ])
            result = physics_diagram.materialize(
                entry,
                raw,
                model_config={"id": "deepseek-v4-flash-api", "provider": "openai-compatible"},
            )
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(
                result["diagnostic_report"]["diagnostics"][0]["code"],
                "scene.panel-count",
            )
            self.assertEqual(result["diagnostic_report"]["diagnostics"][0]["path"], "/panels")
            self.assertEqual(result["diagnostic_report"]["diagnostics"][0]["actual"], 4)
            self.assertTrue((entry / physics_diagram.REJECTED_SCENE_PATH).is_file())
            self.assertTrue((entry / physics_diagram.DIAGNOSTICS_PATH).is_file())
            self.assertFalse((entry / physics_diagram.SVG_PATH).is_file())

    def test_scene_patch_repairs_only_targeted_field_and_has_no_progress_fuse(self):
        raw = scene_payload()
        raw["paths"][0]["geometry"] = "circular-arc"
        raw["paths"][0]["points"] = [
            {"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 50, "y": 64}
        ]
        patch = {
            "status": "completed",
            "message": "修复圆弧中间点",
            "patches": [{
                "op": "replace",
                "path": "/paths/0/points/1/y",
                "value": 52,
            }],
        }
        repaired = physics_diagram.apply_scene_patches(raw, patch)
        self.assertEqual(repaired["paths"][0]["points"][1]["y"], 52)
        self.assertEqual(repaired["regions"], raw["regions"])
        no_progress = {
            **patch,
            "patches": [{
                "op": "replace", "path": "/paths/0/points/1/y", "value": 64
            }],
        }
        with self.assertRaisesRegex(ValueError, "no progress"):
            physics_diagram.apply_scene_patches(raw, no_progress)

    def test_preflight_reports_all_degenerate_arcs_in_one_snapshot(self):
        raw = scene_payload()
        raw["paths"] = [
            {
                **raw["paths"][0],
                "id": f"arc-{index}",
                "geometry": "circular-arc",
                "points": [
                    {"x": 10, "y": 10 + index},
                    {"x": 20, "y": 20 + index},
                    {"x": 30, "y": 30 + index},
                ],
            }
            for index in range(2)
        ]
        diagnostics = physics_diagram._aggregate_preflight_diagnostics(raw)
        arc_diagnostics = [
            item for item in diagnostics if item["code"] == "scene.circular-arc-degenerate"
        ]
        self.assertEqual([item["path"] for item in arc_diagnostics], [
            "/paths/0/points", "/paths/1/points"
        ])

    def test_materializer_stops_on_structural_panel_error_without_soft_gate_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory)
            (entry / "problem.md").write_text(
                "带电粒子在周期性交替电场和磁场中运动。", encoding="utf-8"
            )
            (entry / "visual-facts.json").write_text(
                json.dumps(visual_facts(), ensure_ascii=False), encoding="utf-8"
            )
            raw = scene_payload()
            raw["panels"].extend([
                {"id": "p2", "title": "图二"},
                {"id": "p3", "title": "图三"},
                {"id": "p4", "title": "图四"},
            ])
            result = physics_diagram.materialize(
                entry, raw,
                model_config={"id": "deepseek-v4-flash-api", "provider": "openai-compatible"},
            )
        codes = [item["code"] for item in result["diagnostic_report"]["diagnostics"]]
        messages = [item["message"] for item in result["diagnostic_report"]["diagnostics"]]
        self.assertIn("scene.panel-count", codes)
        self.assertFalse(any("axis" in message for message in messages))
        self.assertFalse(any("field-line" in message for message in messages))

    def test_patch_cannot_escape_scene_repair_boundary(self):
        patch = {
            "status": "completed",
            "message": "越界修改",
            "patches": [{"op": "replace", "path": "/status", "value": "unsupported"}],
        }
        with self.assertRaisesRegex(ValueError, "outside the scene repair boundary"):
            physics_diagram.apply_scene_patches(scene_payload(), patch)

    def test_path_geometry_validation(self):
        base = scene_payload()
        # line requires exactly 2 points
        raw = json.loads(json.dumps(base))
        raw["paths"][0]["geometry"] = "line"
        with self.assertRaises(ValueError):
            physics_diagram.normalize_scene(raw)
        raw["paths"][0]["points"] = [{"x": 10, "y": 64}, {"x": 30, "y": 64}]
        self.assertEqual(physics_diagram.normalize_scene(raw)["paths"][0]["geometry"], "line")
        # polyline requires at least 2 points
        raw = json.loads(json.dumps(base))
        raw["paths"][0]["geometry"] = "polyline"
        raw["paths"][0]["points"] = [{"x": 10, "y": 64}]
        with self.assertRaises(ValueError):
            physics_diagram.normalize_scene(raw)
        # smooth requires at least 3 points
        raw = json.loads(json.dumps(base))
        raw["paths"][0]["geometry"] = "smooth"
        raw["paths"][0]["points"] = [{"x": 10, "y": 64}, {"x": 30, "y": 64}]
        with self.assertRaises(ValueError):
            physics_diagram.normalize_scene(raw)
        # circular-arc requires exactly 3 distinct non-collinear points
        raw = json.loads(json.dumps(base))
        raw["paths"][0]["geometry"] = "circular-arc"
        raw["paths"][0]["points"] = [{"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 50, "y": 64}]
        with self.assertRaises(ValueError):
            physics_diagram.normalize_scene(raw)
        raw["paths"][0]["points"] = [{"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 50, "y": 70}]
        self.assertEqual(physics_diagram.normalize_scene(raw)["paths"][0]["geometry"], "circular-arc")

    def test_renderer_emits_geometry_specific_svg_markers(self):
        import copy
        base = scene_payload()
        cases = [
            ("line", [{"x": 10, "y": 64}, {"x": 30, "y": 64}], "<line x1="),
            ("polyline", [{"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 50, "y": 70}], "<polyline points="),
            ("smooth", [{"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 48, "y": 55}, {"x": 58, "y": 35}], " Q "),
            ("circular-arc", [{"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 50, "y": 70}], " A "),
        ]
        for geometry, points, marker in cases:
            raw = copy.deepcopy(base)
            raw["paths"][0]["geometry"] = geometry
            raw["paths"][0]["points"] = points
            scene = physics_diagram.normalize_scene(raw)
            svg = physics_diagram.render_svg(scene)
            self.assertIn(marker, svg)

    def test_circular_arc_rendering_is_deterministic(self):
        import copy
        raw = copy.deepcopy(scene_payload())
        raw["paths"][0]["geometry"] = "circular-arc"
        raw["paths"][0]["points"] = [{"x": 10, "y": 64}, {"x": 30, "y": 64}, {"x": 50, "y": 70}]
        scene = physics_diagram.normalize_scene(raw)
        first = physics_diagram.render_svg(scene)
        second = physics_diagram.render_svg(scene)
        self.assertEqual(first, second)

    def test_circular_arc_path_flags_for_unit_circle(self):
        import math
        points = [(1.0, 0.0), (math.cos(math.pi / 4), math.sin(math.pi / 4)), (0.0, 1.0)]
        self.assertIn("0 0 1", physics_diagram._circular_arc_path(points))
        points = [(1.0, 0.0), (math.cos(5 * math.pi / 4), math.sin(5 * math.pi / 4)), (0.0, 1.0)]
        self.assertIn("0 1 0", physics_diagram._circular_arc_path(points))

    def test_circular_arc_path_rejects_collinear_points(self):
        with self.assertRaises(ValueError):
            physics_diagram._circular_arc_path([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])

    def test_estimated_text_width_mixed_ascii_and_cjk(self):
        self.assertAlmostEqual(physics_diagram._estimated_text_width("AB中"), 2 * 0.56 * 13 + 13)

    def test_place_label_within_canvas_margins_for_start_and_middle(self):
        occupied: list[tuple[float, float, float, float]] = []
        for anchor in ("start", "middle"):
            physics_diagram._place_label("测试", 100.0, 50.0, occupied, 200.0, 100.0, anchor=anchor)
            self.assertTrue(all(
                box[0] >= 4.0 and box[1] >= 4.0 and box[2] <= 196.0 and box[3] <= 96.0
                for box in occupied
            ))

    def test_place_label_avoids_overlap_with_two_px_separation(self):
        occupied: list[tuple[float, float, float, float]] = []
        first = physics_diagram._place_label("甲", 100.0, 50.0, occupied, 200.0, 100.0)
        second = physics_diagram._place_label("乙", 102.0, 50.0, occupied, 200.0, 100.0)
        self.assertNotEqual(first, second)
        box1, box2 = occupied
        gap_x = max(box1[0] - box2[2], box2[0] - box1[2], 0.0)
        gap_y = max(box1[1] - box2[3], box2[1] - box1[3], 0.0)
        self.assertGreaterEqual(gap_x + gap_y, 4.0)

    def test_render_svg_contains_reduced_weight_marker_and_patterns(self):
        svg = physics_diagram.render_svg(physics_diagram.normalize_scene(scene_payload()))
        self.assertIn('markerWidth="8"', svg)
        self.assertIn('M0,0 L8,3 L0,6 Z', svg)
        self.assertIn('width="30" height="30"', svg)
        self.assertIn('r="1.8"', svg)

    def test_place_label_rejects_unsupported_anchor(self):
        with self.assertRaises(ValueError):
            physics_diagram._place_label("测试", 10.0, 10.0, [], 200.0, 100.0, anchor="end")

    def test_periodic_field_missing_axis_and_wave_is_soft_feedback(self):
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(scene_payload()), visual_facts(), "周期性交替电场和磁场"
        )
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["teaching_signals"]["periodic_field"])
        self.assertTrue(any("axis" in warning for warning in gate["warnings"]))
        self.assertTrue(any("field-line" in warning for warning in gate["warnings"]))
        self.assertFalse(gate["soft_revision"]["blocking"])
        self.assertEqual(gate["soft_revision"]["max_rounds"], 1)
        self.assertEqual(gate["soft_revision"]["visual_review_status"], "not-run")
        self.assertFalse(gate["soft_revision"]["quality_approved"])
        self.assertEqual(gate["soft_revision"]["prompts"], gate["warnings"])

    def test_periodic_field_positive_passes_with_axes_and_waves(self):
        import copy
        raw = copy.deepcopy(scene_payload())
        raw["panels"].extend([{"id": "bt", "title": "B-t 图"}, {"id": "et", "title": "E-t 图"}])
        raw["paths"].extend([
            {"id": "axis-bt", "panel_id": "bt", "kind": "axis", "geometry": "line", "points": [{"x": 10, "y": 80}, {"x": 90, "y": 80}], "label": "t", "direction": "right", "fact_ids": []},
            {"id": "axis-b", "panel_id": "bt", "kind": "axis", "geometry": "line", "points": [{"x": 10, "y": 80}, {"x": 10, "y": 20}], "label": "B", "direction": "up", "fact_ids": []},
            {"id": "axis-et", "panel_id": "et", "kind": "axis", "geometry": "line", "points": [{"x": 10, "y": 80}, {"x": 90, "y": 80}], "label": "t", "direction": "right", "fact_ids": []},
            {"id": "axis-e", "panel_id": "et", "kind": "axis", "geometry": "line", "points": [{"x": 10, "y": 80}, {"x": 10, "y": 20}], "label": "E", "direction": "up", "fact_ids": []},
            {"id": "wave-b", "panel_id": "bt", "kind": "field-line", "geometry": "polyline", "points": [{"x": 10, "y": 50}, {"x": 30, "y": 30}, {"x": 50, "y": 50}, {"x": 70, "y": 70}], "label": "B(t)", "direction": "none", "fact_ids": []},
            {"id": "wave-e", "panel_id": "et", "kind": "field-line", "geometry": "polyline", "points": [{"x": 10, "y": 50}, {"x": 30, "y": 70}, {"x": 50, "y": 50}, {"x": 70, "y": 30}], "label": "E(t)", "direction": "none", "fact_ids": []},
        ])
        raw["annotations"].extend([
            {"id": "phase-b", "panel_id": "bt", "x": 50, "y": 15, "text": "T_B", "fact_ids": []},
            {"id": "phase-e", "panel_id": "et", "x": 50, "y": 15, "text": "T_E", "fact_ids": []},
        ])
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(raw), visual_facts(), "周期性交替电场和磁场"
        )
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["teaching_signals"]["axis_count"], 4)
        self.assertEqual(gate["teaching_signals"]["field_wave_count"], 2)

    def test_spatial_projection_missing_axes_and_corpus_is_soft_feedback(self):
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(scene_payload()), visual_facts(),
            "电场和磁场都垂直纸面向里并同时作用",
        )
        self.assertEqual(gate["status"], "passed")
        self.assertTrue(gate["teaching_signals"]["spatial_projection"])
        self.assertTrue(any("axis" in warning for warning in gate["warnings"]))
        self.assertTrue(any("投影" in warning or "空间" in warning for warning in gate["warnings"]))

    def test_source_gate_rejects_fact_bound_and_omitted_at_once(self):
        raw = scene_payload()
        raw["omissions"] = [{"fact_id": "field", "reason": "不应与绑定并存"}]
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(raw), visual_facts(), "带电粒子进入磁场"
        )
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(any("both drawn and omitted" in item for item in gate["errors"]))

    def test_topology_gate_rejects_reversed_upper_and_lower_plates(self):
        facts = visual_facts()
        facts["diagram_facts"].append({
            "id": "plates", "kind": "label", "statement": "上板为P，下板为Q", "confidence": 0.9,
        })
        facts.pop("fingerprint", None)
        facts = normalize_payload(facts, facts["source_fingerprint"])
        raw = scene_payload()
        raw["objects"].extend([
            {"id": "plate-p", "panel_id": "main", "kind": "plate", "x": 10, "y": 80, "width": 80, "height": 4, "label": "P", "fact_ids": ["plates"], "polarity": "none"},
            {"id": "plate-q", "panel_id": "main", "kind": "plate", "x": 10, "y": 15, "width": 80, "height": 4, "label": "Q", "fact_ids": ["plates"], "polarity": "none"},
        ])
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(raw), facts, "带电粒子从Q射向P"
        )
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(gate["hard_gate_categories"]["topology-consistency"])

    def test_model_compiler_replaces_freehand_trajectory_and_corrects_plate_order(self):
        raw = scene_payload()
        raw["panels"][0] = {"id": "motion", "title": "运动"}
        for collection in ("regions", "objects", "paths", "annotations"):
            for item in raw[collection]:
                item["panel_id"] = "motion"
        raw["objects"].extend([
            {"id": "plate-p", "panel_id": "motion", "kind": "plate", "x": 10, "y": 80, "width": 80, "height": 4, "label": "P", "fact_ids": [], "polarity": "none"},
            {"id": "plate-q", "panel_id": "motion", "kind": "plate", "x": 10, "y": 15, "width": 80, "height": 4, "label": "Q", "fact_ids": [], "polarity": "none"},
        ])
        model = {
            "regions": [
                {"id": "plate-q", "label": "Q 板", "field": {"kind": "boundary"}, "shape": {"origin": [0, 0, 0]}},
                {"id": "plate-p", "label": "P 板", "field": {"kind": "boundary"}, "shape": {"origin": [0, 1, 0]}},
            ],
            "event_model": {"timeline": [
                {"id": "case-start", "position": [0, 0, 0]},
                {"id": "case-hit-p", "position": [0.5, 1, 0]},
            ]},
            "trajectory": {"segments": [{
                "id": "case-segment", "label": "模型轨迹", "start_event": "case-start", "end_event": "case-hit-p",
                "geometry": {"path_kind": "points", "points": [[0, 0, 0], [0.2, 0.5, 0], [0.5, 1, 0]]},
            }]},
        }
        scene = physics_diagram.normalize_scene(raw)
        compiled, report = physics_diagram_assets.compile_model_scene(scene, model)
        compiled = physics_diagram.normalize_scene({key: value for key, value in compiled.items() if key != "schema"})
        trajectories = [item for item in compiled["paths"] if item["kind"] == "trajectory"]
        self.assertEqual([item["id"] for item in trajectories], ["case-segment"])
        plates = {item["label"]: item for item in compiled["objects"] if item["kind"] == "plate"}
        self.assertLess(plates["P"]["y"], plates["Q"]["y"])
        gate = physics_diagram.semantic_gate(compiled, visual_facts(), "带电粒子进入磁场", model, compilation=report)
        self.assertEqual(gate["status"], "passed")

    def test_model_compiler_preserves_circle_scale_and_adds_geometry_aids(self):
        raw = scene_payload()
        raw["panels"][0] = {"id": "motion", "title": "运动"}
        for collection in ("regions", "objects", "paths", "annotations"):
            for item in raw[collection]:
                item["panel_id"] = "motion"
        model = {
            "event_model": {"timeline": [
                {"id": "p1-start", "label": "从 Q 板水平射入", "position": [0, 0, 0]},
                {"id": "p1-b-switch", "label": "磁场反向", "position": [1, 1, 0]},
            ]},
            "trajectory": {"segments": [{
                "id": "p1-arc", "case_ids": ["part1"], "label": "+B₀：转过 90°",
                "start_event": "p1-start", "end_event": "p1-b-switch",
                "geometry": {"path_kind": "arc3d", "center": [0, 1, 0], "basis_u": [1, 0, 0], "basis_v": [0, 1, 0], "radius": 1, "start_deg": -90, "end_deg": 0},
            }]},
        }
        compiled, report = physics_diagram_assets.compile_model_scene(
            physics_diagram.normalize_scene(raw), model
        )
        compiled = physics_diagram.normalize_scene({key: value for key, value in compiled.items() if key != "schema"})
        trajectory = next(item for item in compiled["paths"] if item["kind"] == "trajectory")
        self.assertEqual(trajectory["geometry"], "circular-arc")
        self.assertEqual(trajectory["label"], "（1） +B₀ 90°")
        self.assertEqual(trajectory["direction"], "right")
        self.assertTrue(any(item["kind"] == "dimension" for item in compiled["paths"]))
        self.assertTrue(any(item["label"] == "O1" for item in compiled["objects"]))
        self.assertTrue(any(item["label"] == "(1) B换" for item in compiled["objects"]))
        self.assertTrue(report["geometry_aid_ids"])

    def test_sampled_circular_projection_is_promoted_but_noncircle_is_not(self):
        circle = [
            (1.0, 0.0, 0.0),
            (2 ** -0.5, 2 ** -0.5, 0.1),
            (0.0, 1.0, 0.2),
        ]
        self.assertIsNotNone(physics_diagram_assets._circular_projection(circle))
        self.assertIsNone(physics_diagram_assets._circular_projection([
            (0.0, 0.0, 0.0), (0.4, 0.2, 0.0), (1.0, 1.0, 0.0), (1.8, 1.1, 0.0),
        ]))

    def test_three_panel_renderer_gives_motion_panel_double_weight(self):
        import copy
        raw = copy.deepcopy(scene_payload())
        raw["panels"] = [
            {"id": "motion", "title": "运动"},
            {"id": "b-time", "title": "B-t"},
            {"id": "e-time", "title": "E-t"},
        ]
        for collection in ("regions", "objects", "paths", "annotations"):
            for item in raw[collection]:
                item["panel_id"] = "motion"
        svg = physics_diagram.render_svg(physics_diagram.normalize_scene(raw))
        self.assertIn('width="436.8"', svg)
        self.assertIn('width="218.4"', svg)

    def test_spatial_projection_positive_passes_with_axes_and_annotation(self):
        import copy
        raw = copy.deepcopy(scene_payload())
        raw["paths"].extend([
            {"id": "axis-x", "panel_id": "main", "kind": "axis", "geometry": "line", "points": [{"x": 10, "y": 80}, {"x": 90, "y": 80}], "label": "x轴", "direction": "right", "fact_ids": []},
            {"id": "axis-z", "panel_id": "main", "kind": "axis", "geometry": "line", "points": [{"x": 10, "y": 80}, {"x": 10, "y": 20}], "label": "z轴", "direction": "up", "fact_ids": []},
        ])
        raw["annotations"].append({"id": "proj", "panel_id": "main", "x": 50, "y": 90, "text": "空间投影示意", "fact_ids": []})
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(raw), visual_facts(),
            "电场和磁场都垂直纸面向里并同时作用",
        )
        self.assertEqual(gate["status"], "passed")

    def test_plain_magnetic_field_does_not_trigger_teaching_signals(self):
        gate = physics_diagram.semantic_gate(
            physics_diagram.normalize_scene(scene_payload()), visual_facts(),
            "带电粒子进入垂直纸面向里的磁场",
        )
        self.assertEqual(gate["status"], "passed")
        self.assertFalse(gate["teaching_signals"]["periodic_field"])
        self.assertFalse(gate["teaching_signals"]["spatial_projection"])

    def test_flowchart_requires_explicit_plugin_selection(self):
        svg = diagram_plugins.render_optional_diagram(
            diagram_plugins.FLOWCHART_PLUGIN_ID,
            {"title": "逻辑", "nodes": ["识别", "求解"]},
        )
        self.assertIn("<svg", svg)
        with self.assertRaises(ValueError):
            diagram_plugins.render_optional_diagram("", {"title": "逻辑", "nodes": ["识别", "求解"]})


if __name__ == "__main__":
    unittest.main()
