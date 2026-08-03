import importlib.util
import unittest
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts" / "difficulty_assessment.py"
SPEC = importlib.util.spec_from_file_location("difficulty_assessment", SCRIPT)
assert SPEC is not None
difficulty = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(difficulty)


class DifficultyAssessmentTest(unittest.TestCase):
    def setUp(self):
        self.record = {
            "title": "交替电场与磁场中的粒子运动",
            "knowledge_points": ["电场", "磁场", "圆周运动", "动能定理"],
        }
        self.problem = """（1）求位置；（2）求做功；（3）求哪些时刻被捕获。交替电场、磁场和多区域分段运动，需分析周期、轨迹与临界条件。"""  # noqa: E501
        self.standard_path = {
            "schema_version": 1,
            "source": "wuli.analysis.v2",
            "selected_path": "建立分段模型，用圆周运动关系反推捕获时刻。",
            "high_school_basis": ["动能定理", "洛伦兹力", "匀速圆周运动", "周期性"],
            "physical_stages": ["电场加速", "磁场偏转", "跨区域返回"],
            "reasoning_steps": ["确定阶段状态", "建立半径关系", "按周期枚举捕获时刻"],
            "decisive_relations": ["动能定理", "qvB=mv²/r", "T=2πm/(qB)", "相位满足捕获条件"],
            "representation_transforms": ["题干过程转换为分段状态模型", "磁场轨迹转换为圆几何"],
            "condition_checks": ["首次捕获", "所有可能时刻", "区域边界"],
            "type_distance": {
                "mode": "non_obvious_bridge",
                "archetype": "交替场中的分段圆周运动",
                "recognition_barrier": "需要把周期事件转换为轨迹几何",
                "novel_bridge": "用相位条件连接捕获时刻",
            },
            "student_step_count": 3,
        }

    def test_auto_assessment_has_six_weighted_dimensions(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.standard_path)
        self.assertEqual(assessment["status"], "default-accepted")
        self.assertEqual(assessment["rubric_version"], "objective-path-rubric.v8")
        self.assertEqual(assessment["dimension_scale"], {"min": 0, "max": 5, "step": 0.1})
        self.assertEqual(
            assessment["calibration_scale"],
            {"knowledge_depth_max": 6, "operational_max": 5},
        )
        self.assertEqual(len(assessment["dimensions"]), 6)
        self.assertEqual(
            [item["weight"] for item in assessment["dimensions"]],
            [20, 15, 20, 20, 10, 15],
        )
        self.assertEqual(assessment["dimensions"][2]["label"], "题型距离与建模转换")
        self.assertEqual(assessment["dimensions"][0]["scale_max"], 5.0)
        self.assertGreaterEqual(assessment["score"], 0)
        self.assertLessEqual(assessment["score"], 100)
        self.assertTrue(assessment["summary"])
        self.assertTrue(
            all(
                item["evidence"] and item["evidence"][0]["source"] and item["evidence"][0]["excerpt"]
                for item in assessment["dimensions"]
            )
        )
        self.assertEqual(assessment["auto_baseline"]["score"], assessment["score"])

    def test_teacher_edit_recomputes_total_and_validates_score_range(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.standard_path)
        edited = difficulty.normalize_teacher_edit(
            {
                "generated_at": assessment["generated_at"],
                "summary": "教师校准后的难度总结。",
                "teacher_note": "标准路径包含多阶段完备性核对。",
                "auto_baseline": assessment["auto_baseline"],
                "dimensions": [{**item, "score": 5.0} for item in assessment["dimensions"]],
            },
            self.problem,
            self.standard_path,
        )
        self.assertEqual(edited["status"], "teacher-edited")
        self.assertEqual(edited["score"], 100)
        self.assertEqual(edited["auto_baseline"], assessment["auto_baseline"])
        invalid = {**edited, "dimensions": [{**item, "score": 5.05} for item in edited["dimensions"]]}
        with self.assertRaisesRegex(ValueError, "步长为 0.1"):
            difficulty.normalize_teacher_edit(invalid, self.problem, self.standard_path)

    def test_teacher_edit_accepts_fractional_tenths(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.standard_path)
        edited = difficulty.normalize_teacher_edit(
            {
                "generated_at": assessment["generated_at"],
                "summary": "教师使用小数步长校准。",
                "teacher_note": "课堂观察表明六维负担接近。",
                "auto_baseline": assessment["auto_baseline"],
                "dimensions": [{**item, "score": 3.7} for item in assessment["dimensions"]],
            },
            self.problem,
            self.standard_path,
        )
        self.assertEqual(edited["score"], 74)
        self.assertTrue(all(item["score"] == 3.7 for item in edited["dimensions"]))

    def test_changed_teacher_scores_require_a_calibration_reason(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.standard_path)
        with self.assertRaisesRegex(ValueError, "教师校准依据"):
            difficulty.normalize_teacher_edit(
                {
                    "generated_at": assessment["generated_at"],
                    "summary": "教师校准后的难度总结。",
                    "auto_baseline": assessment["auto_baseline"],
                    "dimensions": [{**item, "score": 5.0} for item in assessment["dimensions"]],
                },
                self.problem,
                self.standard_path,
            )

    def test_digest_changes_only_with_problem_or_standard_path(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.standard_path)
        self.assertTrue(difficulty.current(assessment, self.problem, self.standard_path))
        changed_path = {
            **self.standard_path,
            "condition_checks": [*cast(list, self.standard_path["condition_checks"]), "补充边界条件"],
        }
        self.assertFalse(difficulty.current(assessment, self.problem, changed_path))
        self.assertFalse(difficulty.current(assessment, self.problem + "补充题设", self.standard_path))

    def test_missing_standard_path_does_not_invent_a_precise_score(self):
        assessment = difficulty.auto_assess(self.record, self.problem, None)
        self.assertEqual(assessment["status"], "awaiting-standard-path")
        self.assertIsNone(assessment["score"])
        self.assertEqual(assessment["dimensions"], [])

    def test_w3_blueprint_projects_to_same_objective_contract(self):
        blueprint = {
            "status": "completed",
            "question_targets": [{"id": "T1"}, {"id": "T2"}],
            "physical_stages": [
                {"label": "阶段一", "entry_conditions": ["进入区域"], "exit_conditions": ["到达边界"]},
                {"label": "阶段二", "entry_conditions": ["穿过边界"], "exit_conditions": ["首次返回"]},
            ],
            "stage_transitions": [{"from_stage": "P1", "to_stage": "P2", "event": "过边界"}],
            "reasoning_steps": [
                {
                    "id": "S1",
                    "operation": "建立轨迹圆几何模型",
                    "cognitive_operation": "apply",
                    "depends_on": [],
                    "target_ids": ["T1", "T2"],
                    "decisive_relations": ["qvB=mv²/r"],
                    "knowledge_units": [
                        {"id": "lorentz_force", "relation_indexes": [0]},
                        {"id": "circular_motion", "relation_indexes": [0]},
                    ],
                },
                {
                    "id": "S2",
                    "operation": "分类判断首次返回",
                    "cognitive_operation": "audit",
                    "depends_on": ["S1"],
                    "target_ids": ["T2"],
                    "decisive_relations": ["累计转角等于2π"],
                    "knowledge_units": [{"id": "geometry_constraint", "relation_indexes": [0]}],
                },
            ],
            "verification_obligations": [{"check": "枚举所有可能并核对首次返回"}],
            "type_distance": {
                "mode": "non_obvious_bridge",
                "archetype": "磁场圆周运动",
                "recognition_barrier": "需要识别首次返回的几何桥梁",
                "novel_bridge": "累计转角连接事件与位置",
            },
        }
        path = difficulty.standard_path_from_blueprint(blueprint)
        assessment = difficulty.auto_assess(self.record, self.problem, path)
        self.assertEqual(path["source"], "wuli.problem-decompose.v1")
        self.assertIsInstance(assessment["score"], int)
        self.assertTrue(
            all(item["evidence"][0]["source"] == "standard_solution_path" for item in assessment["dimensions"])
        )

    def test_answer_step_granularity_does_not_raise_objective_difficulty(self):
        compact = {
            **self.standard_path,
            "reasoning_steps": ["识别模型", "应用关系"],
            "decisive_relations": ["动能定理", "qvB=mv²/r"],
        }
        verbose = {
            **compact,
            "reasoning_steps": ["识别研究对象", "确定运动阶段", "识别模型", "应用关系"],
        }
        compact_result = difficulty.auto_assess(self.record, self.problem, compact)
        verbose_result = difficulty.auto_assess(self.record, self.problem, verbose)
        self.assertEqual(compact_result["score"], verbose_result["score"])
        self.assertEqual(
            compact_result["dimensions"][3]["score"],
            verbose_result["dimensions"][3]["score"],
        )

    def test_type_distance_uses_fixed_absolute_modes(self):
        simple_problem = "质量为 m 的物体受恒力 F，求加速度。"
        direct = {
            "source": "test",
            "selected_path": "直接应用牛顿第二定律。",
            "high_school_basis": ["牛顿第二定律"],
            "physical_stages": ["恒力作用"],
            "reasoning_steps": ["列式求解"],
            "decisive_relations": ["a=F/m"],
            "representation_transforms": [],
            "condition_checks": [],
            "type_distance": {
                "mode": "direct_archetype",
                "archetype": "牛顿第二定律教材母题",
                "recognition_barrier": "无",
                "novel_bridge": "",
            },
        }
        novel = {
            **direct,
            "type_distance": {
                "mode": "novel_construction",
                "archetype": "高中力学综合题",
                "recognition_barrier": "常规模型不能直接连接题设与目标",
                "novel_bridge": "需要构造隐藏约束",
            },
        }
        direct_result = difficulty.auto_assess({}, simple_problem, direct)
        novel_result = difficulty.auto_assess({}, simple_problem, novel)
        self.assertEqual(direct_result["dimensions"][2]["score"], 0.8)
        self.assertEqual(novel_result["dimensions"][2]["score"], 5.0)
        self.assertEqual(novel_result["score"] - direct_result["score"], 17)

    def test_routine_high_school_variant_stays_out_of_high_score_band(self):
        routine = {
            "source": "test",
            "selected_path": "换算电池容量，估算工作时间并判断能量转化。",
            "high_school_basis": ["电池容量定义", "电功率与能量", "非纯电阻电路"],
            "physical_stages": ["稳定工作状态"],
            "reasoning_steps": ["换算容量并估算时间", "判断电动机与能量转化"],
            "decisive_relations": ["t=Q/I", "电能转化为机械能和内能"],
            "representation_transforms": [],
            "condition_checks": [],
            "type_distance": {
                "mode": "routine_variant",
                "archetype": "电池容量与用电器",
                "recognition_barrier": "常规单位换算和概念辨析",
                "novel_bridge": "",
            },
        }
        result = difficulty.auto_assess(
            {},
            "某航拍设备使用额定电池和电动机，判断容量、工作时间与能量转化。",
            routine,
        )
        self.assertLessEqual(result["score"], 40)
        self.assertIn(result["level"], {"基础", "较易"})

    def test_inferred_type_distance_counts_unique_transform_categories(self):
        base = {
            "source": "test",
            "selected_path": "建立标准模型。",
            "high_school_basis": ["圆周运动"],
            "physical_stages": ["单一阶段"],
            "reasoning_steps": ["建立模型"],
            "decisive_relations": ["v²/r=a"],
            "condition_checks": [],
        }
        repeated = difficulty._normalize_path({
            **base,
            "representation_transforms": ["轨迹转换为圆几何", "再次核对轨迹圆几何"],
        })
        distinct = difficulty._normalize_path({
            **base,
            "representation_transforms": ["轨迹转换为圆几何", "空间运动转换为坐标分量"],
        })
        self.assertEqual(repeated["type_distance"]["mode"], "routine_variant")
        self.assertEqual(distinct["type_distance"]["mode"], "standard_transfer")

    def test_fixed_anchor_registry_contains_teacher_calibrated_a_to_o(self):
        scores = {item["id"]: item["score"] for item in difficulty.ANCHORS["anchors"]}
        self.assertEqual(
            scores,
            {
                "A": 2.0,
                "B": 3.0,
                "C": 1.0,
                "D": 3.3,
                "E": 2.3,
                "F": 3.7,
                "G": 3.2,
                "H": 2.7,
                "I": 2.2,
                "J": 2.2,
                "K": 5.2,
                "L": 4.9,
                "M": 4.6,
                "N": 4.8,
                "O": 4.2,
            },
        )

    def test_five_point_band_requires_verified_reconstruction_chain(self):
        graph = []
        for index in range(5):
            graph.append({
                "id": f"S{index + 1}",
                "operation": f"从基础定律形成中间结论 {index + 1}",
                "cognitive_operation": "reconstruct",
                "depends_on": [f"S{index}"] if index else [],
                "target_ids": ["T1"],
                "decisive_relations": [f"R{index + 1}"],
            })
        base = {
            **self.standard_path,
            "reasoning_steps": [item["operation"] for item in graph],
            "reasoning_graph": graph,
            "decisive_relations": [f"R{index + 1}" for index in range(5)],
            "verification": {"status": "unverified", "passed_target_ids": []},
        }
        unverified = difficulty.auto_assess({}, self.problem, base)
        verified = difficulty.auto_assess(
            {},
            self.problem,
            {
                **base,
                "verification": {
                    "status": "verified",
                    "passed_target_ids": ["T1"],
                },
            },
        )
        self.assertEqual(unverified["dimensions"][0]["score"], 4.8)
        self.assertEqual(verified["dimensions"][0]["score"], 5.0)
        self.assertEqual(verified["knowledge_depth_trace"]["calibrated_score"], 5.2)
        self.assertEqual(verified["knowledge_depth_trace"]["level_five_gate"], "passed")

    def test_knowledge_integration_counts_independent_bound_laws_not_broad_domains(self):
        relations = [
            "原子质量减去电子质量得到核质量",
            "E²=p²c²+m²c⁴",
            "初末态总线动量守恒",
            "连续能谱排除二体衰变模型",
            "反应前后电荷守恒",
            "总角动量由轨道角动量与自旋合成",
        ]
        unit_ids = [
            "atomic_nuclear_mass_conversion",
            "relativistic_energy_momentum",
            "linear_momentum_conservation",
            "experimental_model_inference",
            "charge_conservation",
            "angular_momentum_spin",
        ]
        path = {
            "source": "competition-standard-answer",
            "selected_path": "按标准答案联合六个不可绕过的物理单元。",
            "high_school_basis": ["近代物理"],
            "physical_stages": ["反应前", "反应后"],
            "reasoning_steps": ["建立完整守恒与实验约束"],
            "reasoning_graph": [
                {
                    "id": "S1",
                    "operation": "建立完整守恒与实验约束",
                    "cognitive_operation": "reconstruct",
                    "depends_on": [],
                    "target_ids": ["T1"],
                    "decisive_relations": relations,
                    "knowledge_units": [
                        {"id": unit_id, "relation_indexes": [index]} for index, unit_id in enumerate(unit_ids)
                    ],
                }
            ],
            "decisive_relations": relations,
            "representation_transforms": [],
            "condition_checks": [],
            "type_distance": {
                "mode": "non_obvious_bridge",
                "archetype": "竞赛近代物理综合题",
                "recognition_barrier": "需识别实验事实与多种守恒律的联合约束",
                "novel_bridge": "用连续谱检验反应模型",
            },
            "verification": {"status": "verified", "passed_target_ids": ["T1"]},
        }
        result = difficulty.auto_assess({}, "按标准答案分析该核反应。", path)
        integration = next(item for item in result["dimensions"] if item["id"] == "knowledge_integration")
        self.assertEqual(integration["score"], 4.8)
        self.assertEqual(result["knowledge_integration_trace"]["count"], 6)
        self.assertIn(
            "线动量守恒",
            [item["label"] for item in result["knowledge_integration_trace"]["units"]],
        )
        self.assertIn(
            "角动量守恒与自旋合成",
            [item["label"] for item in result["knowledge_integration_trace"]["units"]],
        )

    def test_same_law_reused_in_components_counts_once_and_parallel_law_counts(self):
        path = {
            **self.standard_path,
            "reasoning_steps": ["列水平动量", "列竖直动量", "列角动量", "联合求解"],
            "reasoning_graph": [
                {
                    "id": "S1",
                    "operation": "列水平动量",
                    "cognitive_operation": "apply",
                    "depends_on": [],
                    "target_ids": ["T1"],
                    "decisive_relations": ["Σp_x 初=Σp_x 末"],
                    "knowledge_units": [
                        {
                            "id": "linear_momentum_conservation",
                            "relation_indexes": [0],
                        }
                    ],
                },
                {
                    "id": "S2",
                    "operation": "列竖直动量",
                    "cognitive_operation": "apply",
                    "depends_on": [],
                    "target_ids": ["T1"],
                    "decisive_relations": ["Σp_y 初=Σp_y 末"],
                    "knowledge_units": [
                        {
                            "id": "linear_momentum_conservation",
                            "relation_indexes": [0],
                        }
                    ],
                },
                {
                    "id": "S3",
                    "operation": "列角动量",
                    "cognitive_operation": "apply",
                    "depends_on": [],
                    "target_ids": ["T1"],
                    "decisive_relations": ["L初=L末"],
                    "knowledge_units": [
                        {
                            "id": "angular_momentum_spin",
                            "relation_indexes": [0],
                        }
                    ],
                },
                {
                    "id": "S4",
                    "operation": "联立三个必要分支",
                    "cognitive_operation": "algebra_only",
                    "depends_on": ["S1", "S2", "S3"],
                    "target_ids": ["T1"],
                    "decisive_relations": [],
                    "knowledge_units": [],
                },
            ],
            "decisive_relations": ["Σp_x 初=Σp_x 末", "Σp_y 初=Σp_y 末", "L初=L末"],
        }
        result = difficulty.auto_assess({}, self.problem, path)
        trace = result["knowledge_integration_trace"]
        self.assertEqual(trace["count"], 2)
        self.assertEqual(
            {item["id"] for item in trace["units"]},
            {"linear_momentum_conservation", "angular_momentum_spin"},
        )

    def test_w3_post_solve_projection_uses_solver_evidence_before_blueprint_fallback(self):
        blueprint = {
            "status": "completed",
            "question_targets": [{"id": "T1"}],
            "physical_stages": [{"label": "单一反应过程"}],
            "stage_transitions": [],
            "reasoning_steps": [
                {
                    "id": "S1",
                    "operation": "尝试列出可能的守恒关系",
                    "depends_on": [],
                    "target_ids": ["T1"],
                    "decisive_relations": ["线动量守恒"],
                }
            ],
            "verification_obligations": [],
        }
        report = {
            "solver_a": {
                "targets": [
                    {
                        "id": "T1",
                        "supporting_relations": ["总角动量由轨道角动量与自旋合成"],
                    }
                ]
            }
        }
        path = difficulty.standard_path_from_blueprint(blueprint, report)
        result = difficulty.auto_assess({}, "分析该反应。", path)
        trace = result["knowledge_integration_trace"]
        self.assertEqual(trace["source"], "post-solve-deterministic-projection")
        self.assertEqual(trace["count"], 1)
        self.assertEqual(trace["units"][0]["id"], "angular_momentum_spin")

    def test_minimum_sufficient_modules_collapse_core_model_but_keep_angle_time(self):
        units = [
            {"id": "lorentz_force", "label": "洛伦兹力"},
            {"id": "circular_motion", "label": "圆周运动关系"},
            {"id": "geometry_constraint", "label": "几何约束"},
        ]
        basic = difficulty._minimum_sufficient_knowledge_units(
            units,
            ["qvB=mv²/r", "轨迹与边界相切"],
        )
        self.assertEqual(
            {item["id"] for item in basic},
            {"magnetic_circular_motion", "geometry_constraint"},
        )
        timed = difficulty._minimum_sufficient_knowledge_units(
            units,
            ["qvB=mv²/r", "由非标准圆心角得到飞行时间t=Δφ/ω"],
        )
        self.assertEqual(
            {item["id"] for item in timed},
            {
                "magnetic_circular_motion",
                "geometry_constraint",
                "trajectory_angle_time",
            },
        )
        released = difficulty._minimum_sufficient_knowledge_units(
            units,
            ["qvB=mv²/r", "由非标准圆心角得到飞行时间", "筛选释放时刻"],
        )
        self.assertIn(
            "external_phase_timing",
            {item["id"] for item in released},
        )

    def test_minimum_sufficient_modules_preserve_independent_laws_only(self):
        momentum = difficulty._minimum_sufficient_knowledge_units(
            [{"id": "linear_momentum_conservation", "label": "线动量守恒"}],
            ["水平方向动量守恒", "竖直方向动量守恒"],
        )
        self.assertEqual(
            {item["id"] for item in momentum},
            {"linear_momentum_conservation"},
        )
        momentum_and_spin = difficulty._minimum_sufficient_knowledge_units(
            momentum,
            ["线动量守恒", "角动量守恒与自旋合成"],
        )
        self.assertEqual(
            {item["id"] for item in momentum_and_spin},
            {"linear_momentum_conservation", "angular_momentum_spin"},
        )
        competition = difficulty._minimum_sufficient_knowledge_units(
            [],
            [
                "由原子质量换算核质量",
                "使用相对论质能和能量动量关系",
                "全过程线动量守恒",
                "连续谱实验事实排除二体模型",
                "电荷守恒",
                "角动量与自旋合成",
            ],
        )
        self.assertEqual(len(competition), 6)

    def test_ampere_force_subsumes_microscopic_lorentz_model_for_a_wire(self):
        units = difficulty._minimum_sufficient_knowledge_units(
            [
                {"id": "lorentz_force", "label": "洛伦兹力"},
                {"id": "ampere_force", "label": "安培力模型"},
            ],
            ["通电导体棒所受安培力F安=BIL"],
        )
        self.assertEqual({item["id"] for item in units}, {"ampere_force"})

    def test_repeated_process_stages_and_audit_checklists_do_not_manufacture_fives(self):
        process = difficulty._process_profile(
            [
                "第一次进入电场",
                "第一次进入磁场",
                "第二次进入电场",
                "第二次进入磁场",
                "第三次进入磁场",
            ],
            [],
        )
        self.assertEqual(process["count"], 2)
        self.assertEqual(process["level"], "标准串联")
        self.assertEqual(process["score"], 1.6)
        conditions = difficulty._condition_profile(["方向与符号", "范围与边界", "首次性", "枚举完备性"])
        self.assertLessEqual(conditions["score"], 3.7)

    def test_process_composition_anchors_timing_sync_branch_and_nested_topology(self):
        temporal_graph = [
            {
                "id": "S1",
                "operation": "以释放时刻参数化首段截断运动",
                "depends_on": [],
                "target_ids": ["T1"],
                "decisive_relations": ["首段剩余时间=切换时刻-释放时刻"],
            },
            {
                "id": "S2",
                "operation": "传播到下一周期并检查相位",
                "depends_on": ["S1"],
                "target_ids": ["T1"],
                "decisive_relations": ["到达时刻必须落入电场时窗"],
            },
        ]
        temporal = difficulty._process_profile(
            ["完整电场段", "释放后的截断段"],
            [],
            temporal_graph,
            ["释放时刻决定首段剩余时间", "到达时刻落入时窗"],
        )
        self.assertEqual(temporal["level"], "时序组合")
        self.assertEqual(temporal["base_score"], 2.5)

        synchronous = difficulty._process_profile(
            ["电子在磁场中作螺旋运动"],
            ["空间到坐标模型"],
            temporal_graph,
            ["横向回旋与轴向匀速必须同步到首次碰壁时刻"],
        )
        self.assertEqual(synchronous["level"], "同步耦合")
        self.assertGreaterEqual(synchronous["score"], 3.4)

        branch_graph = [
            {
                "id": "B1",
                "operation": "建立共同初态",
                "depends_on": [],
                "target_ids": ["T1"],
                "decisive_relations": ["初态确定"],
            },
            {
                "id": "B2",
                "operation": "求路径分支一",
                "depends_on": ["B1"],
                "target_ids": ["T1"],
                "decisive_relations": ["分支一末态"],
            },
            {
                "id": "B3",
                "operation": "求路径分支二",
                "depends_on": ["B1"],
                "target_ids": ["T1"],
                "decisive_relations": ["分支二末态"],
            },
            {
                "id": "B4",
                "operation": "合并两类候选并证明完整性",
                "depends_on": ["B2", "B3"],
                "target_ids": ["T1"],
                "decisive_relations": ["所有可能路径均已覆盖"],
            },
        ]
        branch = difficulty._process_profile(
            ["路径分支一", "路径分支二"],
            [],
            branch_graph,
            ["合并候选并筛选"],
        )
        self.assertEqual(branch["level"], "分支耦合")
        self.assertGreaterEqual(branch["score"], 4.2)

        nested = difficulty._process_profile(
            ["两个粒子的路径分支一", "两个粒子的路径分支二"],
            ["三维同步"],
            branch_graph,
            ["共同旋转中心必须同时满足，最终证明所有分支完整性"],
        )
        self.assertEqual(nested["level"], "嵌套全局组合")
        self.assertEqual(nested["score"], 5.0)

    def test_calculation_load_follows_necessary_chain_not_formula_line_count(self):
        direct = difficulty._calculation_profile(
            [
                {
                    "id": "S1",
                    "operation": "代入求速度",
                    "depends_on": [],
                    "target_ids": ["T1"],
                    "decisive_relations": ["v=s/t"],
                }
            ],
            ["v=s/t"],
        )
        self.assertEqual(direct["base_score"], 0.8)

        parametric_graph = []
        for index, (operation, relation) in enumerate([
            ("求允许发射角范围", "sinα≥R/(2r)"),
            ("建立首次碰壁相位函数", "φ=2arcsin[R/(2r sinα)]"),
            ("求首次碰壁时间", "t(α)=φ(α)/ω"),
            ("建立轴向落点函数", "z(α)=v cosα·t(α)"),
            ("求落点函数边界", "z_min≤z(α)≤z_max"),
        ]):
            parametric_graph.append({
                "id": f"P{index + 1}",
                "operation": operation,
                "depends_on": [f"P{index}"] if index else [],
                "target_ids": ["T1"],
                "decisive_relations": [relation],
            })
        parametric = difficulty._calculation_profile(
            parametric_graph,
            [item["decisive_relations"][0] for item in parametric_graph],
        )
        self.assertEqual(parametric["level"], "较大计算量")
        self.assertGreaterEqual(parametric["score"], 3.4)

    def test_condition_profile_uses_only_necessary_bound_audit_nodes(self):
        graph = [
            {
                "id": "S1",
                "operation": "建立主线关系",
                "cognitive_operation": "apply",
                "depends_on": [],
                "target_ids": ["T1"],
                "decisive_relations": ["x=vt"],
            },
            {
                "id": "S2",
                "operation": "证明释放时刻解集的完整性与唯一性",
                "cognitive_operation": "audit",
                "depends_on": ["S1"],
                "target_ids": ["T1"],
                "decisive_relations": ["全部候选必须同时满足空间命中与时间窗口"],
            },
        ]
        profile = difficulty._condition_profile(
            ["完备性"],
            ["x=vt"],
            "wuli.problem-decompose.v1",
            graph,
        )
        self.assertEqual(profile["source"], "critical-audit-evidence")
        self.assertGreaterEqual(profile["score"], 4.2)
        self.assertLessEqual(profile["score"], 4.4)

    def test_many_audit_steps_refine_but_do_not_raise_depth_to_competition_band(self):
        graph = []
        for index in range(7):
            graph.append({
                "id": f"S{index + 1}",
                "operation": f"核对条件 {index + 1}",
                "cognitive_operation": "audit",
                "depends_on": [f"S{index}"] if index else [],
                "target_ids": ["T1"],
                "decisive_relations": [f"条件关系 {index + 1}"],
            })
        path = {
            **self.standard_path,
            "reasoning_steps": [item["operation"] for item in graph],
            "reasoning_graph": graph,
            "decisive_relations": [item["decisive_relations"][0] for item in graph],
        }
        result = difficulty.auto_assess({}, self.problem, path)
        depth = next(item for item in result["dimensions"] if item["id"] == "knowledge_depth")
        self.assertLess(depth["score"], 4.0)
        self.assertGreaterEqual(depth["score"], 3.0)


if __name__ == "__main__":
    unittest.main()
