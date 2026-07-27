import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "build-physics-simulator"


def load_builder():
    spec = importlib.util.spec_from_file_location("piecewise_builder", SKILL / "scripts" / "build_simulator.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class PiecewiseParticleSimulatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.entry = self.root / "entry"
        (self.entry / "assets").mkdir(parents=True)
        (self.entry / "problem.md").write_text("# 题目\n测试二维分段运动。", encoding="utf-8")
        (self.entry / "assets" / "explanatory.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"/>',
            encoding="utf-8",
        )
        self.model = self.entry / "physics-model.json"
        self.model.write_text(json.dumps(self.fixture(), ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fixture():
        return {
            "schema_version": 1,
            "model_type": "piecewise-field-particle-2d",
            "entry_id": "entry",
            "title": "二维分段场粒子运动",
            "source": {"problem": "problem.md", "diagram": "assets/explanatory.svg"},
            "regions": [
                {
                    "id": "electric",
                    "label": "匀强电场",
                    "field": {"kind": "electric", "label": "E →"},
                    "shape": {"type": "rect", "x": 0, "y": 0, "width": 2, "height": 2},
                },
                {
                    "id": "magnetic",
                    "label": "匀强磁场",
                    "field": {"kind": "magnetic-out", "label": "B ⊙"},
                    "shape": {"type": "circle", "cx": 3, "cy": 1, "r": 1},
                },
            ],
            "facts": {
                "viewport": {"xmin": -0.5, "xmax": 4.5, "ymin": -0.5, "ymax": 2.5},
                "particles": [{"id": "p", "label": "q+"}],
            },
            "technique_ids": [],
            "event_model": {
                "stop_event_id": "end",
                "timeline": [
                    {
                        "id": "start",
                        "order": 0,
                        "time": 0,
                        "label": "进入电场",
                        "region": "electric",
                        "position": [0, 0],
                        "p_candidate": False,
                    },
                    {
                        "id": "switch",
                        "order": 1,
                        "time": 1,
                        "label": "进入磁场",
                        "region": "magnetic",
                        "position": [2, 1],
                        "p_candidate": False,
                    },
                    {
                        "id": "end",
                        "order": 2,
                        "time": 2,
                        "label": "到达终点",
                        "region": "magnetic",
                        "position": [3, 2],
                        "p_candidate": False,
                    },
                ],
                "cases": [{"id": "main", "label": "主情形", "conclusion": "轨迹连续"}],
            },
            "trajectory": {
                "segments": [
                    {
                        "id": "electric-line",
                        "particle_id": "p",
                        "type": "polyline",
                        "region": "electric",
                        "start_event": "start",
                        "end_event": "switch",
                        "case_ids": ["main"],
                        "geometry": {"points": [[0, 0], [0.5, 0.1], [1.2, 0.4], [2, 1]]},
                        "kinematics": {"start_time": 0, "end_time": 1},
                        "force_direction": [1, 0],
                    },
                    {
                        "id": "magnetic-arc",
                        "particle_id": "p",
                        "type": "arc",
                        "region": "magnetic",
                        "start_event": "switch",
                        "end_event": "end",
                        "case_ids": ["main"],
                        "geometry": {
                            "center": [3, 1],
                            "radius": 1,
                            "start_deg": 180,
                            "end_deg": 90,
                            "radius_label": "r",
                        },
                        "kinematics": {"start_time": 1, "end_time": 2},
                        "force_direction": [1, 0],
                    },
                ]
            },
            "student_solution": {
                "quick_answers": ["轨迹连续"],
                "recognition": ["分段场"],
                "main_steps": [{"title": "建模", "formulae": ["r=mv/(qB)"]}],
                "pitfalls": ["方向"],
                "self_check": "检查切线连续。",
            },
            "teacher_audit": {"checks": ["端点与方向一致"]},
            "simulation": {
                "default_duration_seconds": 12,
                "layers": ["trajectory", "field", "geometry"],
                "default_layer": "trajectory",
                "pause_event_ids": ["switch", "end"],
            },
        }

    def test_validator_and_offline_builder_accept_piecewise_model(self):
        validated = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "validate_physics_model.py"), str(self.model)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

        builder = load_builder()
        report = builder.build(
            self.model,
            self.entry,
            self.root / "simulation",
            "physics-simulator",
            "二维分段场粒子运动",
            True,
            "skip",
        )
        self.assertEqual(report["status"], "ok", report)
        self.assertTrue(Path(report["artifacts"]["html"]).exists())
        self.assertTrue(Path(report["artifacts"]["zip"]).exists())
        self.assertEqual(report["simulator_validation"]["errors"], [])

    def test_case_specific_stop_event_is_rendered_and_validated(self):
        fixture = self.fixture()
        fixture["event_model"]["cases"][0]["stop_event_id"] = "end"
        self.model.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

        validated = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "validate_physics_model.py"), str(self.model)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

        builder = load_builder()
        report = builder.build(
            self.model,
            self.entry,
            self.root / "simulation-case-stop",
            "physics-simulator",
            "二维分段场粒子运动",
            False,
            "skip",
        )
        html = Path(report["artifacts"]["html"]).read_text(encoding="utf-8")
        self.assertIn("currentCase()?.stop_event_id||model.event_model.stop_event_id", html)
        self.assertIn("bounds.start+progress*(bounds.end-bounds.start)", html)
        self.assertIn(
            "orderedSegments.find(item=>time<=Number(item.kinematics.end_time)+1e-8)",
            html,
        )

    def test_case_specific_stop_event_must_belong_to_same_case(self):
        fixture = self.fixture()
        fixture["event_model"]["timeline"][-1]["case_ids"] = ["other"]
        fixture["event_model"]["cases"][0]["stop_event_id"] = "end"
        self.model.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

        validated = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "validate_physics_model.py"), str(self.model)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(validated.returncode, 0)
        self.assertIn("stop_event_id must reference an event in the same case", validated.stdout)

    def test_validator_degrades_when_jsonschema_is_unavailable(self):
        blocker = self.root / "without-jsonschema"
        blocker.mkdir()
        (blocker / "jsonschema.py").write_text(
            'raise ImportError("jsonschema intentionally unavailable")\n',
            encoding="utf-8",
        )
        validated = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "validate_physics_model.py"), str(self.model)],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(blocker)},
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertIn("deterministic model-type validation still ran", validated.stdout)


if __name__ == "__main__":
    unittest.main()
