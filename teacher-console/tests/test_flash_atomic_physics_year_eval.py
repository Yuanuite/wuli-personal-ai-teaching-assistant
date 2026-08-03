from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "teacher-console" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import flash_atomic_physics_year_eval as subject  # noqa: E402


def brief() -> dict:
    return {"digest": "digest-1", "targets": [{"id": "Q1"}]}


def payload() -> dict:
    return {
        "status": "completed",
        "message": "completed",
        "target_brief_digest": "digest-1",
        "global_conventions": ["x 轴向右为正"],
        "targets": [
            {
                "id": "Q1",
                "assertions": [
                    {
                        "id": "Q1-A1",
                        "kind": "reference-frame",
                        "decision": "速度沿 x 轴正方向",
                        "basis": "坐标定义固定",
                        "check_kind": "sign",
                        "falsifier": "代入负方向将违反初态定义",
                    },
                    {
                        "id": "Q1-A2",
                        "kind": "law-scope",
                        "decision": "该阶段应用动量定理",
                        "basis": "阶段内存在外力冲量",
                        "check_kind": "conservation",
                        "falsifier": "冲量为零时该状态转移不成立",
                    },
                ],
            }
        ],
    }


class AtomicPhysicsEvalTest(unittest.TestCase):
    def test_accepts_decisive_atomic_assertions(self) -> None:
        self.assertEqual(subject.normalize(payload(), brief())["status"], "completed")

    def test_rejects_hedging(self) -> None:
        candidate = payload()
        candidate["targets"][0]["assertions"][0]["decision"] = "方向需要确认"
        with self.assertRaisesRegex(ValueError, "hedging"):
            subject.normalize(candidate, brief())

    def test_rejects_unknown_kind(self) -> None:
        candidate = payload()
        candidate["targets"][0]["assertions"][0]["kind"] = "formula"
        with self.assertRaisesRegex(ValueError, "unknown assertion kind"):
            subject.normalize(candidate, brief())


if __name__ == "__main__":
    unittest.main()
