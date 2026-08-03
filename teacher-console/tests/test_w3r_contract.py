import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

import w3r_contract  # noqa: E402

FIXTURE = CONSOLE / "tests" / "fixtures" / "w3r" / "verified-multi-target.json"


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class W3RContractTest(unittest.TestCase):
    def test_verified_proof_package_projects_stable_strict_brief(self):
        source = fixture()
        first = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        second = w3r_contract.build_w3r_brief(
            source["problem"],
            copy.deepcopy(source["blueprint"]),
            copy.deepcopy(source["proof_package"]),
        )
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["brief_fingerprint"], second["brief_fingerprint"])
        self.assertEqual(first["brief"]["schema"], "wuli.w3r-brief.v1")
        self.assertEqual(
            [item["response_mode"] for item in first["brief"]["question_targets"]],
            ["single", "enumerate_all"],
        )

    def test_unverified_package_never_reaches_renderer(self):
        source = fixture()
        source["proof_package"]["aggregation_status"] = "PROVISIONAL"
        result = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        self.assertEqual(result["status"], "needs_render_material")
        self.assertEqual(
            result["violations"][0]["code"], "proof-package-not-verified"
        )

    def test_olympiad_profile_is_explicit_in_render_brief(self):
        source = fixture()
        result = w3r_contract.build_w3r_brief(
            source["problem"],
            source["blueprint"],
            source["proof_package"],
            method_profile="olympiad_official",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            result["brief"]["method_scope"]["level"], "olympiad_official"
        )
        self.assertNotIn(
            "calculus.integration",
            result["brief"]["method_scope"]["forbidden_methods"],
        )

    def test_unknown_field_is_rejected_without_silent_compatibility(self):
        source = fixture()
        built = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        brief = copy.deepcopy(built["brief"])
        brief["future_field"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            w3r_contract.normalize_w3r_brief(brief)

    def test_missing_claim_mapping_is_rejected(self):
        source = fixture()
        built = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        brief = copy.deepcopy(built["brief"])
        brief["final_answers"][0]["claim_ids"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "unknown Claim"):
            w3r_contract.normalize_w3r_brief(brief)

    def test_dependency_cycle_fails_preflight(self):
        source = fixture()
        built = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        brief = copy.deepcopy(built["brief"])
        brief["proof_skeleton"][0]["depends_on"] = [
            brief["proof_skeleton"][-1]["step_id"]
        ]
        result = w3r_contract.preflight_w3r_brief(brief)
        self.assertEqual(result["status"], "needs_render_material")
        self.assertIn("cycle", result["violations"][0]["message"])

    def test_enumerate_all_requires_explicit_completeness(self):
        source = fixture()
        built = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        brief = copy.deepcopy(built["brief"])
        brief["final_answers"][1]["complete"] = False
        with self.assertRaisesRegex(ValueError, "complete=true"):
            w3r_contract.normalize_w3r_brief(brief)

    def test_unresolved_obligation_returns_needs_material(self):
        source = fixture()
        source["proof_package"]["unresolved_obligations"] = [
            {"obligation_id": "v1"}
        ]
        result = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        self.assertEqual(result["status"], "needs_render_material")
        self.assertTrue(any(
            item["code"] == "unresolved-verification-obligation"
            for item in result["violations"]
        ))

    def test_proof_package_fingerprint_mismatch_is_blocking(self):
        source = fixture()
        built = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )
        report = w3r_contract.preflight_w3r_brief(
            built["brief"],
            expected_proof_package_fingerprint="sha256:" + "0" * 64,
        )
        self.assertEqual(report["status"], "needs_render_material")
        self.assertEqual(
            report["violations"][0]["code"],
            "proof-package-fingerprint-mismatch",
        )

    def test_proof_skeleton_over_student_step_budget_needs_material(self):
        source = fixture()
        brief = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )["brief"]
        while len(brief["proof_skeleton"]) <= brief["method_scope"]["max_main_steps"]:
            extra = copy.deepcopy(brief["proof_skeleton"][-1])
            extra["step_id"] = f"s-extra-{len(brief['proof_skeleton'])}"
            extra["depends_on"] = []
            brief["proof_skeleton"].append(extra)
        result = w3r_contract.preflight_w3r_brief(brief)
        self.assertEqual(result["status"], "needs_render_material")
        self.assertTrue(any(
            item["code"] == "proof-mainline-too-long"
            for item in result["violations"]
        ))


if __name__ == "__main__":
    unittest.main()
