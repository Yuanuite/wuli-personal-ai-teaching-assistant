#!/usr/bin/env python3
"""Build the isolated third fresh holdout and evaluation-only evidence overlay."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import evidence_agent  # noqa: E402
import evidence_contract  # noqa: E402
import evidence_evaluation  # noqa: E402


BATCH_ID = "evidence-holdout-2026-07-31-c"
DATASET_ID = "wuli-evidence-fresh-holdout-v3"
OVERLAY_PATH = (
    "teacher-console/tests/fixtures/evidence-agent/"
    "fresh-holdout-v3-evidence-overlay.json"
)
PRIOR_DATASETS = (
    ROOT / "student-error-library/evals/evidence-gold-calibration.json",
    ROOT / "student-error-library/evals/evidence-gold-holdout.json",
    ROOT / "teacher-console/tests/fixtures/evidence-agent/repair-replay-v1.json",
    ROOT / "student-error-library/evals/evidence-gold-holdout-v2.json",
    ROOT / "teacher-console/tests/fixtures/evidence-agent/warning-scope-repair-replay-v1.json",
)


def _evidence_id(source_id: str) -> str:
    digest = hashlib.sha256(
        f"fresh-holdout-v3:{source_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"EU-{digest}"


def evidence_unit(
    source_id: str,
    *,
    text: str,
    facets: list[str],
    applicability: list[str],
    exceptions: list[str],
) -> dict[str, Any]:
    return evidence_contract.normalize_evidence_unit(
        {
            "evidence_id": _evidence_id(source_id),
            "unit_kind": "secondary_conclusion",
            "source_kind": "curated_technique",
            "source_locator": {
                "path": OVERLAY_PATH,
                "section": source_id,
                "start_line": 1,
                "end_line": 1,
            },
            "text": text,
            "physics_facets": facets,
            "applicability": applicability,
            "exceptions": exceptions,
            "authority_level": "A",
        }
    )


def units() -> list[dict[str, Any]]:
    return [
        evidence_unit(
            "circular-motion-radial-force-ledger",
            text=(
                "圆周运动中的向心力是所有实际力沿指向圆心方向的合力，不是额外增加"
                "的一种力；在惯性系中应列 ΣF径向=mv²/r。匀速圆周运动只是速率不变，"
                "速度方向和加速度仍在变化，合力不为零。"
            ),
            facets=["圆周运动", "径向合力", "向心加速度", "实际力", "匀速圆周运动"],
            applicability=["物体在所研究瞬间沿圆弧运动", "参考系为惯性系", "圆心方向明确"],
            exceptions=[
                "把向心力与重力或支持力重复计入",
                "把匀速圆周运动误判为合力为零",
                "物体离开圆形约束后仍套用径向合力公式",
            ],
        ),
        evidence_unit(
            "horizontal-projectile-component-independence",
            text=(
                "忽略空气阻力、取重力加速度恒定时，平抛运动可分解为水平方向匀速"
                "x=v0t 和竖直方向自由落体 y=gt²/2；同一竖直落差的飞行时间由竖直"
                "运动决定，与水平初速度无关，落地速度必须由两个分量合成。"
            ),
            facets=["平抛运动", "运动独立性", "水平初速度", "竖直落差", "飞行时间", "速度合成"],
            applicability=["初速度水平", "忽略空气阻力", "重力加速度恒定"],
            exceptions=[
                "存在不可忽略空气阻力",
                "初速度并非水平方向",
                "只用水平速度大小代替落地合速度",
            ],
        ),
        evidence_unit(
            "ideal-gas-fixed-amount-state-law",
            text=(
                "一定质量的理想气体在两个平衡态之间满足 pV/T 为常量，其中 T 必须使用"
                "热力学温度；只有气体物质的量不变时才能直接比较。等温、等容、等压"
                "过程分别是该关系在相应约束下的特例。"
            ),
            facets=["理想气体", "一定质量", "平衡态", "压强", "体积", "热力学温度"],
            applicability=["气体可视为理想气体", "物质的量不变", "比较确定的平衡态"],
            exceptions=[
                "把摄氏温度直接代入状态方程比例",
                "容器漏气后仍按一定质量处理",
                "非平衡瞬态仍直接使用统一压强和温度",
            ],
        ),
        evidence_unit(
            "motional-emf-effective-component",
            text=(
                "直导体切割匀强磁场时，动生电动势由有效长度和垂直切割分量决定；"
                "当导体、速度和磁场两两垂直时 ε=BLv，一般共面情形可写"
                "ε=BLv sinθ。开路仍可有电动势，但只有闭合回路才有持续电流。"
            ),
            facets=["动生电动势", "有效长度", "速度垂直分量", "匀强磁场", "闭合回路", "感应电流"],
            applicability=["直导体切割匀强磁场", "有效切割长度明确", "速度与磁场夹角明确"],
            exceptions=[
                "斜切磁场仍直接使用全部速度",
                "开路存在电动势就声称有持续电流",
                "转动导体或非匀强磁场仍直接套 BLv",
            ],
        ),
        evidence_unit(
            "ideal-spring-shm-energy-phase",
            text=(
                "理想轻弹簧振子做简谐运动时 a=-ω²x，总机械能 E=kA²/2。经过平衡位置"
                "时速度最大、加速度为零；到达振幅端点时速度为零、加速度大小最大，"
                "动能与弹性势能在运动中相互转化。"
            ),
            facets=["简谐运动", "位移", "加速度", "平衡位置", "振幅端点", "机械能"],
            applicability=["理想轻弹簧振子", "阻力可忽略", "形变在线性弹性范围"],
            exceptions=[
                "把平衡位置误判为速度和加速度都最大",
                "把振幅端点误判为加速度为零",
                "存在明显阻尼仍令振幅和机械能恒定",
            ],
        ),
        evidence_unit(
            "total-internal-reflection-boundary",
            text=(
                "全反射只可能发生在光从折射率较大的介质射向折射率较小的介质时，"
                "且入射角大于临界角 C；临界角满足 sinC=n2/n1。入射角等于临界角时"
                "折射光沿界面传播，从光疏介质射向光密介质不会发生全反射。"
            ),
            facets=["全反射", "光密介质", "光疏介质", "入射角", "临界角", "折射率"],
            applicability=["两种均匀透明介质的清晰界面", "光从高折射率介质射向低折射率介质"],
            exceptions=[
                "光从光疏介质射向光密介质却声称全反射",
                "入射角小于临界角却声称全反射",
                "折射率连续变化介质仍直接套单一界面临界角",
            ],
        ),
        evidence_unit(
            "radioactive-half-life-statistical-law",
            text=(
                "大量同种放射性原子核的剩余数满足 N=N0·2^(-t/T)，半衰期 T 由核素"
                "本身决定，通常不随温度、压强或化学状态改变；初始核数只改变衰变"
                "数量，不改变半衰期。统计规律不能预言某一个原子核的确切衰变时刻。"
            ),
            facets=["放射性衰变", "半衰期", "剩余核数", "统计规律", "温度", "初始核数"],
            applicability=["大量同种放射性原子核", "只考虑自发衰变", "半衰期在研究期间稳定"],
            exceptions=[
                "升温或加压就改变核素半衰期",
                "样品质量增加就改变半衰期",
                "外来粒子轰击引发核反应仍只套自发衰变公式",
            ],
        ),
    ]


def proposed_case(
    case_id: str,
    evidence_id: str,
    *,
    problem: str,
    purpose: str,
    question: str,
    facets: list[str],
    sufficient: bool,
    rationale: str,
    forbidden_conflicts: list[str] | None = None,
    diagnostic_targets: list[str] | None = None,
) -> dict[str, Any]:
    need: dict[str, Any] = {
        "schema": "wuli.retrieval-need.v1",
        "need_id": "N1",
        "purpose": purpose,
        "question": question,
        "required_facets": facets,
        "forbidden_conflicts": forbidden_conflicts or [],
        "minimum_authority": "A",
        "criticality": "required",
        "target_ids": ["Q1"],
        "stage_ids": ["P1"],
        "obligation_ids": ["V1"],
    }
    if diagnostic_targets:
        need["diagnostic_targets"] = diagnostic_targets
    return {
        "problem": problem,
        "blueprint": {
            "schema": "wuli.evidence-holdout-blueprint.v1",
            "question_targets": ["Q1"],
            "verification_obligations": ["V1"],
        },
        "gold_case": {
            "schema": "wuli.evidence-gold-case.v1",
            "case_id": case_id,
            "question_snapshot_hash": evidence_contract.stable_fingerprint(
                "question-snapshot-v1", problem
            ),
            "retrieval_need": need,
            "required_evidence_ids": [evidence_id] if sufficient else [],
            "acceptable_evidence_ids": [evidence_id] if sufficient else [],
            "forbidden_evidence_ids": [] if sufficient else [evidence_id],
            "expected_status": "sufficient" if sufficient else "insufficient",
            "teacher_rationale": rationale,
            "evaluation_split": "holdout",
            "batch_id": BATCH_ID,
        },
    }


def cases(unit_by_section: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    circle = unit_by_section["circular-motion-radial-force-ledger"]["evidence_id"]
    projectile = unit_by_section["horizontal-projectile-component-independence"]["evidence_id"]
    gas = unit_by_section["ideal-gas-fixed-amount-state-law"]["evidence_id"]
    emf = unit_by_section["motional-emf-effective-component"]["evidence_id"]
    shm = unit_by_section["ideal-spring-shm-energy-phase"]["evidence_id"]
    tir = unit_by_section["total-internal-reflection-boundary"]["evidence_id"]
    decay = unit_by_section["radioactive-half-life-statistical-law"]["evidence_id"]
    return [
        proposed_case(
            "fresh3-circle-radial-force-valid", circle,
            problem="小球在水平圆盘上随圆盘做匀速圆周运动，需要列出水平方向的径向动力学方程。",
            purpose="method_candidate",
            question="应怎样用实际力的径向分量建立向心运动方程？",
            facets=["径向合力", "实际力", "向心加速度"],
            sufficient=True,
            rationale="证据直接给出实际力径向合力等于 mv²/r 的记账方式。",
        ),
        proposed_case(
            "fresh3-circle-zero-result-diagnostic", circle,
            problem="某解答认为物体做匀速圆周运动时速度大小不变，所以加速度和合力都为零。",
            purpose="false_friend_check",
            question="速率不变是否意味着圆周运动的加速度和合力为零？",
            facets=["匀速圆周运动", "向心加速度", "径向合力"],
            sufficient=True,
            rationale="证据明确区分速率不变与速度方向变化，可直接纠正该结论。",
            diagnostic_targets=["把匀速圆周运动误判为合力为零"],
        ),
        proposed_case(
            "fresh3-circle-detached-trajectory-insufficient", circle,
            problem="小球已经脱离圆形轨道，之后做二维运动；现在需要求落地点位置和飞行时间。",
            purpose="method_candidate",
            question="脱离约束后的二维轨迹、飞行时间和落地点怎样确定？",
            facets=["脱离后轨迹", "飞行时间", "落地点"],
            sufficient=False,
            rationale="圆周径向合力证据不包含脱离后的二维运动分解和落点计算。",
            forbidden_conflicts=["物体离开圆形约束后仍套用径向合力公式"],
        ),
        proposed_case(
            "fresh3-projectile-time-valid", projectile,
            problem="两个小球从同一高度同时水平抛出，水平初速度不同，忽略空气阻力。",
            purpose="verification_support",
            question="两球落地时间是否相同，由哪个方向的运动决定？",
            facets=["竖直落差", "飞行时间", "水平初速度"],
            sufficient=True,
            rationale="同一落差下飞行时间由竖直自由落体决定，与水平初速度无关。",
        ),
        proposed_case(
            "fresh3-projectile-horizontal-speed-diagnostic", projectile,
            problem="某解答声称平抛物体水平初速度越大，在同一高度下落地所需时间越长。",
            purpose="false_friend_check",
            question="水平初速度会不会改变同一落差下的平抛飞行时间？",
            facets=["运动独立性", "水平初速度", "飞行时间"],
            sufficient=True,
            rationale="证据可以直接纠正水平速度决定下落时间的错误。",
            diagnostic_targets=["把水平初速度当作同一落差飞行时间的决定量"],
        ),
        proposed_case(
            "fresh3-projectile-drag-insufficient", projectile,
            problem="小球在强空气阻力中运动，阻力大小与速度成正比，需要求速度随时间的变化和落地时间。",
            purpose="method_candidate",
            question="速度相关阻力下应怎样建立动力学方程并确定落地时间？",
            facets=["速度相关阻力", "动力学方程", "落地时间"],
            sufficient=False,
            rationale="无阻力平抛分解不包含速度相关阻力的动力学模型。",
            forbidden_conflicts=["存在不可忽略空气阻力"],
        ),
        proposed_case(
            "fresh3-gas-two-state-valid", gas,
            problem="密闭容器内一定质量理想气体从一个平衡态缓慢变化到另一个平衡态，已知两态的 p、V、T 部分数据。",
            purpose="method_candidate",
            question="一定质量理想气体的两个平衡态应使用什么状态关系比较？",
            facets=["一定质量", "平衡态", "压强", "体积", "热力学温度"],
            sufficient=True,
            rationale="物质的量固定且两端均为平衡态，可直接使用 pV/T 常量。",
        ),
        proposed_case(
            "fresh3-gas-celsius-diagnostic", gas,
            problem="某解答把 20℃ 和 40℃ 直接当作温度比 1:2 代入理想气体状态方程。",
            purpose="false_friend_check",
            question="状态方程中的温度能否直接使用摄氏温度数值作比例？",
            facets=["理想气体", "热力学温度", "状态方程比例"],
            sufficient=True,
            rationale="证据明确要求使用热力学温度，可直接纠正摄氏温度比例错误。",
            diagnostic_targets=["把摄氏温度直接代入状态方程比例"],
        ),
        proposed_case(
            "fresh3-gas-leak-insufficient", gas,
            problem="带微孔容器持续漏气，容器内气体物质的量随时间改变，需要求漏气质量与压强的时间关系。",
            purpose="method_candidate",
            question="物质的量持续变化时如何联立漏气速率与压强演化？",
            facets=["漏气速率", "物质的量变化", "压强演化"],
            sufficient=False,
            rationale="固定物质的量的两态关系不包含漏气速率或开放系统质量变化。",
            forbidden_conflicts=["容器漏气后仍按一定质量处理"],
        ),
        proposed_case(
            "fresh3-emf-perpendicular-valid", emf,
            problem="长为 L 的直导体棒以速度 v 垂直切割匀强磁场，导体、速度和磁场两两垂直。",
            purpose="method_candidate",
            question="该条件下如何确定导体两端的动生电动势？",
            facets=["动生电动势", "有效长度", "匀强磁场", "速度垂直分量"],
            sufficient=True,
            rationale="两两垂直满足 ε=BLv 的直接适用条件。",
        ),
        proposed_case(
            "fresh3-emf-open-current-diagnostic", emf,
            problem="导体棒在磁场中切割磁感线但回路开路，某解答仍声称回路中存在持续感应电流。",
            purpose="false_friend_check",
            question="开路时存在动生电动势是否等于存在持续感应电流？",
            facets=["动生电动势", "闭合回路", "感应电流"],
            sufficient=True,
            rationale="证据明确区分开路电动势与闭合回路持续电流。",
            diagnostic_targets=["开路存在电动势就声称有持续电流"],
        ),
        proposed_case(
            "fresh3-emf-rotating-nonuniform-insufficient", emf,
            problem="不规则导体在空间非匀强磁场中绕偏心轴转动，需要求各段电动势分布和总电动势。",
            purpose="method_candidate",
            question="非匀强场内转动导体的局部电动势怎样沿导体汇总？",
            facets=["非匀强磁场", "转动导体", "局部电动势分布"],
            sufficient=False,
            rationale="直导体匀强场的有效分量公式不包含非匀强转动情形。",
            forbidden_conflicts=["转动导体或非匀强磁场仍直接套 BLv"],
        ),
        proposed_case(
            "fresh3-shm-equilibrium-valid", shm,
            problem="理想水平弹簧振子经过平衡位置，需要比较此刻速度和加速度的大小。",
            purpose="verification_support",
            question="简谐振子经过平衡位置时速度和加速度分别怎样？",
            facets=["简谐运动", "平衡位置", "速度", "加速度"],
            sufficient=True,
            rationale="证据直接给出平衡位置速度最大、加速度为零。",
        ),
        proposed_case(
            "fresh3-shm-endpoint-diagnostic", shm,
            problem="某解答认为弹簧振子到达振幅端点时位移最大，所以速度和加速度都为零。",
            purpose="false_friend_check",
            question="振幅端点的速度和加速度是否都为零？",
            facets=["振幅端点", "速度", "加速度"],
            sufficient=True,
            rationale="证据明确端点速度为零但加速度大小最大，可直接纠正。",
            diagnostic_targets=["把振幅端点误判为加速度为零"],
        ),
        proposed_case(
            "fresh3-shm-damping-insufficient", shm,
            problem="振子受到与速度成正比的明显阻尼，需要求振幅衰减率和机械能随时间的函数。",
            purpose="method_candidate",
            question="阻尼系数给定时振幅和机械能如何随时间衰减？",
            facets=["阻尼系数", "振幅衰减率", "机械能时间函数"],
            sufficient=False,
            rationale="理想无阻尼简谐运动证据不包含阻尼衰减规律。",
            forbidden_conflicts=["存在明显阻尼仍令振幅和机械能恒定"],
        ),
        proposed_case(
            "fresh3-tir-critical-valid", tir,
            problem="光从折射率较大的均匀介质射向折射率较小的均匀介质，入射角大于临界角。",
            purpose="applicability_check",
            question="该界面发生全反射需要满足哪些方向和角度条件？",
            facets=["光密介质", "光疏介质", "入射角", "临界角", "全反射"],
            sufficient=True,
            rationale="传播方向和入射角均满足全反射条件。",
        ),
        proposed_case(
            "fresh3-tir-thin-to-dense-diagnostic", tir,
            problem="光从空气射入玻璃，某解答因入射角很大就判断发生全反射。",
            purpose="false_friend_check",
            question="光从光疏介质射向光密介质时能否仅凭入射角大判定全反射？",
            facets=["光疏介质", "光密介质", "全反射"],
            sufficient=True,
            rationale="证据明确此传播方向不会发生全反射，可直接纠正。",
            diagnostic_targets=["光从光疏介质射向光密介质却声称全反射"],
        ),
        proposed_case(
            "fresh3-tir-gradient-index-insufficient", tir,
            problem="介质折射率随深度连续变化，光线逐渐弯曲，需要求转向点位置和完整传播轨迹。",
            purpose="method_candidate",
            question="连续折射率分布中光线轨迹和转向点怎样确定？",
            facets=["折射率连续变化", "光线轨迹", "转向点"],
            sufficient=False,
            rationale="单一清晰界面的临界角证据不包含连续梯度介质的轨迹模型。",
            forbidden_conflicts=["折射率连续变化介质仍直接套单一界面临界角"],
        ),
        proposed_case(
            "fresh3-decay-half-life-valid", decay,
            problem="一批大量同种放射性原子核经过三个半衰期，需要求剩余核数占初始核数的比例。",
            purpose="method_candidate",
            question="经过若干半衰期后剩余核数怎样计算？",
            facets=["放射性衰变", "半衰期", "剩余核数", "初始核数"],
            sufficient=True,
            rationale="证据直接给出大量同种核的指数衰变关系。",
        ),
        proposed_case(
            "fresh3-decay-temperature-diagnostic", decay,
            problem="某解答认为把放射性样品加热到高温就会显著缩短该核素的半衰期。",
            purpose="false_friend_check",
            question="通常的温度变化会不会改变核素自身的半衰期？",
            facets=["半衰期", "核素", "温度"],
            sufficient=True,
            rationale="证据明确半衰期通常不随温度变化，可直接纠正。",
            diagnostic_targets=["升温或加压就改变核素半衰期"],
        ),
        proposed_case(
            "fresh3-decay-bombardment-insufficient", decay,
            problem="样品同时受到高强度中子轰击并发生核反应，需要求反应产物生成率与原核数变化。",
            purpose="method_candidate",
            question="外来粒子诱发核反应时如何由反应截面和通量求产物生成率？",
            facets=["反应截面", "粒子通量", "核反应产物生成率"],
            sufficient=False,
            rationale="自发衰变统计律不包含外来粒子诱发反应的截面和通量。",
            forbidden_conflicts=["外来粒子轰击引发核反应仍只套自发衰变公式"],
        ),
    ]


def prior_evidence_ids() -> set[str]:
    result: set[str] = set()
    for path in PRIOR_DATASETS:
        dataset = evidence_evaluation.normalize_gold_dataset(
            json.loads(path.read_text(encoding="utf-8"))
        )
        for item in dataset["cases"]:
            gold = item["gold_case"]
            for field in (
                "required_evidence_ids",
                "acceptable_evidence_ids",
                "forbidden_evidence_ids",
            ):
                result.update(gold[field])
    return result


def build() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    overlay_units = units()
    by_section = {
        item["source_locator"]["section"]: item for item in overlay_units
    }
    new_ids = {item["evidence_id"] for item in overlay_units}
    overlap = prior_evidence_ids() & new_ids
    if overlap:
        raise ValueError(
            f"fresh overlay reuses revealed Evidence IDs: {sorted(overlap)}"
        )
    overlay = evidence_evaluation.normalize_evidence_overlay(
        {
            "schema": "wuli.evidence-overlay.v1",
            "overlay_id": DATASET_ID,
            "review_status": "draft",
            "source_scope": ["curated_technique"],
            "units": overlay_units,
        }
    )
    dataset = evidence_evaluation.normalize_gold_dataset(
        {
            "schema": "wuli.evidence-gold-dataset.v1",
            "dataset_id": DATASET_ID,
            "dataset_version": "2026-07-31-v1",
            "review_status": "draft",
            "label_origin": "agent_proposed_fresh_holdout_v3",
            "source_scope": ["curated_technique"],
            "reviewer": "",
            "reviewed_at": "",
            "evidence_snapshot_fingerprint": overlay[
                "overlay_fingerprint"
            ],
            "cases": cases(by_section),
        }
    )
    rows = []
    for item in dataset["cases"]:
        gold = item["gold_case"]
        _projection, pool = evidence_agent.load_projection_and_candidates(
            ROOT / "student-error-library",
            [gold["retrieval_need"]],
            exclude_entry_id="fresh-holdout-v3-preflight",
            top_k_per_need=5,
            source_kinds=("curated_technique",),
            projection_overlay=overlay_units,
        )
        ranked = [
            unit["evidence_id"] for unit in pool.get("candidates", [])
        ]
        expected_ids = (
            gold["required_evidence_ids"]
            if gold["expected_status"] == "sufficient"
            else gold["forbidden_evidence_ids"]
        )
        missing = sorted(set(expected_ids) - set(ranked))
        rows.append(
            {
                "case_id": gold["case_id"],
                "expected_status": gold["expected_status"],
                "target_evidence_ids": expected_ids,
                "top5_candidate_ids": ranked,
                "target_in_top5": not missing,
            }
        )
    failed = [item["case_id"] for item in rows if not item["target_in_top5"]]
    if len(dataset["cases"]) < 20 or failed:
        raise ValueError(
            f"fresh holdout preflight failed: "
            f"count={len(dataset['cases'])}, cases={failed}"
        )
    preflight = {
        "schema": "wuli.evidence-holdout-preflight.v1",
        "status": "passed",
        "dataset_fingerprint": dataset["dataset_fingerprint"],
        "overlay_fingerprint": overlay["overlay_fingerprint"],
        "case_count": len(dataset["cases"]),
        "revealed_evidence_overlap": [],
        "required_or_forbidden_target_top5_rate": 1.0,
        "expected_status_counts": {
            "sufficient": sum(
                item["gold_case"]["expected_status"] == "sufficient"
                for item in dataset["cases"]
            ),
            "insufficient": sum(
                item["gold_case"]["expected_status"] == "insufficient"
                for item in dataset["cases"]
            ),
        },
        "cases": rows,
        "live_provider_run": False,
    }
    return overlay, dataset, preflight


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay-output", type=Path, required=True)
    parser.add_argument("--dataset-output", type=Path, required=True)
    parser.add_argument("--preflight-output", type=Path, required=True)
    args = parser.parse_args()
    overlay, dataset, preflight = build()
    for path, payload in (
        (args.overlay_output, overlay),
        (args.dataset_output, dataset),
        (args.preflight_output, preflight),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "status": "created",
                "case_count": len(dataset["cases"]),
                "dataset_fingerprint": dataset["dataset_fingerprint"],
                "overlay_fingerprint": overlay["overlay_fingerprint"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
