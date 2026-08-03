#!/usr/bin/env python3
"""Build the frozen, reviewable MVP-E Evidence Unit holdout candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import evidence_contract  # noqa: E402
import evidence_evaluation  # noqa: E402


BATCH_ID = "evidence-holdout-2026-07-30-a"
CALIBRATION = (
    ROOT
    / "teacher-console"
    / "tests"
    / "fixtures"
    / "evidence-agent"
    / "calibration-curated-v1.json"
)

LORENTZ = "EU-0da542351f084b059f89b89a"
RADIAL = "EU-eef1ded86a99a91d252132b6"
MULTI_REGION = "EU-a4acafd42b4332c3ade8b8f8"
CHORD = "EU-f77a25f68669afd927711788"
SANITY_CHECK = "EU-d1dc406ced943e167e2428f0"
CIRCUIT = "EU-4a36df71d3e05cc730ea9aae"


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
    stages: list[str] | None = None,
) -> dict[str, Any]:
    expected = "sufficient" if sufficient else "insufficient"
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
            "retrieval_need": {
                "schema": "wuli.retrieval-need.v1",
                "need_id": "N1",
                "purpose": purpose,
                "question": question,
                "required_facets": facets,
                "forbidden_conflicts": conflicts,
                "minimum_authority": "A",
                "criticality": "required",
                "target_ids": ["Q1"],
                "stage_ids": stages or ["P1"],
                "obligation_ids": ["V1"],
            },
            "required_evidence_ids": [evidence_id] if sufficient else [],
            "acceptable_evidence_ids": [evidence_id] if sufficient else [],
            "forbidden_evidence_ids": [] if sufficient else [evidence_id],
            "expected_status": expected,
            "teacher_rationale": rationale,
            "evaluation_split": "holdout",
            "batch_id": BATCH_ID,
        },
    }


def cases() -> list[dict[str, Any]]:
    return [
        proposed_case(
            "holdout-lorentz-positive-charge",
            LORENTZ,
            problem="正电荷以不平行于磁场的速度进入匀强磁场，需要确定后续运动；候选证据只说明受力方向判断，没有给出速度分解或完整运动方法。",
            purpose="method_candidate",
            question="非垂直入射匀强磁场的完整方法是什么，为什么只给洛伦兹力方向判断不能满足？",
            facets=["洛伦兹力方向判断只是局部步骤", "分解平行与垂直磁场的速度", "仅垂直分量参与圆周运动", "平行分量保持不变", "合成为螺旋运动"],
            conflicts=["只给受力方向口诀却替代完整运动方法"],
            sufficient=False,
            rationale="该证据局部正确但不覆盖本 Need 的完整方法 facets，可能干扰解题主线，因此不进入 Evidence Set。",
        ),
        proposed_case(
            "holdout-lorentz-negative-charge",
            LORENTZ,
            problem="负电荷斜着进入匀强磁场，需要先按正电荷判断再处理电性，并由弯曲方向复核。",
            purpose="verification_support",
            question="负电荷的磁场力方向如何在正电荷规则基础上修正？",
            facets=["先按正电荷判断", "负电荷方向反向", "轨迹弯向复核"],
            conflicts=["忽略电荷正负"],
            sufficient=True,
            rationale="证据明确要求负电荷反向，并提供独立的圆心侧复核。",
        ),
        proposed_case(
            "holdout-lorentz-parallel-conflict",
            LORENTZ,
            problem="带电粒子的速度与匀强磁场完全平行，却准备使用左手定则给出非零洛伦兹力方向。",
            purpose="exception_check",
            question="速度与磁场平行时能否使用该方向判断得到非零磁场力？",
            facets=["速度与磁场夹角", "洛伦兹力是否存在"],
            conflicts=["速度与磁场平行"],
            sufficient=False,
            rationale="证据的适用前提要求速度不与磁场平行，当前情境直接冲突。",
        ),
        proposed_case(
            "holdout-lorentz-right-hand-conflict",
            LORENTZ,
            problem="待核对解答错误地用右手定则判断运动负电荷的磁场力方向；当前检索目的是找到正确规则来纠正该错误。",
            purpose="false_friend_check",
            question="什么证据能够指出右手定则误用，并给出负电荷受力方向的正确判断？",
            facets=["指出右手定则误用", "先按正电荷使用左手定则", "负电荷方向反向"],
            conflicts=["速度与磁场平行而仍声称存在非零洛伦兹力"],
            sufficient=True,
            rationale="题面中的错误是待诊断对象，不是证据冲突；该证据能明确纠错并给出正确规则，应以 condition_warning 用途采纳。",
        ),
        proposed_case(
            "holdout-radial-boundary-valid",
            RADIAL,
            problem="粒子沿圆形匀强磁场区域的半径方向入射，区域内只有磁场且速率不变，需要联系入射、出射半径与速度偏转角。",
            purpose="method_candidate",
            question="沿圆形边界半径入射时，速度偏转角如何由几何关系确定？",
            facets=["圆形磁场边界", "沿半径入射", "速度偏转角等于半径圆心角"],
            conflicts=["斜入射直接套角相等"],
            sufficient=True,
            rationale="题目满足圆边界、径向入射和纯匀强磁场的全部适用条件。",
        ),
        proposed_case(
            "holdout-radial-boundary-slanted-conflict",
            RADIAL,
            problem="粒子斜着穿过圆形磁场边界，入射速度并不沿边界半径，却准备直接令速度偏转角等于两条边界半径的夹角。",
            purpose="exception_check",
            question="非径向入射时还能否直接使用偏转角等于半径圆心角？",
            facets=["入射方向条件", "圆形边界几何关系"],
            conflicts=["斜入射仍直接令两角相等"],
            sufficient=False,
            rationale="缺少径向入射前提，证据明确把该套用方式列为例外。",
        ),
        proposed_case(
            "holdout-radial-boundary-electric-conflict",
            RADIAL,
            problem="圆形区域内电场和磁场同时存在，粒子速率发生变化，仍准备使用纯磁场圆弧的径向边界角关系。",
            purpose="exception_check",
            question="电场同时做功并改变速率时，该纯磁场几何结论是否仍可直接采用？",
            facets=["区域内受力类型", "速率是否不变", "轨迹是否为定半径圆弧"],
            conflicts=["电场与磁场同时做功时直接套用"],
            sufficient=False,
            rationale="当前情境破坏纯匀强磁场和速率不变条件，证据不可采纳。",
        ),
        proposed_case(
            "holdout-angle-ledger-valid",
            MULTI_REGION,
            problem="粒子依次经过三个场区，每段转向和偏转角均已知，并统一规定逆时针为正，需要求最终速度方向。",
            purpose="method_candidate",
            question="多段轨迹怎样用统一符号的方向角增量快速累计？",
            facets=["逐段方向角增量", "统一正方向", "按时间顺序累加"],
            conflicts=["中途更换角度正方向", "漏记返回段"],
            sufficient=True,
            rationale="每段转向可确定且符号约定统一，适合使用方向角账本。",
            stages=["P1", "P2", "P3"],
        ),
        proposed_case(
            "holdout-angle-ledger-sign-conflict",
            MULTI_REGION,
            problem="待核对解答在多场区轨迹中途改变角度正方向并直接相加；当前检索目的是找到正确的累计偏转角规则来纠正它。",
            purpose="false_friend_check",
            question="什么证据能够解释累计偏转角为何必须统一正方向，并纠正当前做法？",
            facets=["统一角度正方向", "带符号角增量", "按时间顺序累计"],
            conflicts=["每段转向或偏转角本身不可确定"],
            sufficient=True,
            rationale="错误做法是待纠正对象；证据完整给出正确累计规则，因此可以作为 condition_warning 采纳。",
            stages=["P1", "P2"],
        ),
        proposed_case(
            "holdout-angle-ledger-position-conflict",
            MULTI_REGION,
            problem="解答把轨迹点相对原点的位置极角和速度方向角混在同一张角度账本中直接相加。",
            purpose="false_friend_check",
            question="位置角与速度方向角能否作为同一种角增量直接累计？",
            facets=["速度方向角", "位置极角", "角度对象一致性"],
            conflicts=["把位置角和速度方向角混用"],
            sufficient=False,
            rationale="证据只允许累计速度方向增量，当前混用正是明确例外。",
        ),
        proposed_case(
            "holdout-chord-minor-arc-valid",
            CHORD,
            problem="半径为 r 的圆上两点对应较小圆心角 θ，需要把两点直线距离写成 r 与 θ 的关系。",
            purpose="method_candidate",
            question="圆上两点的弦长如何由半径和较小圆心角表示？",
            facets=["弦长", "圆半径", "较小圆心角", "L=2r sin(θ/2)"],
            conflicts=["混淆速度偏转角与位置圆心角"],
            sufficient=True,
            rationale="θ 明确是同一圆的较小圆心角，证据公式可直接使用。",
        ),
        proposed_case(
            "holdout-chord-specified-arc-valid",
            CHORD,
            problem="圆轨道上明确指定从 A 到 B 所取圆弧及其圆心角 θ，需要求端点间弦长。",
            purpose="method_candidate",
            question="已明确所取圆弧时，端点弦长怎样计算？",
            facets=["指定圆弧", "对应圆心角", "弦长公式"],
            conflicts=["使用了不对应端点的角"],
            sufficient=True,
            rationale="题目已经明确所取弧与对应圆心角，满足证据的边界说明。",
        ),
        proposed_case(
            "holdout-chord-velocity-angle-conflict",
            CHORD,
            problem="圆周运动中只给出了速度方向的转角，却未证明它等于两位置半径的圆心角，解答便直接代入弦长公式。",
            purpose="exception_check",
            question="未建立角度对应关系时能否把速度偏转角直接代入弦长公式？",
            facets=["位置圆心角", "速度偏转角", "角度对应关系"],
            conflicts=["把速度偏转角与位置圆心角混淆后直接代入"],
            sufficient=False,
            rationale="当前解答没有建立两种角的对应，正中证据所列例外。",
        ),
        proposed_case(
            "holdout-sanity-dimension-valid",
            SANITY_CHECK,
            problem="推导得到一个含多个物理量的时间表达式，需要用与原推导不同的低成本方法检查结果。",
            purpose="verification_support",
            question="怎样用量纲、极端参数或对称情形对表达式做独立检查？",
            facets=["单位正确", "极端参数趋势", "对称情形恢复熟悉结论"],
            conflicts=["只复算同一条推导"],
            sufficient=True,
            rationale="结果含物理量和参数，证据提供三种低成本独立检查入口。",
        ),
        proposed_case(
            "holdout-sanity-symmetry-valid",
            SANITY_CHECK,
            problem="某结果依赖两个结构对称的参数，需要检查令二者相等时是否恢复熟悉的对称结论。",
            purpose="verification_support",
            question="参数进入对称情形时应怎样验证结果合理性？",
            facets=["对称参数", "恢复熟悉结论", "独立于原推导"],
            conflicts=["只复算原方程"],
            sufficient=True,
            rationale="对称情形是证据明确推荐的独立低成本检验。",
        ),
        proposed_case(
            "holdout-sanity-same-derivation-conflict",
            SANITY_CHECK,
            problem="解答把原推导从头到尾按完全相同的代数步骤再算一遍，并声称这已经构成独立物理验证。",
            purpose="false_friend_check",
            question="重复同一条推导是否足以证明结果通过独立检查？",
            facets=["独立验证", "量纲或极限检查", "验证路径差异"],
            conflicts=["只复算同一条推导当作独立验证"],
            sufficient=False,
            rationale="相同路径复算不能发现共享错误，证据明确排除这种做法。",
        ),
        proposed_case(
            "holdout-circuit-slider-boundary-valid",
            CIRCUIT,
            problem="稳恒直流电路中含滑动变阻器，需要先标实际电流通路，再检查滑片位于两个端点时的短路或最大接入电阻。",
            purpose="boundary_check",
            question="滑动变阻器等效电路怎样用端点状态做边界检查？",
            facets=["实际电流通路", "滑片端点", "短路或最大接入电阻"],
            conflicts=["只按图形远近判断串并联"],
            sufficient=True,
            rationale="电路模型明确且为稳恒直流，证据完整覆盖通路和端点检查。",
        ),
        proposed_case(
            "holdout-circuit-path-valid",
            CIRCUIT,
            problem="电路图画法较复杂，但元件连接点明确，需要忽略几何形状，沿实际电流通路判断等效连接。",
            purpose="method_candidate",
            question="复杂画法下应依据什么判断元件的串并联关系？",
            facets=["实际电流通路", "连接节点", "等效电路"],
            conflicts=["只按图形远近判断串并联"],
            sufficient=True,
            rationale="证据要求从真实通路出发，正适合排除图形布局造成的误导。",
        ),
        proposed_case(
            "holdout-circuit-drawing-conflict",
            CIRCUIT,
            problem="解答仅凭两个电阻在图上画得很近就判定并联，没有检查它们是否连接在相同两个节点。",
            purpose="false_friend_check",
            question="仅凭图形远近能否采纳该等效电路判断？",
            facets=["实际连接节点", "电流通路", "串并联判据"],
            conflicts=["只按图形远近判断串并联"],
            sufficient=False,
            rationale="图形距离不是电路拓扑证据，当前做法正是明确例外。",
        ),
        proposed_case(
            "holdout-circuit-meter-resistance-conflict",
            CIRCUIT,
            problem="题目明确给出电流表具有不可忽略的内阻，解答却仍按理想电流表短路处理并套用滑片端点结论。",
            purpose="exception_check",
            question="电表内阻模型与理想设定不同时，能否直接采用原等效边界结论？",
            facets=["电表内阻设定", "元件模型", "端点等效"],
            conflicts=["忽略电表内阻设定"],
            sufficient=False,
            rationale="题设元件模型与理想化前提不一致，证据不能无条件迁移。",
        ),
    ]


def build_dataset() -> dict[str, Any]:
    calibration = evidence_evaluation.normalize_gold_dataset(
        json.loads(CALIBRATION.read_text(encoding="utf-8"))
    )
    calibration_ids = {
        evidence_id
        for item in calibration["cases"]
        for field in (
            "required_evidence_ids",
            "acceptable_evidence_ids",
            "forbidden_evidence_ids",
        )
        for evidence_id in item["gold_case"][field]
    }
    result = evidence_evaluation.normalize_gold_dataset(
        {
            "schema": "wuli.evidence-gold-dataset.v1",
            "dataset_id": "wuli-evidence-fresh-holdout",
            "dataset_version": "2026-07-30-v3",
            "review_status": "draft",
            "label_origin": "agent_proposed_holdout",
            "source_scope": ["curated_technique"],
            "reviewer": "",
            "reviewed_at": "",
            "cases": cases(),
        }
    )
    holdout_ids = {
        evidence_id
        for item in result["cases"]
        for field in (
            "required_evidence_ids",
            "acceptable_evidence_ids",
            "forbidden_evidence_ids",
        )
        for evidence_id in item["gold_case"][field]
    }
    overlap = calibration_ids & holdout_ids
    if overlap:
        raise ValueError(f"holdout reuses calibration Evidence Units: {sorted(overlap)}")
    if len(result["cases"]) < 20:
        raise ValueError("fresh holdout requires at least 20 cases")
    if any(
        item["gold_case"]["evaluation_split"] != "holdout"
        or item["gold_case"]["batch_id"] != BATCH_ID
        for item in result["cases"]
    ):
        raise ValueError("every holdout case must belong to the frozen batch")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = build_dataset()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "created",
                "path": str(args.output),
                "case_count": len(dataset["cases"]),
                "dataset_fingerprint": dataset["dataset_fingerprint"],
                "review_status": dataset["review_status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
