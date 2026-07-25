import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude" / "skills" / "build-physics-simulator"


def load_builder():
    spec = importlib.util.spec_from_file_location("piecewise_3d_builder", SKILL / "scripts" / "build_simulator.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class PiecewiseParticle3DSimulatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.entry = self.root / "entry"
        (self.entry / "assets").mkdir(parents=True)
        (self.entry / "problem.md").write_text("# 题目\n测试三维螺旋运动。", encoding="utf-8")
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
            "model_type": "piecewise-field-particle-3d",
            "entry_id": "entry",
            "title": "三维分段场粒子运动",
            "source": {"problem": "problem.md", "diagram": "assets/explanatory.svg"},
            "regions": [
                {
                    "id": "magnetic",
                    "label": "匀强磁场",
                    "color": "#5c7fb5",
                    "field": {
                        "kind": "magnetic",
                        "label": "B +z",
                        "origin": [0, 0, 0],
                        "direction": [0, 0, 1],
                    },
                    "shape": {"type": "box", "min": [-2, -2, -1], "max": [2, 2, 3]},
                }
            ],
            "facts": {
                "axes": {"origin": [0, 0, 0], "lengths": [2, 2, 2]},
                "particles": [{"id": "electron", "label": "e-", "sign": "−", "color": "#d43f3a"}],
            },
            "technique_ids": [],
            "event_model": {
                "stop_event_id": "end",
                "timeline": [
                    {
                        "id": "start",
                        "order": 0,
                        "time": 0,
                        "label": "进入磁场",
                        "phase": "开始回旋",
                        "region": "magnetic",
                        "position": [1, 0, 0],
                        "p_candidate": False,
                    },
                    {
                        "id": "end",
                        "order": 1,
                        "time": 2,
                        "label": "完成一周",
                        "phase": "螺旋前进",
                        "region": "magnetic",
                        "position": [1, 0, 2],
                        "p_candidate": False,
                    },
                ],
                "cases": [{"id": "main", "label": "主情形", "duration": 2, "conclusion": "回旋一周并沿轴前进"}],
            },
            "trajectory": {
                "segments": [
                    {
                        "id": "helix",
                        "particle_id": "electron",
                        "type": "polyline",
                        "region": "magnetic",
                        "start_event": "start",
                        "end_event": "end",
                        "case_ids": ["main"],
                        "geometry": {
                            "path_kind": "helix",
                            "center_start": [0, 0, 0],
                            "axis": [0, 0, 1],
                            "basis_u": [1, 0, 0],
                            "basis_v": [0, 1, 0],
                            "radius": 1,
                            "start_deg": 0,
                            "end_deg": 360,
                            "advance": 2,
                        },
                        "kinematics": {"start_time": 0, "end_time": 2},
                    }
                ]
            },
            "student_solution": {
                "quick_answers": ["螺旋运动"],
                "recognition": ["速度分解"],
                "main_steps": [{"title": "建模", "formulae": ["r=mv_perp/(qB)"]}],
                "pitfalls": ["忽略平行分量"],
                "self_check": "检查轴向位移。",
            },
            "teacher_audit": {"checks": ["回旋方向与轴向速度一致"]},
            "simulation": {
                "default_duration_seconds": 12,
                "layers": ["trajectory", "field", "geometry"],
                "default_layer": "trajectory",
                "pause_event_ids": ["end"],
                "camera": {"target": [0, 0, 1], "yaw_deg": -38, "pitch_deg": 28, "scale": 1},
            },
        }

    def test_validator_and_offline_builder_accept_piecewise_3d_model(self):
        validated = subprocess.run(
            [sys.executable, str(SKILL / "scripts" / "validate_physics_model.py"), str(self.model)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)

        report = load_builder().build(
            self.model,
            self.entry,
            self.root / "simulation",
            "physics-simulator",
            "三维分段场粒子运动",
            True,
            "skip",
        )
        self.assertEqual(report["status"], "ok", report)
        self.assertTrue(Path(report["artifacts"]["html"]).exists())
        self.assertTrue(Path(report["artifacts"]["zip"]).exists())
        self.assertEqual(report["simulator_validation"]["errors"], [])


if __name__ == "__main__":
    unittest.main()
