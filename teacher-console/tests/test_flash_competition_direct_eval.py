from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import flash_competition_direct_eval as direct_eval  # noqa: E402


class FlashCompetitionDirectEvalTest(unittest.TestCase):
    def test_core_contract_excludes_heavy_w3_and_teaching_fields(self):
        contract = direct_eval.output_contract("core")
        item = contract["schema"]["properties"]["results"]["items"]
        self.assertEqual(
            set(item["properties"]),
            {"part", "final_answer", "key_relations", "confidence"},
        )
        self.assertEqual(item["properties"]["key_relations"]["maxItems"], 4)
        serialized = str(contract)
        for forbidden in (
            "stage_interfaces",
            "stage_transitions",
            "verification_obligations",
            "derivation",
            "checks",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_core_candidate_requires_one_answer_and_relation_per_part(self):
        direct_eval.validate_candidate(
            {
                "status": "completed",
                "results": [
                    {
                        "part": "(1)",
                        "final_answer": "x=1",
                        "key_relations": ["x squared is 1 and x is positive"],
                    }
                ],
            },
            "core",
        )
        with self.assertRaisesRegex(ValueError, "no key_relations"):
            direct_eval.validate_candidate(
                {
                    "status": "completed",
                    "results": [
                        {
                            "part": "(1)",
                            "final_answer": "x=1",
                            "key_relations": [],
                        }
                    ],
                },
                "core",
            )


if __name__ == "__main__":
    unittest.main()
