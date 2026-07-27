#!/usr/bin/env python3
"""Criterion-referenced objective difficulty rubric for reviewed physics problems."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


DIMENSIONS = (
    ("knowledge_depth", "知识深度", 20),
    ("knowledge_integration", "知识整合", 15),
    ("type_distance_modelling", "题型距离与建模转换", 20),
    ("process_state", "过程与状态复杂度", 20),
    ("calculation_expression", "运算与表达负荷", 10),
    ("condition_completeness", "条件与完备性", 15),
)
LEVELS = ((20, "基础"), (40, "较易"), (60, "中等"), (80, "较难"), (100, "挑战"))
SCHEMA_VERSION = 8
RUBRIC_VERSION = "objective-path-rubric.v8"
ANCHOR_FILE = Path(__file__).with_name("difficulty_anchors.json")
ANCHORS = json.loads(ANCHOR_FILE.read_text(encoding="utf-8"))
ANCHOR_VERSION = str(ANCHORS["anchor_version"])
KNOWLEDGE_UNIT_FILE = Path(__file__).with_name("difficulty_knowledge_units.json")
KNOWLEDGE_UNIT_REGISTRY = json.loads(
    KNOWLEDGE_UNIT_FILE.read_text(encoding="utf-8")
)
KNOWLEDGE_UNIT_VERSION = str(KNOWLEDGE_UNIT_REGISTRY["registry_version"])
KNOWLEDGE_UNITS = {
    str(item["id"]): {
        "label": str(item["label"]),
        "aliases": tuple(str(alias) for alias in item.get("aliases", [])),
    }
    for item in KNOWLEDGE_UNIT_REGISTRY["units"]
}
MAX_DIMENSION_SCORE = Decimal("5.0")
DIMENSION_MAX_SCORES: dict[str, Decimal] = {}
DIMENSION_STEP = Decimal("0.1")

COGNITIVE_OPERATIONS = {
    "identify": ("识别概念", 1.0),
    "apply": ("条件内应用", 2.0),
    "explain": ("解释或推导关系", 2.2),
    "audit": ("审查条件与因果", 3.0),
    "reconstruct": ("从基础原理重建", 3.8),
    "algebra_only": ("纯代数整理", 0.0),
}
COGNITIVE_PATTERNS = (
    ("reconstruct", re.compile(r"第一性|从.+(?:定义|定律|原理).*(?:推导|重建|证明)|微观.*宏观|反推出")),
    ("audit", re.compile(r"条件|边界|临界|因果|分类|首次|唯一|全部|成立|守恒对象")),
    ("explain", re.compile(r"为什么|为何|解释|说明|推导|证明|与.+无关|消去|消元")),
    ("apply", re.compile(r"应用|代入|列式|求得|计算|建立.+关系|模型")),
    ("identify", re.compile(r"识别|定义|判断概念")),
)

REPRESENTATION_PATTERN = re.compile(
    r"建模|表征|等效|受力图|电路|图像|图线|坐标|几何|轨迹|函数|状态图|示意图"
)
CONDITION_PATTERN = re.compile(
    r"条件|分类|临界|首次|第一次|唯一|全部|所有可能|至少|至多|边界|范围|恰好|完备|排除"
)
DERIVATION_FAMILIES = (
    ("守恒或闭合", re.compile(r"守恒|闭合|功能关系")),
    ("反向重建", re.compile(r"反推|反求|逆推")),
    ("递推或证明", re.compile(r"递推|归纳|证明")),
    ("联立消元", re.compile(r"联立|消去|消元")),
    ("全局优化", re.compile(r"全局|极值|最大|最小")),
)
KNOWLEDGE_FAMILIES = (
    ("运动学", re.compile(r"位移|速度|加速度|平抛|斜抛|运动学")),
    ("动力学", re.compile(r"牛顿|受力|合力|摩擦|弹力|动力学")),
    ("能量与功", re.compile(r"动能|势能|机械能|做功|功率|能量")),
    ("动量", re.compile(r"动量|冲量|碰撞")),
    ("静电场", re.compile(r"电场|电势|电势能|库仑")),
    ("磁场与带电粒子", re.compile(r"磁场|洛伦兹|回旋|磁偏转")),
    ("恒定电流", re.compile(r"电路|电流|电压|电阻|电源|电容")),
    ("电磁感应", re.compile(r"磁通|感应电动势|电磁感应|楞次|法拉第")),
    ("圆周与几何约束", re.compile(r"圆周|半径|圆心|弦|相切|几何|转角")),
    ("周期与相位", re.compile(r"周期|相位|频率|同步|首次返回")),
    ("振动与波", re.compile(r"振动|波长|波速|驻波|多普勒")),
)
REPRESENTATION_FAMILIES = (
    ("对象到受力模型", re.compile(r"受力图|受力模型|受力分析")),
    ("过程到状态模型", re.compile(r"分段|状态|阶段|事件|时序")),
    ("关系到图像", re.compile(r"图像|图线|函数|斜率|面积")),
    ("轨迹到几何模型", re.compile(r"轨迹|圆心|半径|弦|相切|几何")),
    ("装置到等效电路", re.compile(r"等效电路|电源模型|内阻|负载")),
    ("空间到坐标模型", re.compile(r"坐标|分量|投影|空间|三维")),
)
CONDITION_FAMILIES = (
    ("分类讨论", re.compile(r"分类|分支|不同情况|枚举")),
    ("临界或极值", re.compile(r"临界|最大|最小|极值|恰好")),
    ("首次性", re.compile(r"首次|第一次|最早")),
    ("唯一性", re.compile(r"唯一|仅有")),
    ("范围与边界", re.compile(r"边界|范围|区间|至少|至多")),
    ("完备性", re.compile(r"全部|所有可能|穷尽|完备|遗漏")),
    ("方向与符号", re.compile(r"方向|正负|符号|反向")),
)
TYPE_DISTANCE_MODES = {
    "direct_archetype": ("教材母题", 0.8),
    "routine_variant": ("常规变式", 1.6),
    "standard_transfer": ("标准迁移", 2.5),
    "model_reconstruction": ("模型重构", 3.4),
    "non_obvious_bridge": ("隐蔽桥梁", 4.2),
    "novel_construction": ("非常规构造", 5.0),
}
LEGACY_INFERRED_TYPE_DOWNGRADE = {
    "direct_archetype": "direct_archetype",
    "routine_variant": "direct_archetype",
    "standard_transfer": "routine_variant",
    "model_reconstruction": "standard_transfer",
    "non_obvious_bridge": "model_reconstruction",
    "novel_construction": "non_obvious_bridge",
}
TYPE_DISTANCE_SIGNAL_FAMILIES = (
    ("反向重建", re.compile(r"反推|反求|逆推")),
    ("隐蔽几何", re.compile(r"共圆心|圆心重合|相切|圆心角账本|轨迹.*几何")),
    ("周期或相位耦合", re.compile(r"递推|相位|时窗|同步|周期匹配|累计转角")),
    ("分支搜索", re.compile(r"枚举|分支|筛选候选|全局|所有可能|完整性|完备性")),
    ("隐藏不变量", re.compile(r"不变量|统一形式|与.+无关|等效")),
    ("主动构造", re.compile(r"构造|参数化|辅助|镜像|反演")),
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_path(path: Any) -> dict[str, Any] | None:
    if not isinstance(path, dict) or not path:
        return None
    return json.loads(json.dumps(path, ensure_ascii=False, sort_keys=True))


def input_digest(problem: str, standard_path: Any) -> str:
    path_text = json.dumps(
        _canonical_path(standard_path) or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((problem + "\0" + path_text).encode("utf-8")).hexdigest()


def level_for(score: int) -> str:
    for maximum, label in LEVELS:
        if score <= maximum:
            return label
    return "挑战"


def _dimension_max(dimension_id: str) -> Decimal:
    return DIMENSION_MAX_SCORES.get(dimension_id, MAX_DIMENSION_SCORE)


def _step(value: float | Decimal, maximum: Decimal = MAX_DIMENSION_SCORE) -> float:
    decimal = min(maximum, max(Decimal("0"), Decimal(str(value))))
    return float(decimal.quantize(DIMENSION_STEP, rounding=ROUND_HALF_UP))


def _score(dimensions: list[dict[str, Any]]) -> int:
    value = sum(
        Decimal(str(item["score"]))
        / MAX_DIMENSION_SCORE
        * Decimal(str(item["weight"]))
        for item in dimensions
    )
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _strings(value: Any, *, limit: int = 16) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if text and text not in result:
            result.append(text[:300])
    return result[:limit]


def _count_anchor(count: int, anchors: tuple[float, ...]) -> float:
    return anchors[min(max(int(count), 0), len(anchors) - 1)]


def _matched_categories(
    text: str,
    families: tuple[tuple[str, re.Pattern[str]], ...],
) -> list[str]:
    return [label for label, pattern in families if pattern.search(text)]


def _knowledge_categories(basis: list[str]) -> list[str]:
    categories: list[str] = []
    for item in basis:
        matches = _matched_categories(item, KNOWLEDGE_FAMILIES)
        labels = matches or [item]
        for label in labels:
            if label not in categories:
                categories.append(label)
    return categories


def _normalize_knowledge_units(
    value: Any,
    decisive_relations: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        unit_id = str(raw.get("id", "")).strip()
        if unit_id not in KNOWLEDGE_UNITS:
            continue
        indexes = raw.get("relation_indexes")
        if not isinstance(indexes, list):
            continue
        valid_indexes = sorted({
            index
            for index in indexes
            if isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < len(decisive_relations)
        })
        if not valid_indexes:
            continue
        existing = next((item for item in result if item["id"] == unit_id), None)
        if existing:
            existing["relation_indexes"] = sorted(
                set(existing["relation_indexes"] + valid_indexes)
            )
        else:
            result.append({
                "id": unit_id,
                "relation_indexes": valid_indexes,
            })
    return result


def _normalize_type_distance(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    mode = str(value.get("mode", "")).strip()
    if mode not in TYPE_DISTANCE_MODES:
        return None
    source = str(value.get("source", "standard-path"))[:80]
    if source == "fixed-signal-inference.v1":
        mode = LEGACY_INFERRED_TYPE_DOWNGRADE[mode]
        source = "fixed-signal-inference.v2-conservative"
    label, _ = TYPE_DISTANCE_MODES[mode]
    return {
        "mode": mode,
        "label": label,
        "archetype": str(value.get("archetype", "")).strip()[:160],
        "recognition_barrier": str(value.get("recognition_barrier", "")).strip()[:240],
        "novel_bridge": str(value.get("novel_bridge", "")).strip()[:240],
        "source": source,
    }


def _infer_type_distance(text: str, transforms: list[str]) -> dict[str, Any]:
    signals = _matched_categories(text, TYPE_DISTANCE_SIGNAL_FAMILIES)
    transform_categories = _matched_categories(
        "\n".join(transforms) or text,
        REPRESENTATION_FAMILIES,
    )
    # One familiar representation change is routine; needing two or more
    # distinct representations is a genuine transfer burden. Count categories,
    # never prose items, and cap their contribution so verbose W3 paths cannot
    # manufacture distance.
    transform_units = min(2, len(transform_categories))
    units = min(5, len(signals) + transform_units)
    mode = tuple(TYPE_DISTANCE_MODES)[units]
    label, _ = TYPE_DISTANCE_MODES[mode]
    evidence = [*signals, *transform_categories]
    return {
        "mode": mode,
        "label": label,
        "archetype": "由标准解题路径固定推断",
        "recognition_barrier": "、".join(evidence[:4]) or "可直接识别教材母题",
        "novel_bridge": "、".join(signals[:3]),
        "source": "fixed-signal-inference.v1",
    }


def _dag_depth(steps: list[dict[str, Any]]) -> int:
    parents = {
        str(item.get("id", "")): {
            str(parent) for parent in item.get("depends_on", []) if str(parent).strip()
        }
        for item in steps
        if str(item.get("id", "")).strip()
    }
    memo: dict[str, int] = {}

    def depth(node: str, active: set[str]) -> int:
        if node in memo:
            return memo[node]
        if node in active:
            return 1
        dependencies = parents.get(node, set())
        value = 1 + max((depth(parent, active | {node}) for parent in dependencies), default=0)
        memo[node] = value
        return value

    return max((depth(node, set()) for node in parents), default=0)


def _cognitive_operation(value: Any, text: str = "") -> tuple[str, str]:
    explicit = str(value or "").strip()
    if explicit in COGNITIVE_OPERATIONS:
        return explicit, "w3-enum"
    for operation, pattern in COGNITIVE_PATTERNS:
        if pattern.search(text):
            return operation, "deterministic-fallback"
    return "apply", "deterministic-fallback"


def _verification_summary(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"status": "unverified", "passed_target_ids": []}
    verifier = report.get("verifier")
    audits = verifier.get("target_audits", []) if isinstance(verifier, dict) else []
    passed = sorted({
        str(item.get("target_id", ""))
        for item in audits
        if isinstance(item, dict)
        and str(item.get("verdict", "")).lower() in {"pass", "passed", "equivalent"}
        and str(item.get("target_id", "")).strip()
    })
    return {
        "status": "verified" if passed else "unverified",
        "passed_target_ids": passed,
    }


def _fallback_reasoning_graph(
    steps: list[str],
    relations: list[str],
) -> list[dict[str, Any]]:
    graph: list[dict[str, Any]] = []
    # Old W2 paths do not bind prose steps to relations. Build the conservative
    # fallback from the stable relation list so splitting one explanation into
    # more Markdown steps cannot manufacture conceptual depth.
    for index, relation in enumerate(relations):
        operation_text = steps[min(index, len(steps) - 1)] if steps else relation
        operation, source = _cognitive_operation(
            None,
            relation,
        )
        graph.append({
            "id": f"fallback-{index + 1}",
            "operation": operation_text,
            "cognitive_operation": operation,
            "cognitive_source": source,
            "depends_on": [f"fallback-{index}"] if index else [],
            "target_ids": ["target-1"],
            "decisive_relations": [relation] if relation else [],
        })
    return graph


def _normalize_reasoning_graph(
    raw_graph: list[dict[str, Any]],
    steps: list[str],
    relations: list[str],
) -> list[dict[str, Any]]:
    if not raw_graph:
        return _fallback_reasoning_graph(steps, relations)
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_graph):
        operation_text = str(raw.get("operation", "")).strip()
        decisive = _strings(raw.get("decisive_relations"), limit=12)
        operation, source = _cognitive_operation(
            raw.get("cognitive_operation"),
            "\n".join([operation_text, *decisive]),
        )
        normalized.append({
            **raw,
            "id": str(raw.get("id") or f"step-{index + 1}"),
            "operation": operation_text,
            "cognitive_operation": operation,
            "cognitive_source": source,
            "depends_on": _strings(raw.get("depends_on"), limit=16),
            "target_ids": _strings(raw.get("target_ids"), limit=16) or ["target-1"],
            "decisive_relations": decisive,
            "knowledge_units": _normalize_knowledge_units(
                raw.get("knowledge_units"),
                decisive,
            ),
        })
    return normalized


def _necessary_graph_node_ids(graph: list[dict[str, Any]]) -> set[str]:
    """Return the union of all target-completing backward slices.

    Knowledge integration is a union, not the longest serial path: indispensable
    parallel branches must both count, while orphaned detail outside every
    target-completing slice must not.
    """
    nodes = {
        str(item.get("id", "")): item
        for item in graph
        if str(item.get("id", "")).strip()
    }
    children: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for node_id, item in nodes.items():
        for parent in item.get("depends_on", []):
            if parent in nodes:
                children[parent].add(node_id)
    targets = {
        target
        for item in nodes.values()
        for target in item.get("target_ids", [])
        if target
    } or {"target-1"}
    terminals: set[str] = set()
    for target in targets:
        target_nodes = {
            node_id
            for node_id, item in nodes.items()
            if target in item.get("target_ids", [])
        }
        terminals.update({
            node_id
            for node_id in target_nodes
            if not any(child in target_nodes for child in children[node_id])
        } or target_nodes)

    necessary: set[str] = set()

    def include(node_id: str) -> None:
        if node_id in necessary or node_id not in nodes:
            return
        necessary.add(node_id)
        for parent in nodes[node_id].get("depends_on", []):
            include(parent)

    for terminal in terminals:
        include(terminal)
    return necessary


def _infer_legacy_knowledge_units(
    basis: list[str],
    relations: list[str],
    *,
    preserve_unknown_basis: bool = True,
) -> list[dict[str, str]]:
    text_items = [*basis, *relations]
    inferred: list[dict[str, str]] = []
    matched_basis: set[str] = set()
    for unit_id, spec in KNOWLEDGE_UNITS.items():
        matching_items = [
            item
            for item in text_items
            if any(alias.lower() in item.lower() for alias in spec["aliases"])
        ]
        if matching_items:
            inferred.append({"id": unit_id, "label": str(spec["label"])})
            matched_basis.update(item for item in matching_items if item in basis)
    # Preserve one conservative unit for an unrecognized legacy basis item.
    # This avoids silently reducing old W2 paths to zero while never splitting
    # one prose item into several inferred laws.
    if preserve_unknown_basis:
        for index, item in enumerate(basis):
            if item not in matched_basis:
                inferred.append({"id": f"legacy-{index}", "label": item})
    return inferred


def _minimum_sufficient_knowledge_units(
    units: list[dict[str, str]],
    relations: list[str],
) -> list[dict[str, str]]:
    """Collapse formulas and domain labels into the minimum sufficient modules."""
    by_id = {str(item["id"]): item for item in units}
    relation_text = "\n".join(relations)

    # Evidence-authoritative W3 paths intentionally ignore broad basis labels.
    # Recover only modules that are actually used by decisive relations.  These
    # patterns identify a mother-model, not every formula written underneath it.
    relation_modules = (
        (
            "magnetic_circular_motion",
            r"洛伦兹|qvB|磁场.{0,18}(?:圆|偏转|回旋|轨迹)|"
            r"(?:回旋|圆轨道|圆周运动).{0,18}(?:磁|带电粒子)|r\s*=\s*mv",
        ),
        (
            "geometry_constraint",
            r"相切|相交|交点|共圆心|弦长|圆心位置|轨迹圆心|"
            r"共同旋转中心|几何边界|临界几何|几何返回条件|"
            r"碰壁|圆柱壁|落点|坐标条件",
        ),
        (
            "kinematics_relations",
            r"匀加速|平抛|斜抛|运动学|v[²^]2\s*[-=]|"
            r"(?:位移|速度).{0,12}(?:时间|加速度)|x\s*=\s*.*t",
        ),
        (
            "electric_field_potential",
            r"电场强度|电势差|电势能|电场力|qE|板间电场|静电场",
        ),
        (
            "circuit_laws",
            r"欧姆定律|基尔霍夫|电阻|电流|电功率|电容器|"
            r"电荷积累|充电|放电",
        ),
        (
            "electromagnetic_induction",
            r"电磁感应|感应电动势|磁通量|法拉第|楞次|BLv",
        ),
        (
            "ampere_force",
            r"安培力|F安|BIL",
        ),
        (
            "newton_second_law",
            r"牛顿第二定律|F\s*=\s*ma|合外力.{0,12}加速度",
        ),
        (
            "work_energy_theorem",
            r"动能定理|合力做功|净功|功—能|功能关系",
        ),
        (
            "mechanical_energy_conservation",
            r"机械能守恒|能量守恒",
        ),
        (
            "linear_momentum_conservation",
            r"线动量守恒|动量守恒|水平动量|竖直动量",
        ),
        (
            "angular_momentum_spin",
            r"角动量|自旋|宇称",
        ),
        (
            "charge_conservation",
            r"电荷守恒|电荷数守恒",
        ),
        (
            "relativistic_energy_momentum",
            r"相对论|质能关系|静止能|能量.{0,4}动量关系|E[²^]2",
        ),
        (
            "atomic_nuclear_mass_conversion",
            r"原子质量|核质量|质量亏损|电子质量换算",
        ),
        (
            "experimental_model_inference",
            r"连续谱|实验检验|实验事实|模型证伪|实验反推",
        ),
    )
    for unit_id, pattern in relation_modules:
        if re.search(pattern, relation_text, re.IGNORECASE):
            by_id[unit_id] = {
                "id": unit_id,
                "label": str(KNOWLEDGE_UNITS[unit_id]["label"]),
            }
    if "ampere_force" in by_id and not re.search(
        r"带电粒子|电荷.{0,12}运动|qvB",
        relation_text,
        re.IGNORECASE,
    ):
        # For a macroscopic current-carrying conductor, BIL is the sufficient
        # force model; its microscopic Lorentz-force interpretation is not a
        # second unavoidable module.
        by_id.pop("lorentz_force", None)
    if "experimental_model_inference" in by_id and not re.search(
        r"连续(?:能量|能)?谱|模型证伪|实验事实.{0,12}(?:反推|排除)|"
        r"实验结果.{0,12}模型",
        relation_text,
    ):
        # Reading an ordinary graph or meter is a representation operation,
        # unlike the independent experiment-to-model inference used by the
        # competition anchor.
        by_id.pop("experimental_model_inference", None)

    if {
        "lorentz_force",
        "circular_motion",
    }.issubset(by_id) or "magnetic_circular_motion" in by_id:
        by_id.pop("lorentz_force", None)
        by_id.pop("circular_motion", None)
        by_id["magnetic_circular_motion"] = {
            "id": "magnetic_circular_motion",
            "label": str(KNOWLEDGE_UNITS["magnetic_circular_motion"]["label"]),
        }

    has_angle_time = any(
        re.search(r"转角|圆心角|Δ?φ|(?i:theta)|θ|方位角", relation)
        and re.search(r"时间|时刻|角速度|t\s*=|ω", relation, re.IGNORECASE)
        for relation in relations
    )
    if has_angle_time:
        by_id["trajectory_angle_time"] = {
            "id": "trajectory_angle_time",
            "label": str(KNOWLEDGE_UNITS["trajectory_angle_time"]["label"]),
        }
    if re.search(
        r"释放时|发射时刻|相位匹配|方波相位|场切换|周期切换|时间窗口|首次相遇|"
        r"相遇时刻|到达时刻序列|模周期|延迟释放",
        relation_text,
    ):
        by_id["external_phase_timing"] = {
            "id": "external_phase_timing",
            "label": str(KNOWLEDGE_UNITS["external_phase_timing"]["label"]),
        }
    # Broad legacy domains are display metadata, not independent laws.
    broad_labels = {
        "运动学",
        "动力学",
        "能量与功",
        "动量",
        "静电场",
        "磁场与带电粒子",
        "恒定电流",
        "电磁感应",
        "圆周与几何约束",
        "周期与相位",
        "振动与波",
    }
    return [
        item
        for item in by_id.values()
        if str(item.get("label", "")) not in broad_labels
    ]


def _knowledge_unit_profile(
    graph: list[dict[str, Any]],
    basis: list[str],
    relations: list[str],
    *,
    evidence_authoritative: bool = False,
) -> dict[str, Any]:
    necessary_ids = _necessary_graph_node_ids(graph)
    explicit_ids: list[str] = []
    bound_relations: dict[str, list[str]] = {}
    for item in graph:
        if (
            str(item.get("id", "")) not in necessary_ids
            or item.get("cognitive_operation") == "algebra_only"
            or not item.get("decisive_relations")
        ):
            continue
        decisive = item["decisive_relations"]
        for unit in item.get("knowledge_units", []):
            unit_id = str(unit["id"])
            if unit_id not in explicit_ids:
                explicit_ids.append(unit_id)
            bucket = bound_relations.setdefault(unit_id, [])
            for index in unit["relation_indexes"]:
                relation = decisive[index]
                if relation not in bucket:
                    bucket.append(relation)
    if explicit_ids and not evidence_authoritative:
        units = [
            {"id": unit_id, "label": str(KNOWLEDGE_UNITS[unit_id]["label"])}
            for unit_id in explicit_ids
        ]
        source = "w3-decisive-relation-bindings"
    else:
        units = _infer_legacy_knowledge_units(
            [] if evidence_authoritative else basis,
            relations,
            preserve_unknown_basis=not evidence_authoritative,
        )
        source = (
            "post-solve-deterministic-projection"
            if evidence_authoritative
            else "legacy-deterministic-inference"
        )
    units = _minimum_sufficient_knowledge_units(units, relations)
    return {
        "registry_version": KNOWLEDGE_UNIT_VERSION,
        "source": source,
        "necessary_step_ids": sorted(necessary_ids),
        "units": units,
        "count": len(units),
        "bound_relations": bound_relations,
    }


def _anchor_score(path_cost: float) -> float:
    points = [
        (float(item["path_cost"]), float(item["score"]))
        for item in ANCHORS["calibration_curve"]
    ]
    value = max(0.0, float(path_cost))
    if value <= points[0][0]:
        return points[0][1] * value / points[0][0]
    for (left_cost, left_score), (right_cost, right_score) in zip(points, points[1:]):
        if value <= right_cost:
            ratio = (value - left_cost) / (right_cost - left_cost)
            return left_score + ratio * (right_score - left_score)
    return points[-1][1]


def _conceptual_path_profile(
    graph: list[dict[str, Any]],
    verification: dict[str, Any],
) -> dict[str, Any]:
    nodes = {str(item["id"]): item for item in graph if str(item.get("id", "")).strip()}
    children: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for node_id, item in nodes.items():
        for parent in item.get("depends_on", []):
            if parent in nodes:
                children[parent].add(node_id)
    targets = sorted({
        target
        for item in nodes.values()
        for target in item.get("target_ids", [])
        if target
    }) or ["target-1"]

    def paths_to(node_id: str, active: set[str]) -> list[list[dict[str, Any]]]:
        if node_id in active:
            return [[]]
        node = nodes[node_id]
        parents = [parent for parent in node.get("depends_on", []) if parent in nodes]
        if not parents:
            return [[node]]
        result: list[list[dict[str, Any]]] = []
        for parent in parents:
            for prefix in paths_to(parent, active | {node_id}):
                result.append([*prefix, node])
        return result

    candidates: list[tuple[float, str, list[dict[str, Any]]]] = []
    for target in targets:
        target_nodes = {
            node_id for node_id, item in nodes.items() if target in item.get("target_ids", [])
        }
        terminals = [
            node_id
            for node_id in target_nodes
            if not any(child in target_nodes for child in children[node_id])
        ] or list(target_nodes)
        for terminal in terminals:
            for raw_path in paths_to(terminal, set()):
                conceptual = [
                    item
                    for item in raw_path
                    if item.get("cognitive_operation") != "algebra_only"
                    and item.get("decisive_relations")
                ]
                if not conceptual:
                    continue
                bases = [
                    COGNITIVE_OPERATIONS[str(item["cognitive_operation"])][1]
                    for item in conceptual
                ]
                kinds = {str(item["cognitive_operation"]) for item in conceptual}
                highest = max(bases)
                if "reconstruct" in kinds:
                    # First-principles reconstruction may deepen as several
                    # indispensable derivation links are chained. This is the
                    # only operation allowed a substantial chain increment.
                    cost = highest + min(0.8, 0.2 * (len(conceptual) - 1))
                    if target in set(verification.get("passed_target_ids", [])):
                        cost += 0.4
                else:
                    # For identify/apply/explain/audit, depth is the highest
                    # unavoidable understanding layer. More steps primarily
                    # belong to integration/process, so only a small within-
                    # level refinement is permitted.
                    refinement = min(
                        0.3,
                        0.08 * (len(kinds) - 1)
                        + 0.04 * min(4, len(conceptual) - 1),
                    )
                    cost = highest + refinement
                if any(
                    item.get("cognitive_source") != "w3-enum"
                    for item in conceptual
                ):
                    cost = max(1.0, cost - 0.1)
                candidates.append((min(6.0, cost), target, conceptual))
    if not candidates:
        return {
            "score": 1.0,
            "calibrated_score": 1.0,
            "path_cost": 1.0,
            "target_id": "",
            "step_ids": [],
            "operations": [],
            "source": "deterministic-fallback",
            "level_five_gate": "downgraded-no-decisive-path",
        }
    path_cost, target, conceptual = max(
        candidates,
        key=lambda item: (item[0], len(item[2]), item[1]),
    )
    score = _anchor_score(path_cost)
    exact_enum = all(item.get("cognitive_source") == "w3-enum" for item in conceptual)
    operations = [str(item["cognitive_operation"]) for item in conceptual]
    gate_reasons: list[str] = []
    if "reconstruct" not in operations:
        gate_reasons.append("no-reconstruction")
    if len(conceptual) < 4:
        gate_reasons.append("chain-shorter-than-four")
    if target not in set(verification.get("passed_target_ids", [])):
        gate_reasons.append("target-not-independently-verified")
    if not exact_enum:
        gate_reasons.append("cognitive-operation-inferred")
    if score >= 5.0 and gate_reasons:
        score = min(score, 4.9)
    calibrated_score = _step(score, Decimal("6.0"))
    return {
        "score": _step(min(5.0, calibrated_score)),
        "calibrated_score": calibrated_score,
        "path_cost": round(path_cost, 2),
        "target_id": target,
        "step_ids": [str(item["id"]) for item in conceptual],
        "operations": operations,
        "source": "w3-critical-path" if exact_enum else "deterministic-fallback",
        "level_five_gate": "passed" if not gate_reasons else "downgraded-" + ",".join(gate_reasons),
    }


def standard_path_from_blueprint(
    blueprint: Any,
    verification_report: Any = None,
) -> dict[str, Any] | None:
    """Project a W3 two-layer blueprint into the stable scoring input contract."""
    if not isinstance(blueprint, dict) or blueprint.get("status") != "completed":
        return None
    raw_steps = [
        {
            key: value
            for key, value in item.items()
            if key not in {"cognitive_operation", "knowledge_units"}
        }
        for item in blueprint.get("reasoning_steps", [])
        if isinstance(item, dict)
    ]
    stages = [
        str(item.get("label", "")).strip()
        for item in blueprint.get("physical_stages", [])
        if isinstance(item, dict) and str(item.get("label", "")).strip()
    ]
    operations = [
        str(item.get("operation", "")).strip()
        for item in raw_steps
        if str(item.get("operation", "")).strip()
    ]
    relations = [
        relation
        for item in raw_steps
        for relation in _strings(item.get("decisive_relations"), limit=12)
    ]
    stage_conditions = [
        condition
        for item in blueprint.get("physical_stages", [])
        if isinstance(item, dict)
        for key in ("entry_conditions", "exit_conditions")
        for condition in _strings(item.get(key), limit=12)
        if CONDITION_PATTERN.search(condition)
    ]
    obligation_checks = [
        str(item.get("check", "")).strip()
        for item in blueprint.get("verification_obligations", [])
        if isinstance(item, dict) and str(item.get("check", "")).strip()
    ]
    all_text = "\n".join([*operations, *relations, *stage_conditions, *obligation_checks])
    transforms = _matched_categories(all_text, REPRESENTATION_FAMILIES)
    condition_categories = _matched_categories(all_text, CONDITION_FAMILIES)
    basis = _strings(blueprint.get("high_school_basis"), limit=12)
    if not basis:
        # Retained for legacy display/fallback only. W3 integration scoring uses
        # relation-bound canonical knowledge units from reasoning_steps.
        basis = _matched_categories(all_text, KNOWLEDGE_FAMILIES)
    type_distance = _normalize_type_distance(blueprint.get("type_distance"))
    if type_distance is None:
        type_distance = _infer_type_distance(all_text, transforms)
    report = verification_report if isinstance(verification_report, dict) else {}
    solver_a = report.get("solver_a")
    verifier = report.get("verifier")
    adjudication = report.get("adjudication")
    solved_relations = [
        relation
        for target in (
            solver_a.get("targets", [])
            if isinstance(solver_a, dict)
            else []
        )
        if isinstance(target, dict)
        for relation in _strings(target.get("supporting_relations"), limit=12)
    ]
    verified_relations = [
        relation
        for audit in (
            verifier.get("target_audits", [])
            if isinstance(verifier, dict)
            else []
        )
        if isinstance(audit, dict)
        for relation in _strings(audit.get("decisive_checks"), limit=12)
    ]
    adjudicated_relations = [
        str(item.get("decisive_relation", "")).strip()
        for item in (
            adjudication.get("target_decisions", [])
            if isinstance(adjudication, dict)
            else []
        )
        if isinstance(item, dict) and str(item.get("decisive_relation", "")).strip()
    ]
    assessment_relations = _strings(
        [*solved_relations, *verified_relations, *adjudicated_relations],
        limit=32,
    ) or _strings(relations, limit=16)
    assessment_relation_source = (
        "post-solve-evidence"
        if solved_relations or verified_relations or adjudicated_relations
        else "blueprint-fallback"
    )
    return {
        "schema_version": 1,
        "source": "wuli.problem-decompose.v1",
        "selected_path": " → ".join(operations),
        "high_school_basis": basis,
        "physical_stages": stages or ["单一连续物理过程"],
        "reasoning_steps": operations,
        "reasoning_graph": _normalize_reasoning_graph(raw_steps, operations, relations),
        "decisive_relations": _strings(relations, limit=16),
        "assessment_relations": assessment_relations,
        "assessment_relation_source": assessment_relation_source,
        "representation_transforms": _strings(transforms, limit=8),
        "condition_checks": condition_categories,
        "type_distance": type_distance,
        "target_count": len(
            [item for item in blueprint.get("question_targets", []) if isinstance(item, dict)]
        ),
        "transition_count": len(
            [item for item in blueprint.get("stage_transitions", []) if isinstance(item, dict)]
        ),
        "verification": _verification_summary(verification_report),
    }


def _normalize_path(path: Any) -> dict[str, Any] | None:
    if not isinstance(path, dict):
        return None
    if isinstance(path.get("reasoning_steps"), list) and any(
        isinstance(item, dict) for item in path.get("reasoning_steps", [])
    ):
        projected = standard_path_from_blueprint(path)
        if projected:
            return projected
    steps = _strings(path.get("reasoning_steps"), limit=5)
    stages = _strings(path.get("physical_stages"), limit=12)
    relations = _strings(path.get("decisive_relations"), limit=16)
    basis = _strings(path.get("high_school_basis"), limit=12)
    if not steps or not stages or not relations or not basis:
        return None
    source = str(path.get("source", "standard-solution-path"))[:80]
    default_assessment_source = (
        "blueprint-relations"
        if source.startswith("wuli.problem-decompose")
        else "standard-path"
    )
    normalized = {
        "schema_version": 1,
        "source": source,
        "selected_path": str(path.get("selected_path", "")).strip()[:500],
        "high_school_basis": basis,
        "physical_stages": stages,
        "reasoning_steps": steps,
        "reasoning_graph": [],
        "decisive_relations": relations,
        "assessment_relations": (
            _strings(path.get("assessment_relations"), limit=32) or relations
        ),
        "assessment_relation_source": str(
            path.get("assessment_relation_source", default_assessment_source)
        )[:80],
        "representation_transforms": _strings(
            path.get("representation_transforms"), limit=8
        ),
        "condition_checks": _strings(path.get("condition_checks"), limit=16),
        "type_distance": _normalize_type_distance(path.get("type_distance")),
        "target_count": max(1, int(path.get("target_count", 1) or 1)),
        "transition_count": max(0, int(path.get("transition_count", max(0, len(stages) - 1)) or 0)),
        "verification": (
            path.get("verification")
            if isinstance(path.get("verification"), dict)
            else {"status": "unverified", "passed_target_ids": []}
        ),
    }
    normalized["reasoning_graph"] = _normalize_reasoning_graph(
        [item for item in path.get("reasoning_graph", []) if isinstance(item, dict)][:16],
        steps,
        relations,
    )
    if normalized["type_distance"] is None:
        normalized["type_distance"] = _infer_type_distance(
            "\n".join(
                [
                    normalized["selected_path"],
                    *normalized["reasoning_steps"],
                    *normalized["decisive_relations"],
                    *normalized["condition_checks"],
                ]
            ),
            normalized["representation_transforms"],
        )
    return normalized


def _evidence(locator: str, excerpt: str) -> dict[str, Any]:
    return {
        "source": "standard_solution_path",
        "locator": {"kind": "semantic", "value": locator[:80]},
        "excerpt": excerpt[:220],
    }


PROCESS_TEMPLATES = (
    ("电场运动", re.compile(r"电场|电加速|电偏转|[+＋−-]E(?:0|₀)?")),
    (
        "磁场运动",
        re.compile(
            r"磁场|回旋|螺旋|圆内|圆外|圆弧|磁偏转|曲率偏转|[+＋−-]B(?:0|₀)?"
        ),
    ),
    ("无场或自由运动", re.compile(r"无场|场外|自由|惯性|自由落体")),
    ("碰撞或相互作用", re.compile(r"碰撞|相互作用|爆炸|衰变")),
    ("电路稳态", re.compile(r"电路|充电|放电|稳态|电荷积累|电容")),
    ("电磁感应", re.compile(r"感应|磁通变化|线框|导体棒")),
    ("平衡状态", re.compile(r"平衡|静止")),
)

PROCESS_OUTCOME_ONLY = re.compile(
    r"判定|筛选|极值分支|落点集合|首次返回|首次相遇|首次到达|首次碰|"
    r"闭合|捕获|完备性|解集|释放粒子的首段截断"
)


def _process_profile(
    stages: list[str],
    transforms: list[str],
    graph: list[dict[str, Any]] | None = None,
    relations: list[str] | None = None,
) -> dict[str, Any]:
    graph = graph or []
    relations = relations or []
    templates: list[str] = []
    for stage in stages:
        matched = [
            template
            for template, pattern in PROCESS_TEMPLATES
            if pattern.search(stage)
        ]
        if re.search(r"交替场|电磁交替", stage):
            matched = ["电场运动", "磁场运动"]
        if not matched:
            if PROCESS_OUTCOME_ONLY.search(stage):
                continue
            matched = [
                re.sub(
                    r"第?[一二三四五六七八九十\d]+(?:次|阶段)?", "", stage
                ).strip()
                or "单一状态"
            ]
        for label in matched:
            if label not in templates:
                templates.append(label)
    necessary_ids = _necessary_graph_node_ids(graph)
    necessary_graph = [
        item for item in graph if str(item.get("id", "")) in necessary_ids
    ]
    composition_text = "\n".join([
        *stages,
        *transforms,
        *relations,
        *[
            str(item.get("operation", ""))
            for item in necessary_graph
        ],
    ])
    children: dict[str, set[str]] = {
        str(item.get("id", "")): set() for item in necessary_graph
    }
    for item in necessary_graph:
        node_id = str(item.get("id", ""))
        for parent in item.get("depends_on", []):
            if parent in children:
                children[parent].add(node_id)
    graph_has_split = any(len(items) > 1 for items in children.values())
    graph_has_merge = any(
        len([parent for parent in item.get("depends_on", []) if parent in children]) > 1
        for item in necessary_graph
    )

    temporal = bool(re.search(
        r"截断|释放时|发射时|时窗|时间窗口|相位|周期|递推|场切换|"
        r"首次.{0,10}(?:时间|时刻|相位)|到达时刻序列|模周期",
        composition_text,
    ))
    synchronous = bool(re.search(
        r"三维|螺旋|同步|同时满足|相对距离|共同旋转中心|共圆心|"
        r"横向.{0,16}轴向|轴向.{0,16}横向|两个?粒子|双电子|多对象|"
        r"甲.{0,20}乙|分量.{0,12}(?:叠加|同时)",
        composition_text,
    ))
    branch_language = bool(re.search(
        r"路径分支|两类候选|不同情况|分别传播|分段传播|候选.{0,12}(?:合并|筛选)|"
        r"分支.{0,16}(?:合并|筛选|完备)|先进入.{0,12}再返回",
        composition_text,
    ))
    # A reasoning DAG often fans out merely because several quantities are
    # computed in parallel.  That is not a physical process branch unless the
    # reviewed path explicitly carries different candidate/state paths.
    branch = branch_language
    global_closure = bool(re.search(
        r"完整性|完备性|所有可能|全部候选|唯一性|全局|闭合条件|"
        r"合并.{0,12}(?:筛选|核对)|解集",
        composition_text,
    ))
    sequential = len(templates) > 1 or len(stages) > 1

    signals = {
        "标准串联": sequential,
        "时序组合": temporal,
        "同步耦合": synchronous,
        "分支耦合": branch,
        "全局闭合": global_closure,
    }
    if branch and synchronous and global_closure:
        level = "嵌套全局组合"
        base_score = 5.0
        primary = {"分支耦合", "同步耦合", "全局闭合"}
    elif branch:
        level = "分支耦合"
        base_score = 4.2
        primary = {"分支耦合", "标准串联"}
    elif synchronous:
        level = "同步耦合"
        base_score = 3.4
        primary = {"同步耦合", "标准串联"}
    elif temporal:
        level = "时序组合"
        base_score = 2.5
        primary = {"时序组合", "标准串联"}
    elif sequential:
        level = "标准串联"
        base_score = 1.6
        primary = {"标准串联"}
    else:
        level = "单一状态"
        base_score = 0.8
        primary = set()
    secondary = [
        label for label, present in signals.items()
        if present and label not in primary
    ]
    refinement = 0.0 if base_score >= 5.0 else min(0.3, 0.1 * len(secondary))
    count = max(1, len(templates))
    return {
        "score": _step(base_score + refinement),
        "level": level,
        "base_score": base_score,
        "refinement": refinement,
        "signals": [label for label, present in signals.items() if present],
        "templates": templates,
        "count": count,
        "graph_has_split": graph_has_split,
        "graph_has_merge": graph_has_merge,
    }


CALCULATION_TEXT_PATTERN = re.compile(
    r"=|≈|∝|≤|≥|<|>|Σ|∑|∫|√|"
    r"\b(?:sin|cos|tan|cot)\b|求|计算|联立|消去|代入|反求|函数",
    re.IGNORECASE,
)


def _calculation_profile(
    graph: list[dict[str, Any]],
    relations: list[str],
) -> dict[str, Any]:
    necessary_ids = _necessary_graph_node_ids(graph)
    nodes = {
        str(item.get("id", "")): item
        for item in graph
        if str(item.get("id", "")) in necessary_ids
    }
    calculation_ids = {
        node_id
        for node_id, item in nodes.items()
        if CALCULATION_TEXT_PATTERN.search(
            "\n".join([
                str(item.get("operation", "")),
                *_strings(item.get("decisive_relations"), limit=12),
            ])
        )
    }
    memo: dict[str, int] = {}

    def chain_to(node_id: str, active: set[str]) -> int:
        if node_id in memo:
            return memo[node_id]
        if node_id in active:
            return 0
        parents = [
            parent
            for parent in nodes[node_id].get("depends_on", [])
            if parent in nodes
        ]
        parent_best = max(
            (chain_to(parent, active | {node_id}) for parent in parents),
            default=0,
        )
        value = parent_best + (1 if node_id in calculation_ids else 0)
        memo[node_id] = value
        return value

    longest_chain = max(
        (chain_to(node_id, set()) for node_id in nodes),
        default=0,
    )
    calculation_relations = [
        relation
        for item in nodes.values()
        for relation in _strings(item.get("decisive_relations"), limit=12)
        if CALCULATION_TEXT_PATTERN.search(relation)
    ] or [relation for relation in relations if CALCULATION_TEXT_PATTERN.search(relation)]
    text = "\n".join([
        *relations,
        *[
            str(item.get("operation", ""))
            for item in nodes.values()
        ],
    ])
    nonlinear = bool(re.search(
        r"sin|cos|tan|cot|三角|√|根式|二次方程|平方根|[²³^]",
        text,
        re.IGNORECASE,
    ))
    parametric = bool(re.search(
        r"参数|参数化|函数|[tzrx]\s*\([αβφθ]\)|随.{0,8}变化",
        text,
        re.IGNORECASE,
    ))
    coupled = bool(re.search(
        r"联立|消去|消元|方程组|多个未知|共同求解|同时求",
        text,
    ))
    range_or_extreme = bool(re.search(
        r"范围|区间|极值|最大|最小|边界|临界|取值域",
        text,
    ))
    piecewise = bool(re.search(
        r"分段|不同区间|分别计算|各分支|两类候选|路径分支",
        text,
    ))

    children: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for node_id, item in nodes.items():
        for parent in item.get("depends_on", []):
            if parent in children:
                children[parent].add(node_id)
    branch_calculation = piecewise and (
        any(
            len([child for child in child_ids if child in calculation_ids]) > 1
            for child_ids in children.values()
        )
        and any(
            len([
                parent
                for parent in item.get("depends_on", [])
                if parent in calculation_ids
            ]) > 1
            for item in nodes.values()
        )
    )

    if (
        branch_calculation
        and parametric
        and range_or_extreme
        and longest_chain >= 6
    ):
        level = "极高计算量"
        base_score = 5.0
        primary = {"分支计算", "参数函数", "范围或边界"}
    elif branch_calculation and (parametric or range_or_extreme or piecewise):
        level = "很大计算量"
        base_score = 4.2
        primary = {"分支计算"}
    elif longest_chain >= 4 or parametric or coupled:
        level = "较大计算量"
        base_score = 3.4
        primary = {"长计算链"} if longest_chain >= 4 else set()
    elif (
        longest_chain >= 3
        or nonlinear
        or len(calculation_relations) >= 4
    ):
        level = "中等计算量"
        base_score = 2.5
        primary = {"标准多步计算"}
    elif longest_chain >= 2 or len(calculation_relations) >= 2:
        level = "较少计算量"
        base_score = 1.6
        primary = {"短计算链"}
    else:
        level = "极少计算量"
        base_score = 0.8
        primary = set()

    signals = {
        "非线性表达": nonlinear,
        "参数函数": parametric,
        "联立消元": coupled,
        "范围或边界": range_or_extreme,
        "分段计算": piecewise,
        "分支计算": branch_calculation,
    }
    secondary = [
        label for label, present in signals.items()
        if present and label not in primary
    ]
    refinement = 0.0 if base_score >= 5.0 else min(0.3, 0.1 * len(secondary))
    return {
        "score": _step(base_score + refinement),
        "level": level,
        "base_score": base_score,
        "refinement": refinement,
        "longest_chain": longest_chain,
        "calculation_node_count": len(calculation_ids),
        "calculation_relation_count": len(set(calculation_relations)),
        "signals": [label for label, present in signals.items() if present],
        "branch_calculation": branch_calculation,
    }


CONDITION_BURDENS = (
    ("全局或耦合完备性", re.compile(r"全局|耦合|模周期|时间窗口|时窗|同时满足"), 4.2),
    ("枚举完备性", re.compile(r"全部|所有可能|穷尽|完备|完整|遗漏"), 3.5),
    ("分类讨论", re.compile(r"分类|分支|不同情况|枚举"), 3.0),
    ("首次或唯一性", re.compile(r"首次|第一次|最早|唯一|仅有"), 2.6),
    ("临界或极值", re.compile(r"临界|最大|最小|极值|恰好"), 2.5),
    ("范围与边界", re.compile(r"边界|范围|区间|至少|至多|截止"), 1.8),
    ("方向、符号或适用条件", re.compile(r"方向|正负|符号|适用条件|单位"), 1.0),
)


CONDITION_CATEGORY_LABELS = {
    "分类讨论",
    "临界或极值",
    "首次性",
    "唯一性",
    "范围与边界",
    "完备性",
    "方向与符号",
}


def _condition_profile(
    conditions: list[str],
    relations: list[str] | None = None,
    source: str = "",
    graph: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    graph = graph or []
    category_only = bool(conditions) and all(
        item in CONDITION_CATEGORY_LABELS for item in conditions
    )
    category_only = category_only or source.startswith(
        "teacher-requested-standard-path-backfill"
    )
    # Old W3 paths store an audit taxonomy here.  Taxonomy membership is not
    # evidence of burden, so project the burden from the actual solved
    # relations instead.  A teacher-authored concrete condition remains direct
    # evidence.
    evidence_items = list((relations or []) if category_only else conditions)
    necessary_ids = _necessary_graph_node_ids(graph)
    audit_items = [
        "\n".join([
            str(item.get("operation", "")),
            *_strings(item.get("decisive_relations"), limit=12),
        ])
        for item in graph
        if str(item.get("id", "")) in necessary_ids
        and item.get("cognitive_operation") == "audit"
        and item.get("decisive_relations")
    ]
    evidence_items.extend(audit_items)
    matches: list[tuple[str, float]] = []
    for label, pattern, score in CONDITION_BURDENS:
        if any(pattern.search(item) for item in evidence_items):
            matches.append((label, score))
    if not matches:
        return {
            "score": 0.8,
            "burdens": ["显式常规条件"],
            "count": 0,
            "source": (
                "critical-audit-evidence"
                if audit_items
                else "decisive-relations" if category_only else "condition-checks"
            ),
        }
    highest = max(score for _, score in matches)
    # Several condition kinds may broaden checking work, but checklist length
    # must never turn a routine problem into a five-point completeness proof.
    refinement = min(0.2, 0.1 * (len(matches) - 1))
    return {
        "score": _step(highest + refinement),
        "burdens": [label for label, _ in matches],
        "count": len(matches),
        "source": (
            "critical-audit-evidence"
            if audit_items
            else "decisive-relations" if category_only else "condition-checks"
        ),
        "audit_node_count": len(audit_items),
    }


def _baseline_snapshot(
    *,
    score: int,
    level: str,
    dimensions: list[dict[str, Any]],
    digest: str,
    generated_at: str,
) -> dict[str, Any]:
    return {
        "rubric_version": RUBRIC_VERSION,
        "anchor_version": ANCHOR_VERSION,
        "score": score,
        "level": level,
        "dimension_scores": {
            str(item["id"]): float(item["score"]) for item in dimensions
        },
        "input_digest": digest,
        "generated_at": generated_at,
    }


def unavailable_assessment(problem: str) -> dict[str, Any]:
    generated_at = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "anchor_version": ANCHOR_VERSION,
        "dimension_scale": {"min": 0, "max": 5, "step": 0.1},
        "calibration_scale": {"knowledge_depth_max": 6, "operational_max": 5},
        "status": "awaiting-standard-path",
        "source": RUBRIC_VERSION,
        "score": None,
        "level": "未评分",
        "dimensions": [],
        "summary": "等待形成规范化的标准解题路径后评分。",
        "confidence": 0.0,
        "calibration": {
            "status": "not-applicable",
            "note": "没有标准解题路径时不根据关键词、答案长度或模型运行过程生成伪精确分。",
        },
        "auto_baseline": None,
        "input_digest": input_digest(problem, None),
        "generated_at": generated_at,
        "updated_at": generated_at,
    }


def auto_assess(
    record: dict[str, Any],
    problem: str,
    standard_path: Any = None,
) -> dict[str, Any]:
    """Score intrinsic problem difficulty from a reviewed problem and standard path."""
    del record  # Teaching labels and generated prose are deliberately not scoring inputs.
    path = _normalize_path(standard_path)
    if path is None:
        return unavailable_assessment(problem)

    steps = path["reasoning_steps"]
    graph = path["reasoning_graph"]
    relations = path["decisive_relations"]
    assessment_relations = path["assessment_relations"]
    basis = path["high_school_basis"]
    stages = path["physical_stages"]
    transforms = path["representation_transforms"]
    conditions = path["condition_checks"]
    scoring_text = "\n".join([problem, path["selected_path"], *steps, *relations])
    transform_categories = _matched_categories(
        "\n".join(transforms) or scoring_text,
        REPRESENTATION_FAMILIES,
    )
    condition_categories = _matched_categories(
        "\n".join(conditions) or scoring_text,
        CONDITION_FAMILIES,
    )
    condition_profile = _condition_profile(
        conditions or condition_categories,
        assessment_relations,
        path["source"],
        graph,
    )
    depth_profile = _conceptual_path_profile(graph, path["verification"])
    knowledge_profile = _knowledge_unit_profile(
        graph,
        basis,
        assessment_relations,
        evidence_authoritative=(
            path["assessment_relation_source"]
            in {"post-solve-evidence", "blueprint-relations"}
        ),
    )
    knowledge_units = knowledge_profile["units"]
    process_profile = _process_profile(
        stages,
        transforms,
        graph,
        assessment_relations,
    )
    calculation_profile = _calculation_profile(graph, assessment_relations)
    type_distance = path["type_distance"]
    type_mode = str(type_distance["mode"])
    type_score = TYPE_DISTANCE_MODES[type_mode][1]

    raw = {
        "knowledge_depth": depth_profile["score"],
        "knowledge_integration": _step(
            _count_anchor(
                knowledge_profile["count"],
                (0.0, 1.0, 2.0, 3.0, 3.8, 4.4, 4.8, 5.0),
            )
        ),
        "type_distance_modelling": _step(type_score),
        "process_state": process_profile["score"],
        "calculation_expression": calculation_profile["score"],
        "condition_completeness": condition_profile["score"],
    }
    evidence = {
        "knowledge_depth": [
            _evidence(
                "critical_concept_path",
                f"目标 {depth_profile['target_id'] or '默认目标'} 的最长不可绕过概念链为 "
                f"{len(depth_profile['step_ids'])} 步，路径成本 {depth_profile['path_cost']}，"
                f"按 {ANCHOR_VERSION} 内部校准为 "
                f"{depth_profile['calibrated_score']}/6，正式计分为 {depth_profile['score']}/5；"
                f"五分门槛：{depth_profile['level_five_gate']}。",
            )
        ],
        "knowledge_integration": [
            _evidence(
                "reasoning_graph.knowledge_units",
                f"目标完成切片联合 {knowledge_profile['count']} 个独立且不可绕过的规律/概念单元："
                f"{'、'.join(item['label'] for item in knowledge_units[:6])}；"
                "同一定律的重复使用、分量式与代数变形只计一次。",
            )
        ],
        "type_distance_modelling": [
            _evidence(
                "type_distance",
                f"固定题型距离为“{type_distance['label']}”；"
                f"识别障碍：{type_distance['recognition_barrier'] or '无明显障碍'}。",
            )
        ],
        "process_state": [
            _evidence(
                "reasoning_graph.process_composition",
                f"已知正确模型后，最低过程组合层级为“{process_profile['level']}”；"
                f"证据：{'、'.join(process_profile['signals']) or '单一状态'}。",
            )
        ],
        "calculation_expression": [
            _evidence(
                "reasoning_graph.calculation_chain",
                f"正确列式后的最长必要计算链为 "
                f"{calculation_profile['longest_chain']} 个计算节点，"
                f"属于“{calculation_profile['level']}”；"
                f"证据：{'、'.join(calculation_profile['signals']) or '直接代入'}。",
            )
        ],
        "condition_completeness": [
            _evidence(
                "condition_checks",
                f"条件负担取最高不可绕过类型，不按审计清单条数累加："
                f"{'、'.join(condition_profile['burdens'])}。"
                + (f" 例如：{conditions[0]}。" if conditions else ""),
            )
        ],
    }
    judgments = {
        "knowledge_depth": (
            f"知识深度取目标 {depth_profile['target_id'] or '默认目标'} 的最长不可绕过概念链，"
            f"认知操作为 {' → '.join(depth_profile['operations']) or '未分类'}。"
        ),
        "knowledge_integration": (
            f"需要联合 {knowledge_profile['count']} 个绑定决定性关系的独立规律/概念单元；"
            "并行必要分支均计入，同一定律重复使用只计一次。"
        ),
        "type_distance_modelling": (
            f"距离熟悉高中母题属于“{type_distance['label']}”，"
            f"按固定锚点计 {type_score}/5。"
        ),
        "process_state": (
            f"已知正确知识点后，状态与过程的最低组合层级为"
            f"“{process_profile['level']}”。"
        ),
        "calculation_expression": (
            f"正确建模后，最短标准路径形成"
            f"“{calculation_profile['level']}”。"
        ),
        "condition_completeness": (
            f"最高条件负担为{condition_profile['burdens'][0]}，"
            "教师审计项目数量不参与累计。"
        ),
    }
    dimensions = [
        {
            "id": key,
            "label": label,
            "weight": weight,
            "score": raw[key],
            "scale_max": float(_dimension_max(key)),
            "core_judgment": judgments[key],
            "evidence": evidence[key],
        }
        for key, label, weight in DIMENSIONS
    ]
    total = _score(dimensions)
    generated_at = now_iso()
    digest = input_digest(problem, path)
    strongest = sorted(dimensions, key=lambda item: (-item["score"], item["id"]))[:2]
    completeness = sum(
        bool(path[key])
        for key in (
            "high_school_basis",
            "physical_stages",
            "reasoning_steps",
            "decisive_relations",
        )
    )
    confidence = round(0.55 + 0.1 * completeness, 2)
    baseline = _baseline_snapshot(
        score=total,
        level=level_for(total),
        dimensions=dimensions,
        digest=digest,
        generated_at=generated_at,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "anchor_version": ANCHOR_VERSION,
        "dimension_scale": {"min": 0, "max": 5, "step": 0.1},
        "calibration_scale": {"knowledge_depth_max": 6, "operational_max": 5},
        "status": "default-accepted",
        "source": RUBRIC_VERSION,
        "score": total,
        "level": level_for(total),
        "dimensions": dimensions,
        "summary": f"{level_for(total)}：主要难点在{'、'.join(item['label'] for item in strongest)}。",
        "confidence": confidence,
        "calibration": {
            "status": "baseline",
            "note": (
                "固定标杆校准；内部知识深度标杆可到 6，但正式计分封顶 5；"
                "越过 5 的校准必须具备第一性重建链与独立验证。"
            ),
        },
        "knowledge_depth_trace": depth_profile,
        "knowledge_integration_trace": knowledge_profile,
        "process_state_trace": process_profile,
        "calculation_expression_trace": calculation_profile,
        "condition_completeness_trace": condition_profile,
        "auto_baseline": baseline,
        "input_digest": digest,
        "generated_at": generated_at,
        "updated_at": generated_at,
    }


def current(assessment: Any, problem: str, standard_path: Any = None) -> bool:
    path = _normalize_path(standard_path)
    expected = input_digest(problem, path)
    return (
        isinstance(assessment, dict)
        and assessment.get("rubric_version") == RUBRIC_VERSION
        and assessment.get("input_digest") == expected
    )


def normalize_teacher_edit(
    value: Any,
    problem: str,
    standard_path: Any,
    *,
    baseline: Any = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("难度量表格式错误")
    path = _normalize_path(standard_path)
    if path is None:
        raise ValueError("标准解题路径尚未形成，不能校准精确分")
    supplied = value.get("dimensions")
    if not isinstance(supplied, list) or len(supplied) != len(DIMENSIONS):
        raise ValueError("难度量表必须包含六个维度")
    by_id = {str(item.get("id", "")): item for item in supplied if isinstance(item, dict)}
    dimensions = []
    for key, label, weight in DIMENSIONS:
        item = by_id.get(key)
        if not item:
            raise ValueError(f"缺少难度维度：{label}")
        raw_score = item.get("score")
        maximum = _dimension_max(key)
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise ValueError("维度评分必须是合法数字")
        decimal = Decimal(str(raw_score))
        if (
            decimal < 0
            or decimal > maximum
            or decimal != decimal.quantize(DIMENSION_STEP)
        ):
            raise ValueError(f"{label}评分必须是 0–{maximum:g} 且步长为 0.1")
        judgment = str(item.get("core_judgment", "")).strip()
        if not judgment or len(judgment) > 180:
            raise ValueError(f"{label}的核心判断应为 1–180 个字符")
        supplied_evidence = (
            item.get("evidence") if isinstance(item.get("evidence"), list) else []
        )
        evidence = [
            entry
            for entry in supplied_evidence
            if isinstance(entry, dict) and entry.get("source") and entry.get("excerpt")
        ][:3]
        dimensions.append(
            {
                "id": key,
                "label": label,
                "weight": weight,
                "score": float(decimal),
                "scale_max": float(maximum),
                "core_judgment": judgment,
                "evidence": evidence,
            }
        )
    summary = str(value.get("summary", "")).strip()
    if not summary or len(summary) > 300:
        raise ValueError("难度总结应为 1–300 个字符")

    baseline_value = baseline if isinstance(baseline, dict) else value.get("auto_baseline")
    if not isinstance(baseline_value, dict):
        raise ValueError("缺少可追溯的自动评分基线")
    baseline_scores = (
        baseline_value.get("dimension_scores")
        if isinstance(baseline_value.get("dimension_scores"), dict)
        else {}
    )
    changed = [
        item["id"]
        for item in dimensions
        if float(item["score"]) != float(baseline_scores.get(item["id"], item["score"]))
    ]
    teacher_note = str(value.get("teacher_note", "")).strip()
    if changed and not teacher_note:
        raise ValueError("修改自动评分时请填写简短的教师校准依据")
    if len(teacher_note) > 300:
        raise ValueError("教师校准依据不能超过 300 个字符")

    total = _score(dimensions)
    generated_at = str(value.get("generated_at") or now_iso())
    return {
        "schema_version": SCHEMA_VERSION,
        "rubric_version": RUBRIC_VERSION,
        "anchor_version": ANCHOR_VERSION,
        "dimension_scale": {"min": 0, "max": 5, "step": 0.1},
        "calibration_scale": {"knowledge_depth_max": 6, "operational_max": 5},
        "status": "teacher-edited",
        "source": "teacher-console",
        "score": total,
        "level": level_for(total),
        "dimensions": dimensions,
        "summary": summary,
        "confidence": 1.0,
        "calibration": {
            "status": "teacher-calibrated",
            "note": teacher_note or "教师确认自动基线。",
            "changed_dimensions": changed,
        },
        "auto_baseline": baseline_value,
        "input_digest": input_digest(problem, path),
        "generated_at": generated_at,
        "updated_at": now_iso(),
    }


def _assessment_context(entry: Path) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    from kb import load_json  # Lazy import keeps this module independently testable.

    record = load_json(entry / "record.json", {})
    problem = (
        (entry / "problem.md").read_text(encoding="utf-8")
        if (entry / "problem.md").is_file()
        else ""
    )
    path = record.get("standard_solution_path")
    return record, problem, path if isinstance(path, dict) else None
