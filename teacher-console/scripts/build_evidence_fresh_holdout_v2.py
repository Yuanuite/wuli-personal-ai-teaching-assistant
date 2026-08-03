#!/usr/bin/env python3
"""Build an isolated second fresh holdout and its evaluation-only evidence overlay."""

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


BATCH_ID = "evidence-holdout-2026-07-30-b"
OVERLAY_PATH = (
    "teacher-console/tests/fixtures/evidence-agent/"
    "fresh-holdout-v2-evidence-overlay.json"
)
PRIOR_DATASETS = (
    ROOT / "student-error-library/evals/evidence-gold-calibration.json",
    ROOT / "student-error-library/evals/evidence-gold-holdout.json",
    ROOT / "teacher-console/tests/fixtures/evidence-agent/repair-replay-v1.json",
)


def _evidence_id(source_id: str) -> str:
    digest = hashlib.sha256(
        f"fresh-holdout-v2:{source_id}".encode("utf-8")
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
            "momentum-external-impulse-boundary",
            text=(
                "研究对象组成的系统明确，且研究时间内系统所受合外力冲量可忽略时，"
                "系统总动量守恒；内力是否做功、机械能是否守恒不影响这一判据。"
            ),
            facets=["动量", "系统边界", "外力冲量", "碰撞", "爆炸"],
            applicability=["研究对象组成的系统明确", "研究时间内合外力冲量可忽略"],
            exceptions=[
                "把机械能守恒当作动量守恒前提",
                "只检查瞬时合外力而不检查外力冲量",
                "系统边界中途改变",
            ],
        ),
        evidence_unit(
            "induction-lenz-flux-change",
            text=(
                "闭合导体回路中，感应电流产生的磁场总是阻碍穿过回路的磁通量变化；"
                "应先判原磁通方向及增减，再判感应磁场方向，不能只看原磁场方向。"
            ),
            facets=["电磁感应", "楞次定律", "磁通量变化", "感应电流方向"],
            applicability=["导体回路闭合", "穿过回路的磁通量发生变化"],
            exceptions=[
                "把阻碍磁通量变化说成阻碍原磁场",
                "只看磁场方向而不看面积或夹角变化",
                "回路不闭合却声称有持续感应电流",
            ],
        ),
        evidence_unit(
            "ideal-transformer-ratio-ledger",
            text=(
                "理想变压器满足 U1/U2=N1/N2，且输入功率等于输出功率，"
                "所以 I1/I2=N2/N1；电压比与电流比互为倒数。"
            ),
            facets=["交变电流", "理想变压器", "匝数比", "电压比", "电流比", "功率守恒"],
            applicability=["理想变压器", "忽略漏磁与线圈损耗"],
            exceptions=[
                "把电流比写成匝数比同向",
                "非理想变压器仍直接令输入功率等于输出功率",
                "直流稳态接入变压器仍套匝数比",
            ],
        ),
        evidence_unit(
            "wave-stable-interference-condition",
            text=(
                "形成稳定干涉图样要求两列波频率相同、相位差保持恒定且在观察区域"
                "能够叠加；仅仅相遇或振幅相同都不足以保证稳定干涉。"
            ),
            facets=["机械波", "波的干涉", "频率相同", "相位差恒定", "波的叠加"],
            applicability=["两列波在观察区域叠加", "比较的是稳定干涉图样"],
            exceptions=[
                "频率不同仍声称条纹位置稳定",
                "只因两列波相遇就判定稳定干涉",
                "把振幅相同当作相干的充分条件",
            ],
        ),
        evidence_unit(
            "relative-motion-closest-approach",
            text=(
                "两物体均做匀速直线运动时，可在惯性系中写相对位置和相对速度；"
                "若最近时刻位于给定时间区间内部，则相对位置与相对速度垂直，"
                "否则最近距离出现在区间端点。"
            ),
            facets=["相对运动", "匀速直线运动", "最近距离", "相对位置", "相对速度", "时间区间"],
            applicability=["两物体在同一惯性系中均做匀速直线运动", "研究时间区间明确"],
            exceptions=[
                "存在加速度仍把相对速度当常量",
                "求得的垂直时刻不在区间内却不检查端点",
                "混用不同参考系的位置和速度",
            ],
        ),
        evidence_unit(
            "satellite-circular-orbit-scaling",
            text=(
                "同一中心天体的圆轨道满足 v=√(GM/r)、T=2π√(r³/GM)；"
                "轨道半径越大，线速度越小、周期越大，这些比例只适用于圆轨道比较。"
            ),
            facets=["万有引力", "卫星", "圆轨道", "线速度", "周期", "轨道半径"],
            applicability=["绕同一中心天体", "轨道为圆轨道", "中心天体质量不变"],
            exceptions=[
                "把椭圆轨道瞬时速度直接代入圆轨道比例",
                "比较不同中心天体时忽略中心天体质量",
                "把轨道高度直接当作轨道半径",
            ],
        ),
        evidence_unit(
            "photoelectric-frequency-intensity-split",
            text=(
                "单色光照射金属时，最大初动能满足 Ek,max=hν−W0；频率决定单个"
                "光电子的最大初动能，光强主要影响单位时间逸出的光电子数。低于"
                "截止频率时，提高光强也不能发生光电效应。"
            ),
            facets=["光电效应", "截止频率", "逸出功", "最大初动能", "光强", "频率"],
            applicability=["单色光照射同一种金属", "讨论光电子最大初动能或光电流"],
            exceptions=[
                "只提高光强就声称最大初动能增大",
                "低于截止频率时靠增大光强产生光电子",
                "更换金属后仍沿用原逸出功",
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
    conflicts: list[str],
    sufficient: bool,
    rationale: str,
    diagnostic_targets: list[str] | None = None,
) -> dict[str, Any]:
    retrieval_need = {
        "schema": "wuli.retrieval-need.v1",
        "need_id": "N1",
        "purpose": purpose,
        "question": question,
        "required_facets": facets,
        "forbidden_conflicts": conflicts,
        "minimum_authority": "A",
        "criticality": "required",
        "target_ids": ["Q1"],
        "stage_ids": ["P1"],
        "obligation_ids": ["V1"],
    }
    if diagnostic_targets is not None:
        retrieval_need["diagnostic_targets"] = diagnostic_targets
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
            "retrieval_need": retrieval_need,
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
    momentum = unit_by_section["momentum-external-impulse-boundary"]["evidence_id"]
    lenz = unit_by_section["induction-lenz-flux-change"]["evidence_id"]
    transformer = unit_by_section["ideal-transformer-ratio-ledger"]["evidence_id"]
    interference = unit_by_section["wave-stable-interference-condition"]["evidence_id"]
    relative = unit_by_section["relative-motion-closest-approach"]["evidence_id"]
    satellite = unit_by_section["satellite-circular-orbit-scaling"]["evidence_id"]
    photoelectric = unit_by_section["photoelectric-frequency-intensity-split"]["evidence_id"]
    return [
        proposed_case("fresh2-momentum-collision-valid", momentum, problem="两小车在水平轨道上短时碰撞，取两车为系统，碰撞期间外力冲量相对内力冲量可忽略。", purpose="applicability_check", question="该碰撞阶段采用系统动量守恒需要核对哪些条件？", facets=["系统边界", "外力冲量", "总动量守恒"], conflicts=["系统边界中途改变"], sufficient=True, rationale="系统固定且碰撞期间外力冲量可忽略，满足动量守恒判据。"),
        proposed_case("fresh2-momentum-explosion-valid", momentum, problem="静止物体在光滑水平面上爆炸成两部分，研究时间只取爆炸极短过程且系统包含全部碎片。", purpose="method_candidate", question="爆炸极短过程中为什么可以使用总动量守恒？", facets=["爆炸", "研究时间", "合外力冲量可忽略"], conflicts=["把机械能守恒当作动量守恒前提"], sufficient=True, rationale="全部碎片属于固定系统，短过程外力冲量可忽略，机械能是否守恒不是必要条件。"),
        proposed_case("fresh2-momentum-boundary-conflict", momentum, problem="解答在碰撞前把两车作为系统，碰撞后却只保留其中一车，仍直接声称前后系统总动量守恒。", purpose="exception_check", question="研究过程中改变系统边界后还能否直接比较所谓系统总动量？", facets=["系统边界", "研究时间", "外力冲量"], conflicts=["系统边界中途改变"], sufficient=False, rationale="前后研究对象不同，证据的固定系统前提被破坏。"),
        proposed_case("fresh2-lenz-field-increase-valid", lenz, problem="闭合线圈面积和方向不变，垂直纸面向里的匀强磁场正在增强，需要判断感应电流产生的磁场方向。", purpose="method_candidate", question="应用楞次定律时应怎样从原磁通的方向和增减判断感应磁场？", facets=["原磁通方向", "磁通量增加", "阻碍磁通变化"], conflicts=["把阻碍磁通量变化说成阻碍原磁场"], sufficient=True, rationale="闭合回路中磁通增加，证据给出从磁通变化到感应磁场的完整判断顺序。"),
        proposed_case("fresh2-lenz-area-decrease-valid", lenz, problem="闭合线圈处在方向不变的匀强磁场中，线圈有效面积逐渐减小，需要判断感应电流方向。", purpose="applicability_check", question="磁场本身不变但有效面积减小时，楞次定律是否仍依据磁通量变化？", facets=["闭合导体回路", "面积变化", "感应电流方向"], conflicts=["只看磁场方向而不看面积或夹角变化"], sufficient=True, rationale="有效面积变化导致磁通变化，证据明确要求同时检查面积和夹角。"),
        proposed_case("fresh2-lenz-open-loop-conflict", lenz, problem="一段导线与电阻之间存在断口，磁通量虽在变化，解答却画出了持续的闭合感应电流。", purpose="exception_check", question="回路存在断口时能否依据楞次定律直接声称存在持续感应电流？", facets=["闭合导体回路", "磁通量变化", "感应电流方向"], conflicts=["回路不闭合却声称有持续感应电流"], sufficient=False, rationale="磁通变化可产生感应电动势，但断路不满足持续闭合电流的适用条件。"),
        proposed_case("fresh2-transformer-voltage-valid", transformer, problem="理想变压器原、副线圈匝数分别为 N1、N2，原线圈接正弦交流电压 U1，需要求副线圈电压。", purpose="method_candidate", question="理想变压器的电压比怎样由匝数比确定？", facets=["理想变压器", "匝数比", "电压比"], conflicts=["直流稳态接入变压器仍套匝数比"], sufficient=True, rationale="明确为理想变压器和交流输入，可直接使用电压匝数比。"),
        proposed_case("fresh2-transformer-current-valid", transformer, problem="理想变压器稳定工作，已知原副线圈匝数比和副线圈负载电流，需要求原线圈电流。", purpose="verification_support", question="怎样利用功率守恒确认电流比与匝数比方向相反？", facets=["输入输出功率相等", "电流比", "匝数比互为倒数"], conflicts=["把电流比写成匝数比同向"], sufficient=True, rationale="理想条件下功率守恒，证据明确给出电流比与匝数比的倒数关系。"),
        proposed_case("fresh2-transformer-loss-conflict", transformer, problem="变压器效率明确只有 80%，解答仍令输入功率等于输出功率并直接套用理想电流比。", purpose="exception_check", question="存在明确能量损耗时还能否直接使用理想变压器的功率与电流比？", facets=["理想变压器", "功率守恒", "电流比"], conflicts=["非理想变压器仍直接令输入功率等于输出功率"], sufficient=False, rationale="效率小于 100% 直接破坏理想功率守恒前提。"),
        proposed_case("fresh2-interference-coherent-valid", interference, problem="两列频率相同、相位差恒定的简谐波在同一区域持续叠加，需要判断是否形成稳定干涉图样。", purpose="applicability_check", question="稳定干涉需要满足哪些相干条件？", facets=["频率相同", "相位差恒定", "观察区域叠加"], conflicts=["只因两列波相遇就判定稳定干涉"], sufficient=True, rationale="频率、相位差和空间叠加三个条件均明确满足。"),
        proposed_case("fresh2-interference-amplitude-valid", interference, problem="两列相干波频率相同且相位差恒定，但振幅不同，在观察区域叠加。", purpose="boundary_check", question="振幅不同是否会破坏稳定干涉的相干条件？", facets=["稳定干涉图样", "频率相同", "相位差恒定"], conflicts=["把振幅相同当作相干的充分条件"], sufficient=True, rationale="振幅相同不是稳定干涉的必要条件，关键是频率相同和相位差恒定。"),
        proposed_case("fresh2-interference-frequency-conflict", interference, problem="两列波频率略有不同，解答仅因它们在空间相遇就声称各加强点位置长期固定。", purpose="false_friend_check", question="频率不同的两列波能否形成条纹位置稳定的干涉图样？", facets=["频率相同", "相位差恒定", "稳定干涉图样"], conflicts=[], sufficient=True, rationale="证据明确给出稳定干涉要求频率相同、相位差恒定，因此可以直接纠正“频率不同仍有稳定图样”的错误结论。", diagnostic_targets=["频率不同仍声称条纹位置稳定"]),
        proposed_case("fresh2-relative-interior-valid", relative, problem="甲乙在同一惯性系中均做匀速直线运动，计算得到某时刻二者相对位置与相对速度垂直，且该时刻位于题设时间区间内。", purpose="method_candidate", question="匀速相对运动中如何判定区间内部的最近时刻？", facets=["相对位置", "相对速度", "垂直", "时间区间"], conflicts=["存在加速度仍把相对速度当常量"], sufficient=True, rationale="匀速和区间内部条件均满足，可用垂直条件判最近时刻。"),
        proposed_case("fresh2-relative-endpoint-valid", relative, problem="两物体均做匀速直线运动，但由相对位置与相对速度垂直求出的时刻早于题设观察区间。", purpose="boundary_check", question="形式上求得的最近时刻不在观察区间时应怎样处理？", facets=["研究时间区间", "垂直时刻", "区间端点"], conflicts=["求得的垂直时刻不在区间内却不检查端点"], sufficient=True, rationale="证据明确要求内部解越界时比较区间端点。"),
        proposed_case("fresh2-relative-acceleration-conflict", relative, problem="乙物体正在做匀加速运动，解答仍把初始相对速度当作全程常量并套用相对位置垂直条件。", purpose="exception_check", question="存在加速度时能否直接使用恒定相对速度的最近距离结论？", facets=["匀速直线运动", "相对速度", "最近距离"], conflicts=["存在加速度仍把相对速度当常量"], sufficient=False, rationale="相对速度随时间变化，固定速度条件不成立。"),
        proposed_case("fresh2-satellite-speed-valid", satellite, problem="两颗卫星绕同一行星做半径不同的圆周运动，需要比较轨道线速度。", purpose="method_candidate", question="同一中心天体圆轨道的线速度怎样随轨道半径变化？", facets=["同一中心天体", "圆轨道", "线速度", "轨道半径"], conflicts=["比较不同中心天体时忽略中心天体质量"], sufficient=True, rationale="同一中心天体且均为圆轨道，可使用 v 与 r 的比例关系。"),
        proposed_case("fresh2-satellite-period-valid", satellite, problem="同步比较绕同一中心天体的两个圆轨道，外轨道半径更大，需要判断周期大小。", purpose="verification_support", question="圆轨道周期随轨道半径怎样变化？", facets=["周期", "轨道半径", "同一中心天体"], conflicts=["把轨道高度直接当作轨道半径"], sufficient=True, rationale="满足同一中心天体和圆轨道条件，半径更大周期更大。"),
        proposed_case("fresh2-satellite-ellipse-conflict", satellite, problem="卫星沿明显的椭圆轨道运动，解答把当前到中心天体的距离直接代入圆轨道 v=√(GM/r) 求瞬时速度。", purpose="exception_check", question="椭圆轨道某点的瞬时速度能否直接使用圆轨道速度公式？", facets=["圆轨道", "瞬时速度", "轨道半径"], conflicts=["把椭圆轨道瞬时速度直接代入圆轨道比例"], sufficient=False, rationale="当前轨道不是圆轨道，圆轨道向心条件不能直接迁移。"),
        proposed_case("fresh2-photo-frequency-valid", photoelectric, problem="同一种金属受高于截止频率的单色光照射，现提高入射光频率并保持其他条件不变。", purpose="method_candidate", question="提高频率会怎样影响光电子最大初动能？", facets=["最大初动能", "频率", "逸出功"], conflicts=["只提高光强就声称最大初动能增大"], sufficient=True, rationale="同一种金属的逸出功固定，爱因斯坦方程直接给出频率对最大初动能的影响。"),
        proposed_case("fresh2-photo-intensity-valid", photoelectric, problem="高于截止频率的单色光照射同一种金属，只提高光强，需要区分光电子最大初动能和单位时间逸出数。", purpose="verification_support", question="提高光强主要改变光电效应中的哪个量？", facets=["光强", "光电子数", "最大初动能"], conflicts=["只提高光强就声称最大初动能增大"], sufficient=True, rationale="光频率不变时单个光子能量不变，光强主要改变光电子数。"),
        proposed_case("fresh2-photo-below-threshold-conflict", photoelectric, problem="入射单色光频率低于该金属截止频率，解答声称只要把光强调得足够大就能逸出光电子。", purpose="false_friend_check", question="低于截止频率时增大光强能否触发光电效应？", facets=["截止频率", "光强", "光电子"], conflicts=[], sufficient=True, rationale="证据明确说明低于截止频率时提高光强也不能发生光电效应，因此能够直接纠正当前错误结论。", diagnostic_targets=["低于截止频率时靠增大光强产生光电子"]),
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
    overlap = prior_evidence_ids() & {
        item["evidence_id"] for item in overlay_units
    }
    if overlap:
        raise ValueError(f"fresh overlay reuses revealed Evidence IDs: {sorted(overlap)}")
    overlay = {
        "schema": "wuli.evidence-overlay.v1",
        "overlay_id": "wuli-evidence-fresh-holdout-v2",
        "review_status": "draft",
        "source_scope": ["curated_technique"],
        "units": overlay_units,
    }
    overlay["overlay_fingerprint"] = evidence_contract.stable_fingerprint(
        "evidence-overlay-v1", overlay_units
    )
    dataset = evidence_evaluation.normalize_gold_dataset(
        {
            "schema": "wuli.evidence-gold-dataset.v1",
            "dataset_id": "wuli-evidence-fresh-holdout-v2",
            "dataset_version": "2026-07-30-v2",
            "review_status": "draft",
            "label_origin": "agent_proposed_fresh_holdout_v2",
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
            exclude_entry_id="fresh-holdout-v2-preflight",
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
            f"fresh holdout preflight failed: count={len(dataset['cases'])}, cases={failed}"
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
