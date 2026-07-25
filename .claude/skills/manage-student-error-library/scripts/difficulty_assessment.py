#!/usr/bin/env python3
"""Evidence-backed, teacher-calibratable objective-difficulty rubric for physics entries."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


DIMENSIONS = (
    ("knowledge_depth", "知识点深度", 30),
    ("knowledge_integration", "多知识点交叉", 20),
    ("type_distance", "题型距离与表征转换", 15),
    ("structure", "题目长度与结构", 15),
    ("calculation_expression", "运算与规范表达", 10),
    ("condition_discernment", "干扰信息与条件辨析", 10),
)
LEVELS = ((20, "基础"), (40, "较易"), (60, "中等"), (80, "较难"), (100, "挑战"))
SCHEMA_VERSION = 2
RUBRIC_VERSION = "evidence-rubric.v2"
MAX_DIMENSION_SCORE = Decimal("5.0")
DIMENSION_STEP = Decimal("0.1")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def input_digest(problem: str, answer: str, model: Any = None) -> str:
    model_text = json.dumps(model, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if model else ""
    return hashlib.sha256((problem + "\0" + answer + "\0" + model_text).encode("utf-8")).hexdigest()


def level_for(score: int) -> str:
    for maximum, label in LEVELS:
        if score <= maximum:
            return label
    return "挑战"


def _step(value: float) -> float:
    decimal = min(MAX_DIMENSION_SCORE, max(Decimal("0"), Decimal(str(value))))
    return float(decimal.quantize(DIMENSION_STEP, rounding=ROUND_HALF_UP))


def _score(dimensions: list[dict[str, Any]]) -> int:
    value = sum(Decimal(str(item["score"])) / MAX_DIMENSION_SCORE * Decimal(str(item["weight"])) for item in dimensions)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _matches(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term in text]


def _evidence(source: str, locator: str, excerpt: str) -> dict[str, Any]:
    return {"source": source, "locator": {"kind": "semantic", "value": locator[:80]}, "excerpt": excerpt[:180]}


def _model_signals(model: Any) -> tuple[int, int, int]:
    if not isinstance(model, dict):
        return 0, 0, 0
    events = model.get("event_model", {}) if isinstance(model.get("event_model"), dict) else {}
    timeline = events.get("timeline", []) if isinstance(events.get("timeline"), list) else []
    cases = events.get("cases", []) if isinstance(events.get("cases"), list) else []
    trajectory = events.get("trajectory", {}) if isinstance(events.get("trajectory"), dict) else {}
    segments = trajectory.get("segments", []) if isinstance(trajectory.get("segments"), list) else []
    return len(timeline), len(cases), len(segments)


def _assessment_context(entry: Path) -> tuple[dict[str, Any], str, str, dict[str, Any] | None]:
    from kb import load_json  # Lazy import keeps this module independently testable.
    record = load_json(entry / "record.json", {})
    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").is_file() else ""
    answer = (entry / "student-solution.md").read_text(encoding="utf-8") if (entry / "student-solution.md").is_file() else ""
    model_path = entry / "physics-model.json"
    model = load_json(model_path, {}) if model_path.is_file() else None
    return record, problem, answer, model


def auto_assess(record: dict[str, Any], problem: str, answer: str, model: Any = None) -> dict[str, Any]:
    """Make an explainable baseline from demonstrated reasoning structure, not title keywords alone."""
    text = f"{record.get('title', '')}\n{problem}\n{answer}".lower()
    points = [str(item) for item in record.get("knowledge_points", []) if str(item).strip()]
    parts = len(re.findall(r"(?:（|\()\s*[1-9][0-9]*\s*(?:）|\))", problem))
    formulae = len(re.findall(r"\$[^$]+\$|=|\\frac|\\dfrac", problem + answer))
    events, cases, segments = _model_signals(model)
    dynamic = _matches(text, ("交替", "分段", "周期", "多过程", "多区域", "复合场", "时刻"))
    representation = _matches(text, ("图像", "图示", "坐标", "几何", "函数", "曲线", "阶梯"))
    modelling = _matches(text, ("建模", "分类讨论", "事件", "枚举", "轨迹", "反推", "捕获", "临界", "最小", "最大"))
    conditions = _matches(text, ("方向", "条件", "不计", "仅", "首次", "范围", "期间", "限制", "恰好"))
    time_graph = bool(_matches(text, ("变化规律", "阶梯", "周期为", "横轴", "图2", "图像")))
    inverse_target = bool(_matches(text, ("捕获", "反推", "什么时刻", "哪些时刻", "接收器")))
    domains = sum(bool(_matches(text, group)) for group in (
        ("电场", "电势", "电压"), ("磁场", "洛伦兹", "圆周"), ("动能", "做功", "能量"),
        ("坐标", "位移", "速度"), ("时间", "周期", "时刻"),
    ))

    # Each score is tied to an observable solution feature. The model adds verified
    # event/case structure when a reviewed interactive model is present.
    raw = {
        "knowledge_depth": _step(1.0 + min(1.0, len(points) * 0.2) + min(1.2, len(dynamic) * 0.25) + min(1.4, len(modelling) * 0.28) + (0.4 if events >= 4 else 0) + (0.8 if events >= 10 else 0)),
        "knowledge_integration": _step(0.6 + max(0, domains - 1) * 0.65 + min(1.2, len(dynamic) * 0.18) + (0.5 if events >= 4 and cases >= 2 else 0) + (0.1 if events >= 10 else 0)),
        "type_distance": _step(0.5 + min(1.4, len(representation) * 0.35) + min(1.8, len(modelling) * 0.32) + min(0.9, len(dynamic) * 0.16) + (0.6 if events >= 4 else 0) + (1.0 if time_graph else 0) + (1.0 if inverse_target else 0)),
        "structure": _step(0.5 + min(1.2, parts * 0.35) + min(1.4, events * 0.18) + min(1.0, cases * 0.45) + min(0.9, segments * 0.12) + (1.2 if events >= 10 else 0)),
        "calculation_expression": _step(0.5 + min(2.4, formulae * 0.10) + min(1.2, len(modelling) * 0.18) + (0.5 if cases >= 2 else 0) + (0.5 if events >= 10 else 0)),
        "condition_discernment": _step(0.4 + min(1.8, len(conditions) * 0.25) + min(1.5, len(modelling) * 0.22) + min(0.8, len(dynamic) * 0.14) + (0.5 if cases >= 2 else 0) + (0.6 if events >= 10 else 0)),
    }
    evidence = {
        "knowledge_depth": [_evidence("student_solution", "建模与连续推理", f"解析呈现 {len(modelling)} 个建模、枚举或反推节点；题干有 {len(dynamic)} 个时序过程特征。 ")],
        "knowledge_integration": [_evidence("problem", "必要物理域", f"题目与解析实际涉及 {domains} 类物理关系；知识点记录 {len(points)} 项。")],
        "type_distance": [_evidence("problem", "表征转换", f"题干包含 {len(representation)} 个图像/坐标/函数线索，并要求 {len(modelling)} 项迁移或反推。")],
        "structure": [_evidence("physics_model" if events else "problem", "event_model.timeline" if events else "题目小问", f"题干含 {parts} 个显式小问；已复核模型有 {events} 个事件、{cases} 个分支、{segments} 段轨迹。")],
        "calculation_expression": [_evidence("student_solution", "公式与推导链", f"题目与解析包含 {formulae} 个公式或等式表达，并关联 {len(modelling)} 个推理节点。")],
        "condition_discernment": [_evidence("problem", "限制条件与时序", f"检测到 {len(conditions)} 个限制条件、{len(dynamic)} 个时序切换、{len(modelling)} 个临界或反向判断线索。")],
    }
    judgments = {
        "knowledge_depth": "需要综合分析、建模并把规律用于连续推理。" if raw["knowledge_depth"] >= 3 else "以理解应用和连续推理为主。",
        "knowledge_integration": f"解题中需要联动 {domains} 类物理关系，而非只计知识点标签。",
        "type_distance": "需在时序图、轨迹、坐标或条件模型之间转换，并完成迁移或反推。" if raw["type_distance"] >= 3 else "与教材常规母题保持较近距离。",
        "structure": f"包含 {parts} 个显式小问，并由 {events} 个事件、{cases} 个分支或 {segments} 段过程维持衔接。",
        "calculation_expression": "包含多段推导、参数表达或规范图像/符号表达。" if raw["calculation_expression"] >= 3 else "计算链和规范表达负担适中。",
        "condition_discernment": "需要辨析时序切换、限制条件、临界或反向筛选。" if raw["condition_discernment"] >= 3 else "条件以显性给出为主，辨析负担有限。",
    }
    dimensions = [{"id": key, "label": label, "weight": weight, "score": raw[key], "core_judgment": judgments[key], "evidence": evidence[key]} for key, label, weight in DIMENSIONS]
    total = _score(dimensions)
    strongest = sorted(dimensions, key=lambda item: (-item["score"], item["id"]))[:2]
    confidence = round(min(0.95, 0.45 + 0.04 * min(formulae, 8) + 0.06 * len(modelling) + 0.05 * min(events, 4)), 2)
    return {
        "schema_version": SCHEMA_VERSION, "rubric_version": RUBRIC_VERSION, "dimension_scale": {"min": 0, "max": 5, "step": 0.1},
        "status": "default-accepted", "source": RUBRIC_VERSION, "score": total, "level": level_for(total), "dimensions": dimensions,
        "summary": f"{level_for(total)}：主要难点在{'、'.join(item['label'] for item in strongest)}。",
        "confidence": confidence, "calibration": {"status": "baseline", "note": "可由教师逐维校准；不会阻断答案复核。"},
        "input_digest": input_digest(problem, answer, model), "generated_at": now_iso(), "updated_at": now_iso(),
    }


def current(assessment: Any, problem: str, answer: str, model: Any = None) -> bool:
    return isinstance(assessment, dict) and assessment.get("rubric_version") == RUBRIC_VERSION and assessment.get("input_digest") == input_digest(problem, answer, model)


def normalize_teacher_edit(value: Any, problem: str, answer: str, model: Any = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("难度量表格式错误")
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
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise ValueError("维度评分必须是 0–5 的数字")
        decimal = Decimal(str(raw_score))
        if decimal < 0 or decimal > MAX_DIMENSION_SCORE or decimal != decimal.quantize(DIMENSION_STEP):
            raise ValueError("维度评分必须是 0–5 且步长为 0.1")
        judgment = str(item.get("core_judgment", "")).strip()
        if not judgment or len(judgment) > 180:
            raise ValueError(f"{label}的核心判断应为 1–180 个字符")
        supplied_evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        evidence = [entry for entry in supplied_evidence if isinstance(entry, dict) and entry.get("source") and entry.get("excerpt")][:3]
        dimensions.append({"id": key, "label": label, "weight": weight, "score": float(decimal), "core_judgment": judgment, "evidence": evidence})
    summary = str(value.get("summary", "")).strip()
    if not summary or len(summary) > 300:
        raise ValueError("难度总结应为 1–300 个字符")
    return {
        "schema_version": SCHEMA_VERSION, "rubric_version": RUBRIC_VERSION, "dimension_scale": {"min": 0, "max": 5, "step": 0.1},
        "status": "teacher-edited", "source": "teacher-console", "score": _score(dimensions), "level": level_for(_score(dimensions)), "dimensions": dimensions,
        "summary": summary, "confidence": 1.0, "calibration": {"status": "teacher-calibrated", "note": "教师逐维修改。"},
        "input_digest": input_digest(problem, answer, model), "generated_at": str(value.get("generated_at") or now_iso()), "updated_at": now_iso(),
    }
