import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_archive  # noqa: E402
import evaluator  # noqa: E402
import kb  # noqa: E402
import knowledge_store  # noqa: E402


class KnowledgeStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "student-error-library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260722-knowledge-store"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        (assets / "original.png").write_bytes(b"source")
        (assets / "explanation.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80"></svg>',
            encoding="utf-8",
        )
        solution = (
            "# 解析\n\n"
            "## 答案速览\n用动量守恒和能量守恒处理碰撞。\n\n"
            "## 详细解答\n先判断系统外力冲量可忽略，再列动量守恒式，最后用能量关系检查结果。"
            "这个步骤用于测试本地 RAG 证据检索。\n\n"
            "## 易错点\n不要把机械能守恒误用于非弹性碰撞。\n\n"
            "![解释图](assets/explanation.svg)\n"
        )
        for name, text in {
            "problem.md": "# 碰撞测试题\n\n小车与木块发生非弹性碰撞，求共同速度。",
            "solution.md": solution,
            "student-solution.md": solution,
            "teacher-solution.md": solution,
        }.items():
            kb.write_text(self.entry / name, text)
        kb.write_json(
            self.entry / "record.json",
            {
                "schema_version": 1,
                "id": self.entry.name,
                "kind": "error",
                "status": "ready",
                "answer_status": "complete",
                "title": "动量守恒碰撞题",
                "subject": "高中物理",
                "grade": "高二",
                "knowledge_points": ["动量守恒", "非弹性碰撞"],
                "error_types": ["误用机械能守恒"],
                "difficulty": "3",
                "created_at": "2026-07-22T09:00:00+08:00",
                "updated_at": "2026-07-22T09:00:00+08:00",
                "source": {
                    "sha256": hashlib.sha256(b"source").hexdigest(),
                    "source_type": "png",
                    "stored_files": ["assets/original.png"],
                },
                "ocr": {"engine": "test", "review_required": False},
                "source_review": {"status": "passed"},
            },
        )
        answer_review = {
            "schema_version": 1,
            "entry_id": self.entry.name,
            "status": "passed",
            "reviewer": "teacher",
            "reviewed_at": "2026-07-22T10:00:00+08:00",
            "answer_digest": kb.answer_artifact_digest(self.entry),
            "note": "checked",
        }
        kb.write_json(self.entry / "answer-review.json", answer_review)
        record = kb.load_json(self.entry / "record.json", {})
        record["answer_review"] = answer_review
        kb.write_json(self.entry / "record.json", record)
        self.evaluation = evaluator.evaluate_entry(self.library, self.entry.name, write=True)
        candidate_archive.append_event(
            self.library,
            self.entry,
            task_type="answer.save",
            actor="teacher",
            event_type="manual-edit",
            status="saved",
            summary="教师调整学生版解析",
            evaluation=self.evaluation,
            changed_files=["student-solution.md"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_blueprint_evidence_adapts_need_count_and_uses_one_selector(self):
        blueprint = {
            "retrieval_needs": [
                {
                    "id": f"R{index}",
                    "query": f"查询 {index}",
                    "purpose": f"目的 {index}",
                    "priority": 6 - index,
                    "target_ids": [f"Q{index}"],
                    "stage_ids": [],
                }
                for index in range(1, 6)
            ]
        }
        target = knowledge_store.db_path(self.library)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"placeholder")

        def fake_query(_root, text, **_kwargs):
            index = int(text.rsplit(" ", 1)[-1])
            return {
                "status": "ok",
                "freshness": {"status": "current"},
                "results": [
                    {
                        "entry_id": f"other-{index}",
                        "title": f"参考题 {index}",
                        "selected_rank": 1,
                        "knowledge_points": ["磁场"],
                        "teaching_memory": {"methods": ["事件枚举"], "secondary_conclusions": []},
                        "matched_documents": [{"kind": "problem", "snippet": text}],
                        "evidence_coverage": {"covered_slots": ["problem-context"]},
                        "evidence_audit": {
                            "decision": "accepted",
                            "precision_score": 0.8,
                            "condition_profile": {},
                            "match_basis": {"route_ids": ["problem"]},
                        },
                    }
                ],
            }

        with mock.patch.object(knowledge_store, "query", side_effect=fake_query):
            result = knowledge_store.build_blueprint_evidence(
                self.library,
                self.entry.name,
                blueprint,
                default_need_limit=3,
                max_need_limit=5,
                top_k=4,
                explicit_db=target,
            )
        self.assertEqual(result["retrieval_plan"]["executed_need_count"], 5)
        self.assertEqual(result["evidence_set"]["selection_policy"], "evidence-set-v2")
        self.assertLessEqual(len(result["references"]), 4)

    def test_rebuild_creates_sqlite_store_with_entry_evidence(self):
        report = knowledge_store.rebuild(self.library)
        self.assertEqual(report["status"], "rebuilt")
        self.assertEqual(report["entries"], 1)
        self.assertTrue((self.library / "indexes" / "wuli-memory.db").is_file())
        evidence = knowledge_store.query(self.library, "动量守恒 非弹性碰撞", mode="teaching", top_k=3)
        self.assertEqual(evidence["results"][0]["entry_id"], self.entry.name)
        self.assertEqual(evidence["results"][0]["evaluation"]["status"], self.evaluation["status"])
        self.assertEqual(evidence["results"][0]["recent_events"][0]["task_type"], "answer.save")
        self.assertIn("entries/20260722-knowledge-store", evidence["evidence_sources"][0])
        self.assertEqual(evidence["evolve_observations"], [])
        self.assertEqual(evidence["evidence_set"]["candidate_entry_count"], 1)
        self.assertEqual(
            evidence["evidence_set"]["traceable_document_count"],
            evidence["evidence_set"]["document_count"],
        )
        self.assertGreater(report["evidence_units"], 0)
        self.assertNotIn("evidence_units", evidence)

    def test_rebuild_projects_traceable_high_value_evidence_units_in_shadow_only(self):
        first = knowledge_store.rebuild(self.library)
        projection = knowledge_store.load_evidence_unit_projection(self.library)
        self.assertEqual(projection["status"], "ready")
        self.assertEqual(projection["unit_count"], first["evidence_units"])
        units = projection["units"]
        false_friend = next(
            item
            for item in units
            if item["unit_kind"] == "false_friend_warning" and self.entry.name in item["source_locator"]["path"]
        )
        self.assertEqual(false_friend["source_kind"], "approved_solution")
        self.assertEqual(false_friend["authority_level"], "B")
        self.assertTrue(false_friend["applicability"])
        self.assertTrue(false_friend["exceptions"])
        self.assertTrue(false_friend["content_hash"].startswith("sha256:"))
        curated = next(item for item in units if item["source_kind"] == "curated_technique")
        self.assertEqual(curated["authority_level"], "A")
        self.assertNotIn(self.entry.name, curated["source_locator"]["path"])
        circuit_node = next(item for item in units if item["source_locator"]["section"] == "circuit-node-topology")
        self.assertEqual(circuit_node["authority_level"], "A")
        self.assertIn("连接节点", circuit_node["physics_facets"])
        self.assertIn("电流通路", circuit_node["physics_facets"])

        identity_before = {item["evidence_id"]: item["content_hash"] for item in units}
        second = knowledge_store.rebuild(self.library)
        projection_after = knowledge_store.load_evidence_unit_projection(self.library)
        identity_after = {item["evidence_id"]: item["content_hash"] for item in projection_after["units"]}
        self.assertEqual(first["evidence_units"], second["evidence_units"])
        self.assertEqual(identity_before, identity_after)

    def test_shadow_projection_excludes_current_entry_and_unapproved_answers(self):
        unapproved = self.library / "entries" / "20260722-unapproved"
        unapproved.mkdir(parents=True)
        kb.write_text(
            unapproved / "teacher-solution.md",
            "# 解析\n\n## 易错点\n\n- 不应进入证据投影。",
        )
        kb.write_json(
            unapproved / "record.json",
            {
                "schema_version": 1,
                "id": unapproved.name,
                "status": "ready",
                "title": "未批准解析",
                "subject": "高中物理",
                "knowledge_points": ["动量守恒"],
                "error_types": [],
            },
        )
        knowledge_store.rebuild(self.library)
        complete = knowledge_store.load_evidence_unit_projection(self.library)
        serialized = json.dumps(complete, ensure_ascii=False)
        self.assertNotIn(unapproved.name, serialized)

        excluded = knowledge_store.load_evidence_unit_projection(self.library, exclude_entry_id=self.entry.name)
        self.assertEqual(excluded["status"], "ready")
        self.assertTrue(excluded["excluded_current_entry"])
        self.assertNotIn(
            f"entries/{self.entry.name}/",
            json.dumps(excluded, ensure_ascii=False),
        )

    def test_shadow_projection_extracts_typed_method_and_conditioned_conclusion(self):
        teacher_path = self.entry / "teacher-solution.md"
        text = teacher_path.read_text(encoding="utf-8").replace(
            "## 详细解答",
            "## 一眼识别\n\n"
            "- 题型识别：系统外力冲量可忽略的碰撞。\n"
            "- 最短主线：先选系统，再规定正方向并列动量守恒。\n"
            "- 可用二级结论：完全非弹性碰撞后共速；"
            "**适用条件**：碰撞后两物体粘在一起。\n\n"
            "## 详细解答",
        )
        teacher_path.write_text(text, encoding="utf-8")
        record = kb.load_json(self.entry / "record.json", {})
        review = record["answer_review"]
        review["answer_digest"] = kb.answer_artifact_digest(self.entry)
        record["answer_review"] = review
        kb.write_json(self.entry / "answer-review.json", review)
        kb.write_json(self.entry / "record.json", record)

        knowledge_store.rebuild(self.library)
        projection = knowledge_store.load_evidence_unit_projection(self.library)
        entry_units = [item for item in projection["units"] if self.entry.name in item["source_locator"]["path"]]
        method = next(item for item in entry_units if item["unit_kind"] == "method_applicability")
        conclusion = next(item for item in entry_units if item["unit_kind"] == "secondary_conclusion")
        self.assertIn("规定正方向", method["text"])
        self.assertTrue(any("题型识别" in item for item in method["applicability"]))
        self.assertEqual(conclusion["applicability"], ["碰撞后两物体粘在一起。"])

    def test_shadow_projection_failure_does_not_block_production_rebuild(self):
        record = kb.load_json(self.entry / "record.json", {})
        record["methods"] = [
            {
                "text": "过长方法" * 5000,
                "conditions": ["条件明确"],
                "forbidden": [],
            }
        ]
        kb.write_json(self.entry / "record.json", record)
        report = knowledge_store.rebuild(self.library)
        self.assertEqual(report["status"], "rebuilt")
        self.assertGreaterEqual(report["evidence_unit_projection_errors"], 1)
        evidence = knowledge_store.query(self.library, "动量守恒 非弹性碰撞", mode="teaching", top_k=3)
        self.assertEqual(evidence["status"], "ok")

    def test_kb_rebuild_refreshes_knowledge_store_fail_soft(self):
        report = kb.rebuild_index(self.library)
        self.assertEqual(report["knowledge_store"]["status"], "rebuilt")
        self.assertTrue((self.library / "indexes" / "wuli-memory.db").is_file())

    def test_agent_evidence_excludes_current_entry_and_internal_paths(self):
        similar = self.library / "entries" / "20260722-similar-private-id"
        similar.mkdir(parents=True)
        kb.write_text(similar / "problem.md", "# 相似碰撞题\n\n两个小车发生非弹性碰撞，求共同速度。")
        kb.write_text(similar / "solution.md", "使用动量守恒，先规定正方向，再检查单位和极限情况。")
        kb.write_json(
            similar / "record.json",
            {
                "schema_version": 1,
                "id": similar.name,
                "kind": "error",
                "status": "ready",
                "title": "同类动量守恒题",
                "subject": "高中物理",
                "grade": "高二",
                "knowledge_points": ["动量守恒", "非弹性碰撞"],
                "error_types": ["方向符号错误"],
                "methods": ["规定正方向后列动量守恒"],
                "library_folder": "学生私有文件夹",
            },
        )
        knowledge_store.rebuild(self.library)
        evidence = knowledge_store.build_agent_evidence(
            self.library,
            self.entry.name,
            "动量守恒 非弹性碰撞 共同速度",
            task_type="answer.revise",
            top_k=2,
            char_budget=4000,
        )
        self.assertEqual(evidence["status"], "ready")
        self.assertEqual(evidence["references"][0]["title"], "同类动量守恒题")
        serialized = json.dumps(evidence, ensure_ascii=False)
        self.assertNotIn(self.entry.name, serialized)
        self.assertNotIn(similar.name, serialized)
        self.assertNotIn("学生私有文件夹", serialized)
        self.assertNotIn("wuli-memory.db", serialized)
        self.assertEqual(evidence["context_budget"]["policy"], "deterministic-evidence-v1")
        self.assertLessEqual(evidence["context_budget"]["serialized_chars"], 4000)
        self.assertEqual(evidence["references"][0]["content_hash"][:7], "sha256:")
        self.assertEqual(evidence["evidence_set"]["kind"], "candidate-evidence-set")
        self.assertEqual(evidence["evidence_set"]["reference_count"], len(evidence["references"]))

    def test_query_is_read_only_and_requires_explicit_rebuild_for_incomplete_store(self):
        knowledge_store.rebuild(self.library)
        target = knowledge_store.db_path(self.library)
        connection = knowledge_store.connect(target)
        try:
            connection.execute("DROP TABLE scheduler_benchmark")
            connection.commit()
        finally:
            connection.close()
        evidence = knowledge_store.query(self.library, "动量守恒", mode="teaching", top_k=2)
        self.assertEqual(evidence["status"], "unavailable")
        self.assertEqual(evidence["reason"], "knowledge-store-incomplete")
        connection = knowledge_store.connect(target)
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduler_benchmark'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNone(table)
        knowledge_store.rebuild(self.library)
        self.assertTrue(knowledge_store.query(self.library, "动量守恒", mode="teaching", top_k=2)["results"])

    def test_new_archive_event_marks_store_stale_until_explicit_rebuild(self):
        knowledge_store.rebuild(self.library)
        self.assertEqual(knowledge_store.query(self.library, "动量守恒")["freshness"]["status"], "current")
        candidate_archive.append_event(
            self.library,
            self.entry,
            task_type="answer.revision-request",
            actor="teacher",
            event_type="feedback",
            status="revision-requested",
            feedback={"categories": ["physics-correction"]},
        )
        stale = knowledge_store.query(self.library, "动量守恒")
        self.assertEqual(stale["freshness"]["status"], "stale")
        unavailable = knowledge_store.build_agent_evidence(
            self.library, self.entry.name, "动量守恒", task_type="answer.revise"
        )
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["reason"], "knowledge-store-stale")

    def test_fts_ranking_prefers_the_more_relevant_entry(self):
        weak = self.library / "entries" / "aaa-weak-match"
        strong = self.library / "entries" / "zzz-strong-match"
        for entry, title, problem in (
            (weak, "普通电学题", "# 题目\n\n本题只简单提到电磁感应。"),
            (
                strong,
                "楞次定律与电磁感应",
                "# 题目\n\n用楞次定律判断电磁感应、电磁感应、电磁感应中的电流方向。",
            ),
        ):
            entry.mkdir(parents=True)
            kb.write_text(entry / "problem.md", problem)
            kb.write_json(
                entry / "record.json",
                {
                    "schema_version": 1,
                    "id": entry.name,
                    "kind": "error",
                    "status": "ready",
                    "title": title,
                    "subject": "高中物理",
                },
            )
        knowledge_store.rebuild(self.library)

        evidence = knowledge_store.query(self.library, "楞次定律 电磁感应", top_k=3)

        self.assertEqual(evidence["results"][0]["entry_id"], strong.name)

    def test_teaching_query_expands_teacher_phrasing_to_canonical_labels(self):
        spatial = self.library / "entries" / "direction-spatial-match"
        spatial.mkdir(parents=True)
        kb.write_text(spatial / "problem.md", "# 空间方向题\n\n判断通电导体在磁场中的转动方向。")
        kb.write_json(
            spatial / "record.json",
            {
                "schema_version": 1,
                "id": spatial.name,
                "kind": "error",
                "status": "ready",
                "title": "通电导体方向判断",
                "subject": "高中物理",
                "knowledge_points": ["安培力"],
                "error_types": ["方向判断", "空间想象"],
            },
        )
        knowledge_store.rebuild(self.library)

        evidence = knowledge_store.query(self.library, "磁场方向性理解", mode="teaching", top_k=3)

        self.assertEqual(evidence["results"][0]["entry_id"], spatial.name)
        self.assertEqual(evidence["query_expansions"], ["方向判断", "符号方向", "空间想象"])

    def test_teaching_intent_removes_task_phrasing_and_adds_reviewed_aliases(self):
        intent, removed, additions = knowledge_store._teaching_intent_query("帮我找一道关于粒子反复进出磁场的题目")
        self.assertNotIn("帮我找一道", intent)
        self.assertNotIn("题目", intent)
        self.assertIn("反复进出磁场", intent)
        self.assertIn("圆形有界磁场", intent)
        self.assertIn("帮我找一道", removed)
        self.assertIn("带电粒子", additions)
        self.assertIn("轨迹衔接", additions)

    def test_query_runs_independent_routes_and_exposes_rrf_contributions(self):
        fixtures = (
            (
                "cross-route-match",
                {"knowledge_points": ["回旋半径判据"]},
                "# 题目\n\n利用回旋半径判据判断粒子能否离开磁场。",
                "",
            ),
            (
                "problem-route-match",
                {},
                "# 题目\n\n使用回旋半径判据。",
                "",
            ),
            (
                "solution-route-match",
                {},
                "# 题目\n\n判断粒子运动范围。",
                "解析采用回旋半径判据，将轨迹半径与区域宽度比较。",
            ),
        )
        for entry_id, metadata, problem, solution in fixtures:
            entry = self.library / "entries" / entry_id
            entry.mkdir(parents=True)
            kb.write_text(entry / "problem.md", problem)
            if solution:
                kb.write_text(entry / "solution.md", solution)
            kb.write_json(
                entry / "record.json",
                {
                    "schema_version": 1,
                    "id": entry_id,
                    "kind": "error",
                    "status": "ready",
                    "title": f"检索路由样本 {entry_id}",
                    "subject": "高中物理",
                    **metadata,
                },
            )
        knowledge_store.rebuild(self.library)

        evidence = knowledge_store.query(
            self.library,
            "回旋半径判据",
            mode="teaching",
            top_k=5,
            ranking_policy="multi-route",
        )

        self.assertEqual(evidence["retrieval"]["strategy"], "multi-route-bm25-rrf-v1")
        self.assertEqual(
            [route["id"] for route in evidence["retrieval"]["routes"]],
            [
                "metadata",
                "problem",
                "solution",
            ],
        )
        self.assertEqual(evidence["query_plan"]["raw_query"], "回旋半径判据")
        self.assertEqual(evidence["results"][0]["entry_id"], "cross-route-match")
        by_id = {item["entry_id"]: item for item in evidence["results"]}
        self.assertEqual(
            {item["route"] for item in by_id["cross-route-match"]["route_matches"]},
            {"metadata", "problem"},
        )
        self.assertEqual(
            {item["route"] for item in by_id["solution-route-match"]["route_matches"]},
            {"solution"},
        )
        self.assertTrue(
            all("route" in document for item in evidence["results"] for document in item["matched_documents"])
        )

    def test_condition_audit_rejects_cross_domain_false_positive(self):
        query_plan = {
            "retrieval_text": "带电粒子在分区磁场中的平均速度",
            "tokens": ["带电粒子", "分区磁场", "平均速度"],
        }
        accepted = knowledge_store._evidence_relevance_audit(
            query_plan,
            title="两同向分区磁场中带电粒子的平均速度",
            knowledge_points=["带电粒子在磁场中的运动"],
            error_types=[],
            matched_documents=[
                {
                    "snippet": "粒子依次经过两个分区磁场，求平均速度。",
                }
            ],
            route_matches=[{"route": "problem"}],
        )
        rejected = knowledge_store._evidence_relevance_audit(
            query_plan,
            title="正方形线框匀速穿越宽磁场",
            knowledge_points=["电磁感应", "感应电动势"],
            error_types=[],
            matched_documents=[
                {
                    "snippet": "导线框穿越磁场，判断感应电流。",
                }
            ],
            route_matches=[{"route": "solution"}],
        )

        self.assertEqual(accepted["decision"], "accepted")
        self.assertEqual(rejected["decision"], "rejected-low-precision")
        self.assertTrue(rejected["conflict_conditions"])

    def test_precision_gated_pack_degrades_to_empty_when_all_candidates_conflict(self):
        target = knowledge_store.db_path(self.library)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
        conflicting = {
            "entry_id": "induction-entry",
            "title": "正方形线框匀速穿越宽磁场",
            "knowledge_points": ["电磁感应"],
            "error_types": [],
            "teaching_memory": {},
            "evaluation": {},
            "recent_events": [],
            "matched_documents": [{"kind": "problem", "snippet": "线框切割磁感线"}],
            "evidence_coverage": {},
            "evidence_audit": {
                "decision": "rejected-low-precision",
                "precision_score": 0.08,
                "conflict_conditions": ["domain 不相容"],
            },
        }
        fake_query = {
            "status": "ok",
            "freshness": {"status": "current"},
            "results": [conflicting],
        }

        with mock.patch.object(knowledge_store, "query", return_value=fake_query):
            evidence = knowledge_store.build_agent_evidence(
                self.library,
                self.entry.name,
                "带电粒子在分区磁场中的平均速度",
                task_type="analysis.generate",
                selection_policy="precision-gated-v1",
            )

        self.assertEqual(evidence["references"], [])
        self.assertEqual(
            evidence["evidence_set"]["selection_status"],
            "degraded-empty-low-precision",
        )
        self.assertEqual(evidence["evidence_set"]["rejected_low_precision_count"], 1)

    def test_evidence_set_v2_deduplicates_and_rejects_inter_reference_conflict(self):
        def result(title, precision, domain, *, rank):
            return {
                "title": title,
                "selected_rank": rank,
                "knowledge_points": ["磁场"],
                "error_types": [],
                "matched_documents": [{"snippet": "带电粒子在分区磁场中的轨迹分析"}],
                "evidence_coverage": {
                    "covered_slots": ["problem-context", "solution-method"],
                },
                "evidence_audit": {
                    "decision": "accepted",
                    "precision_score": precision,
                    "condition_profile": {"domain": [domain]},
                    "match_basis": {"route_ids": ["problem", "solution"]},
                },
            }

        selection = knowledge_store.select_evidence_results(
            [
                result("分区磁场粒子轨迹", 0.9, "charged-particle", rank=1),
                result("分区磁场粒子轨迹", 0.8, "charged-particle", rank=2),
                result("线框电磁感应", 0.7, "circuit-induction", rank=3),
            ],
            selection_policy="evidence-set-v2",
            limit=3,
        )

        self.assertEqual(
            [item["title"] for item in selection["selected_results"]],
            ["分区磁场粒子轨迹"],
        )
        self.assertEqual(selection["trace"]["rejected_duplicate_count"], 1)
        self.assertEqual(selection["trace"]["rejected_conflict_count"], 1)


if __name__ == "__main__":
    unittest.main()
