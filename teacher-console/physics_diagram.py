#!/usr/bin/env python3
"""Typed physics-scene contract, semantic gate, and deterministic SVG renderer."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
from pathlib import Path
from typing import Any

import physics_diagram_assets
import svg_collaboration
from visual_facts import normalize_payload as normalize_visual_facts

SCENE_SCHEMA = "wuli.physics-diagram-scene.v1"
GATE_SCHEMA = "wuli.physics-diagram-gate-result.v1"
SCENE_PATH = "physics-diagram-scene.json"
GATE_PATH = "physics-diagram-gate.json"
SVG_PATH = "assets/explanatory.svg"
PROVENANCE_PATH = "svg-provenance.json"
REJECTED_SCENE_PATH = ".agent-context/rejected-physics-diagram-scene.json"
DIAGNOSTICS_PATH = ".agent-context/physics-diagram-diagnostics.json"
OBLIGATIONS_PATH = ".agent-context/diagram-obligations.json"
TRANSPORT_METADATA_FIELDS = {"model", "model_tier", "requested_tier", "usage"}

REGION_KINDS = ("magnetic", "electric", "neutral")
DIRECTIONS = ("none", "left", "right", "up", "down", "into-page", "out-of-page")
OBJECT_KINDS = ("particle", "plate", "point", "wire", "boundary", "capacitor", "detector", "source")
POLARITIES = ("none", "positive", "negative")
PATH_KINDS = ("trajectory", "field-line", "connector", "dimension", "axis")
PATH_GEOMETRIES = ("line", "polyline", "smooth", "circular-arc")
REPAIRABLE_DIAGNOSTIC_CODES = {
    "scene.panel-count",
    "scene.path-point-count",
    "scene.circular-arc-degenerate",
    "scene.schema",
    "scene.semantic-obligation",
}


POINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "x": {"type": "number", "minimum": 0, "maximum": 100},
        "y": {"type": "number", "minimum": 0, "maximum": 100},
    },
    "required": ["x", "y"],
}


def _string_array(max_items: int = 32) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "maxItems": max_items}


def _box_properties(*, include_kind: tuple[str, ...]) -> dict[str, Any]:
    return {
        "id": {"type": "string"},
        "panel_id": {"type": "string"},
        "kind": {"type": "string", "enum": list(include_kind)},
        "x": {"type": "number", "minimum": 0, "maximum": 100},
        "y": {"type": "number", "minimum": 0, "maximum": 100},
        "width": {"type": "number", "minimum": 0, "maximum": 100},
        "height": {"type": "number", "minimum": 0, "maximum": 100},
        "label": {"type": "string"},
        "fact_ids": _string_array(16),
    }


SCENE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "title": {"type": "string"},
        "panels": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["id", "title"],
            },
        },
        "regions": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {**_box_properties(include_kind=REGION_KINDS), "direction": {"enum": list(DIRECTIONS)}},
                "required": ["id", "panel_id", "kind", "x", "y", "width", "height", "label", "fact_ids", "direction"],
            },
        },
        "objects": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {**_box_properties(include_kind=OBJECT_KINDS), "polarity": {"enum": list(POLARITIES)}},
                "required": ["id", "panel_id", "kind", "x", "y", "width", "height", "label", "fact_ids", "polarity"],
            },
        },
        "paths": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "panel_id": {"type": "string"},
                    "kind": {"enum": list(PATH_KINDS)},
                    "geometry": {"enum": list(PATH_GEOMETRIES)},
                    "points": {"type": "array", "items": POINT_SCHEMA, "maxItems": 32},
                    "label": {"type": "string"},
                    "direction": {"enum": list(DIRECTIONS)},
                    "fact_ids": _string_array(16),
                },
                "required": ["id", "panel_id", "kind", "geometry", "points", "label", "direction", "fact_ids"],
            },
        },
        "annotations": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "panel_id": {"type": "string"},
                    "x": {"type": "number", "minimum": 0, "maximum": 100},
                    "y": {"type": "number", "minimum": 0, "maximum": 100},
                    "text": {"type": "string"},
                    "fact_ids": _string_array(16),
                },
                "required": ["id", "panel_id", "x", "y", "text", "fact_ids"],
            },
        },
        "omissions": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"fact_id": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["fact_id", "reason"],
            },
        },
    },
    "required": ["status", "message", "title", "panels", "regions", "objects", "paths", "annotations", "omissions"],
}


def output_contract() -> dict[str, Any]:
    return {
        "name": SCENE_SCHEMA,
        "schema": SCENE_OUTPUT_SCHEMA,
        "instructions": (
            "只输出强类型物理场景配方，不输出 SVG、Markdown 或解题流程图。优先从 diagram-obligations.json 的"
            "component_catalog 选择语义组件并组合；不要发明新的固定 SVG 素材。图元坐标均为所属面板内 0–100 的相对坐标；"
            "面板只给 id 和 title，程序会自动排版，禁止让多个物理阶段共用同一面板。"
            "panels 表示并列物理阶段或分支；regions 表示磁场、电场或中性区域；objects 表示粒子、极板、边界、"
            "电容器等物理对象；paths 中 trajectory 表示真实运动轨迹。path 的 kind 只表达教学语义，geometry "
            "单独表达形状：line 恰好 2 点，polyline 至少 2 点，smooth 至少 3 点，circular-arc 必须恰好 3 个互异且"
            "不共线的点，依次为起点、中间经过点、终点；真实圆周段优先 circular-arc，禁止给 circular-arc 多于 3 点。"
            "若存在 physics-model.json，trajectory、关键事件、边界顺序和最终坐标由确定性编译器接管，"
            "你提供的对应坐标只作为布局草案。必须依据 problem.md、已生成答案和"
            "visual-facts.json，不得把解题步骤画成方框箭头。每条经使用的视觉事实 ID 写入对应 primitive.fact_ids；"
            "确实不能画出的低置信事实写入 omissions 并说明原因。带电粒子在电场或磁场中运动时必须同时给出场区、"
            "粒子/关键点和 trajectory；分段或分支运动优先使用多个面板。若无法忠实表示则 status=unsupported，"
            "其余数组置空，禁止退化为流程图。"
            "周期/交替 B、E：运动与时间图分面；每张时间图用两条 axis 表示 t 和场强，用带明确 B(t)/E(t) 标签的 "
            "field-line 画波形，用 annotation 或 path label 标明 T_B/T_E、半周期或阶段。"
            "电场和磁场同时沿纸面法向、运动需要空间表达：二维 SVG 明确标注‘投影/空间示意’，至少两条带 "
            "x/y/z 轴标签的 axis；无法忠实表达则 status=unsupported。"
        ),
    }


PATCH_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string"},
        "patches": {
            "type": "array",
            "maxItems": 24,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "op": {"type": "string", "enum": ["add", "remove", "replace"]},
                    "path": {"type": "string"},
                    "value": {},
                },
                "required": ["op", "path", "value"],
            },
        },
    },
    "required": ["status", "message", "patches"],
}


def patch_output_contract() -> dict[str, Any]:
    return {
        "name": "wuli.physics-diagram-scene-patch.v1",
        "schema": PATCH_OUTPUT_SCHEMA,
        "instructions": (
            "只返回针对上轮场景的最小 JSON Patch，不得重写整张场景。"
            "patches 按顺序执行；path 使用 JSON Pointer，只能修改 title、message、panels、regions、objects、"
            "paths、annotations、omissions 下的字段。remove 也必须给 value，可设为 null。"
            "只修复 diagnostics 指出的依赖锥；保留其余已经正确的图元、事实绑定和教学语义。"
            "若无法在不引入新物理事实的前提下修复，返回 status=unsupported 且 patches=[]。"
        ),
    }


def _diagnostic(
    code: str, message: str, *, path: str = "", expected: Any = None, actual: Any = None, auto_repairable: bool = False
) -> dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "message": message,
        "expected": expected,
        "actual": actual,
        "auto_repairable": auto_repairable,
    }


def _normalization_diagnostic(exc: ValueError, payload: dict[str, Any]) -> dict[str, Any]:
    message = str(exc)
    if message == "completed scene requires 1..3 panels":
        panels = payload.get("panels")
        return _diagnostic(
            "scene.panel-count",
            message,
            path="/panels",
            expected="1..3",
            actual=len(panels) if isinstance(panels, list) else type(panels).__name__,
            auto_repairable=False,
        )
    path_match = re.search(r"paths\[(\d+)\]", message)
    path_index = int(path_match.group(1)) if path_match else None
    point_path = f"/paths/{path_index}/points" if path_index is not None else "/paths"
    paths = payload.get("paths")
    points = (
        paths[path_index].get("points")
        if isinstance(paths, list)
        and path_index is not None
        and path_index < len(paths)
        and isinstance(paths[path_index], dict)
        else None
    )
    if ("geometry requires" in message or "requires 2..32 points" in message) and "points" in message:
        return _diagnostic(
            "scene.path-point-count",
            message,
            path=point_path,
            expected="geometry-specific point count",
            actual=len(points) if isinstance(points, list) else type(points).__name__,
            auto_repairable=False,
        )
    if "circular-arc points" in message:
        return _diagnostic(
            "scene.circular-arc-degenerate",
            message,
            path=point_path,
            expected="three distinct non-collinear points",
            actual=points,
            auto_repairable=False,
        )
    return _diagnostic("scene.schema", message, auto_repairable=False)


def _semantic_diagnostics(gate: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for raw_message in gate.get("errors", []):
        message = str(raw_message)
        if "trajectory" in message or "axis" in message or "field-line" in message or "wave" in message:
            path = "/paths"
        elif "region" in message:
            path = "/regions"
        elif "particle or key point" in message:
            path = "/objects"
        elif "scene corpus" in message:
            path = "/annotations"
        elif "visual fact" in message:
            path = "/fact_ids"
        else:
            path = ""
        diagnostics.append(
            _diagnostic(
                "scene.semantic-obligation",
                message,
                path=path,
                expected="obligation satisfied",
                actual="missing or conflicting scene evidence",
                auto_repairable=False,
            )
        )
    return diagnostics


def _aggregate_preflight_diagnostics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect independent structural failures without stopping at the first path."""
    diagnostics: list[dict[str, Any]] = []
    panels = payload.get("panels")
    if isinstance(panels, list) and payload.get("status") == "completed" and not 1 <= len(panels) <= 3:
        diagnostics.append(
            _diagnostic(
                "scene.panel-count",
                "completed scene requires 1..3 panels",
                path="/panels",
                expected="1..3",
                actual=len(panels),
            )
        )
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return diagnostics
    for index, item in enumerate(paths):
        if not isinstance(item, dict):
            continue
        geometry = item.get("geometry")
        points = item.get("points")
        path = f"/paths/{index}/points"
        if not isinstance(points, list):
            diagnostics.append(
                _diagnostic(
                    "scene.path-point-count",
                    f"paths[{index}] points must be a list",
                    path=path,
                    expected="geometry-specific point list",
                    actual=type(points).__name__,
                )
            )
            continue
        required: str | None = None
        invalid_count = False
        if geometry == "line":
            required, invalid_count = "exactly 2", len(points) != 2
        elif geometry == "polyline":
            required, invalid_count = "at least 2", len(points) < 2
        elif geometry == "smooth":
            required, invalid_count = "at least 3", len(points) < 3
        elif geometry == "circular-arc":
            required, invalid_count = "exactly 3", len(points) != 3
        if invalid_count:
            diagnostics.append(
                _diagnostic(
                    "scene.path-point-count",
                    f"paths[{index}] {geometry} geometry requires {required} points",
                    path=path,
                    expected=required,
                    actual=len(points),
                )
            )
            continue
        if geometry != "circular-arc" or len(points) != 3:
            continue
        if any(
            not isinstance(point, dict)
            or isinstance(point.get("x"), bool)
            or isinstance(point.get("y"), bool)
            or not isinstance(point.get("x"), (int, float))
            or not isinstance(point.get("y"), (int, float))
            for point in points
        ):
            continue
        p0, p1, p2 = points
        point_pairs = [(float(point["x"]), float(point["y"])) for point in points]
        distinct = len(set(point_pairs)) == 3
        cross = abs(
            (float(p1["x"]) - float(p0["x"])) * (float(p2["y"]) - float(p0["y"]))
            - (float(p1["y"]) - float(p0["y"])) * (float(p2["x"]) - float(p0["x"]))
        )
        max_squared_edge = max(
            (point_pairs[0][0] - point_pairs[1][0]) ** 2 + (point_pairs[0][1] - point_pairs[1][1]) ** 2,
            (point_pairs[1][0] - point_pairs[2][0]) ** 2 + (point_pairs[1][1] - point_pairs[2][1]) ** 2,
            (point_pairs[2][0] - point_pairs[0][0]) ** 2 + (point_pairs[2][1] - point_pairs[0][1]) ** 2,
        )
        if not distinct or max_squared_edge <= 1e-12 or cross / max_squared_edge <= 1e-4:
            diagnostics.append(
                _diagnostic(
                    "scene.circular-arc-degenerate",
                    f"paths[{index}] circular-arc points are nearly collinear or degenerate",
                    path=path,
                    expected="three distinct non-collinear points",
                    actual=points,
                )
            )
    return diagnostics


def _deduplicate_diagnostics(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in diagnostics:
        key = (str(item.get("code", "")), str(item.get("path", "")), str(item.get("message", "")))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _text(value: Any, field: str, maximum: int = 160) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{field} is too long")
    return value


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    result = float(value)
    if result < 0 or result > 100 or (positive and result <= 0):
        raise ValueError(f"{field} must be {'in (0, 100]' if positive else 'in [0, 100]'}")
    return result


def normalize_scene(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("scene must be an object")
    expected = set(SCENE_OUTPUT_SCHEMA["required"])
    actual = set(raw) - TRANSPORT_METADATA_FIELDS
    if actual != expected:
        raise ValueError(f"scene fields mismatch: {sorted(actual ^ expected)}")
    status = _text(raw["status"], "status")
    if status not in {"completed", "unsupported"}:
        raise ValueError("invalid scene status")
    result: dict[str, Any] = {
        "schema": SCENE_SCHEMA,
        "status": status,
        "message": _text(raw["message"], "message", 500),
    }
    result["title"] = _text(raw["title"], "title", 120)
    for field in ("panels", "regions", "objects", "paths", "annotations", "omissions"):
        if not isinstance(raw[field], list):
            raise ValueError(f"{field} must be a list")
    if status == "unsupported":
        if any(raw[field] for field in ("panels", "regions", "objects", "paths", "annotations", "omissions")):
            raise ValueError("unsupported scene arrays must be empty")
        return {**result, "panels": [], "regions": [], "objects": [], "paths": [], "annotations": [], "omissions": []}
    if not 1 <= len(raw["panels"]) <= 3:
        raise ValueError("completed scene requires 1..3 panels")

    seen: set[str] = set()
    panels: list[dict[str, Any]] = []
    for index, item in enumerate(raw["panels"]):
        if not isinstance(item, dict) or set(item) != {"id", "title"}:
            raise ValueError(f"panels[{index}] fields mismatch")
        panel_id = _text(item["id"], f"panels[{index}].id", 48)
        if not panel_id or panel_id in seen:
            raise ValueError("panel IDs must be unique and non-empty")
        seen.add(panel_id)
        panels.append({"id": panel_id, "title": _text(item["title"], f"panels[{index}].title", 80)})
    panel_ids = {item["id"] for item in panels}

    collections: dict[str, list[dict[str, Any]]] = {key: [] for key in ("regions", "objects", "paths", "annotations")}
    allowed_fields = {
        "regions": {"id", "panel_id", "kind", "x", "y", "width", "height", "label", "fact_ids", "direction"},
        "objects": {"id", "panel_id", "kind", "x", "y", "width", "height", "label", "fact_ids", "polarity"},
        "paths": {"id", "panel_id", "kind", "geometry", "points", "label", "direction", "fact_ids"},
        "annotations": {"id", "panel_id", "x", "y", "text", "fact_ids"},
    }
    enum_by_field = {"regions": REGION_KINDS, "objects": OBJECT_KINDS, "paths": PATH_KINDS}
    for field in collections:
        for index, item in enumerate(raw[field]):
            if not isinstance(item, dict) or set(item) != allowed_fields[field]:
                raise ValueError(f"{field}[{index}] fields mismatch")
            item_id = _text(item["id"], f"{field}[{index}].id", 48)
            if not item_id or item_id in seen:
                raise ValueError("all primitive IDs must be unique and non-empty")
            seen.add(item_id)
            panel_id = _text(item["panel_id"], f"{field}[{index}].panel_id", 48)
            if panel_id not in panel_ids:
                raise ValueError(f"unknown panel: {panel_id}")
            clean: dict[str, Any] = {"id": item_id, "panel_id": panel_id}
            if field in enum_by_field:
                kind = _text(item["kind"], f"{field}[{index}].kind", 32)
                if kind not in enum_by_field[field]:
                    raise ValueError(f"invalid {field} kind")
                clean["kind"] = kind
            if field in {"regions", "objects"}:
                for key in ("x", "y"):
                    clean[key] = _number(item[key], f"{field}[{index}].{key}")
                for key in ("width", "height"):
                    clean[key] = _number(item[key], f"{field}[{index}].{key}", positive=True)
                if clean["x"] + clean["width"] > 100 or clean["y"] + clean["height"] > 100:
                    raise ValueError(f"{item_id} exceeds panel")
                clean["label"] = _text(item["label"], f"{field}[{index}].label", 80)
                extra = "direction" if field == "regions" else "polarity"
                allowed = DIRECTIONS if extra == "direction" else POLARITIES
                clean[extra] = _text(item[extra], f"{field}[{index}].{extra}", 32)
                if clean[extra] not in allowed:
                    raise ValueError(f"invalid {extra}")
            elif field == "paths":
                points = item["points"]
                if not isinstance(points, list) or not 2 <= len(points) <= 32:
                    raise ValueError(f"paths[{index}] requires 2..32 points")
                geometry = _text(item["geometry"], f"{field}[{index}].geometry", 32)
                if geometry not in PATH_GEOMETRIES:
                    raise ValueError("invalid path geometry")
                clean["geometry"] = geometry
                clean["points"] = [
                    {
                        "x": _number(point.get("x") if isinstance(point, dict) else None, "point.x"),
                        "y": _number(point.get("y") if isinstance(point, dict) else None, "point.y"),
                    }
                    for point in points
                ]
                if geometry == "line" and len(points) != 2:
                    raise ValueError(f"paths[{index}] line geometry requires exactly 2 points")
                if geometry == "polyline" and len(points) < 2:
                    raise ValueError(f"paths[{index}] polyline geometry requires at least 2 points")
                if geometry == "smooth" and len(points) < 3:
                    raise ValueError(f"paths[{index}] smooth geometry requires at least 3 points")
                if geometry == "circular-arc":
                    if len(points) != 3:
                        raise ValueError(f"paths[{index}] circular-arc geometry requires exactly 3 points")
                    p0, p1, p2 = clean["points"]
                    if p0 == p1 or p1 == p2 or p0 == p2:
                        raise ValueError(f"paths[{index}] circular-arc points must be distinct")
                    cross = abs((p1["x"] - p0["x"]) * (p2["y"] - p0["y"]) - (p1["y"] - p0["y"]) * (p2["x"] - p0["x"]))
                    max_squared_edge = max(
                        (p0["x"] - p1["x"]) ** 2 + (p0["y"] - p1["y"]) ** 2,
                        (p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2,
                        (p2["x"] - p0["x"]) ** 2 + (p2["y"] - p0["y"]) ** 2,
                    )
                    if max_squared_edge <= 1e-12 or cross / max_squared_edge <= 1e-4:
                        raise ValueError(f"paths[{index}] circular-arc points are nearly collinear or degenerate")
                clean["label"] = _text(item["label"], f"{field}[{index}].label", 80)
                clean["direction"] = _text(item["direction"], f"{field}[{index}].direction", 32)
                if clean["direction"] not in DIRECTIONS:
                    raise ValueError("invalid path direction")
            else:
                clean["x"] = _number(item["x"], f"{field}[{index}].x")
                clean["y"] = _number(item["y"], f"{field}[{index}].y")
                clean["text"] = _text(item["text"], f"{field}[{index}].text", 120)
            facts = item["fact_ids"]
            if not isinstance(facts, list) or any(not isinstance(fact, str) or not fact.strip() for fact in facts):
                raise ValueError(f"{field}[{index}].fact_ids must be strings")
            clean["fact_ids"] = list(dict.fromkeys(fact.strip() for fact in facts))
            collections[field].append(clean)

    omissions: list[dict[str, str]] = []
    for index, item in enumerate(raw["omissions"]):
        if not isinstance(item, dict) or set(item) != {"fact_id", "reason"}:
            raise ValueError(f"omissions[{index}] fields mismatch")
        omissions.append({
            "fact_id": _text(item["fact_id"], f"omissions[{index}].fact_id", 48),
            "reason": _text(item["reason"], f"omissions[{index}].reason", 240),
        })
    return {**result, "panels": panels, **collections, "omissions": omissions}


def validate_canonical_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """Reject hand-edited persisted scenes that no longer match the contract."""
    if not isinstance(scene, dict) or scene.get("schema") != SCENE_SCHEMA:
        raise ValueError(f"scene schema must be {SCENE_SCHEMA}")
    normalized = normalize_scene({key: value for key, value in scene.items() if key != "schema"})
    if normalized != scene:
        raise ValueError("scene is not canonical")
    return normalized


def build_obligations(visual_facts: dict[str, Any], problem_text: str) -> dict[str, Any]:
    """Compile source semantics into a bounded view/layout contract before generation."""
    facts = normalize_visual_facts(visual_facts, visual_facts.get("source_fingerprint", ""))
    text = problem_text + " " + facts.get("reviewed_text", "") + " " + " ".join(facts.get("printed_facts", []))
    has_b = "B" in text or "磁场" in text
    has_e = "E" in text or "电场" in text
    periodic = any(token in text for token in ("周期", "交替", "每隔", "T_B", "T_E", "B(t)", "E(t)")) and (
        has_b or has_e
    )
    spatial = (
        has_b
        and has_e
        and ("同时" in text or "都" in text)
        and any(token in text for token in ("垂直纸面", "纸面向里", "纸面向外"))
    )
    particle_motion = "带电粒子" in text or ("粒子" in text and (has_b or has_e))
    views: list[dict[str, Any]] = [
        {
            "id": "motion",
            "purpose": "运动与场区物理示意",
            "required_content": [
                *(["trajectory", "field-region", "particle-or-key-point"] if particle_motion else []),
                *(["spatial-projection-label", "two-labeled-spatial-axes"] if spatial else []),
            ],
        }
    ]
    if periodic and has_b:
        views.append({
            "id": "b-time",
            "purpose": "B-t 时间图",
            "required_content": ["t-axis", "B-axis", "B(t)-field-line-wave", "phase-label"],
        })
    if periodic and has_e:
        views.append({
            "id": "e-time",
            "purpose": "E-t 时间图",
            "required_content": ["t-axis", "E-axis", "E(t)-field-line-wave", "phase-label"],
        })
    obligations: list[dict[str, str]] = []
    for view in views:
        for content in view["required_content"]:
            obligations.append({
                "id": f"{view['id']}:{content}",
                "view_id": view["id"],
                "requirement": content,
            })
    result = {
        "schema": "wuli.diagram-obligations.v1",
        "max_panels": 3,
        "layout_policy": "Use exactly these view slots; merge spatial projection into motion and never add a fourth panel.",
        "signals": {
            "particle_motion": particle_motion,
            "periodic_field": periodic,
            "spatial_projection": spatial,
            "has_b": has_b,
            "has_e": has_e,
        },
        "views": views,
        "obligations": obligations,
        "component_catalog": physics_diagram_assets.component_catalog({
            "particle_motion": particle_motion,
            "periodic_field": periodic,
            "spatial_projection": spatial,
        }),
        "responsibility_split": {
            "agent": ["teaching-intent", "component-selection", "labels", "emphasis"],
            "compiler": ["physics-model-trajectories", "event-continuity", "boundary-order", "coordinates"],
            "visual-review": ["readability", "helpfulness", "aesthetics", "overlap-suggestions"],
            "hard-gate": ["source-consistency", "topology-consistency", "model-consistency", "safety-provenance"],
        },
    }
    result["fingerprint"] = _fingerprint(result)
    return result


def _plate_label(value: Any) -> str:
    return str(value or "").replace(" ", "").replace("板", "").strip()


def _topology_errors(scene: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    statements = " ".join(str(item.get("statement", "")) for item in facts.get("diagram_facts", []))
    plates = {
        _plate_label(item.get("label")): item
        for item in scene.get("objects", [])
        if item.get("kind") == "plate" and _plate_label(item.get("label"))
    }
    match = re.search(r"上板(?:为|是)?([A-Za-z][A-Za-z0-9]*)[^\n]*?下板(?:为|是)?([A-Za-z][A-Za-z0-9]*)", statements)
    if match and match.group(1) in plates and match.group(2) in plates:
        upper, lower = plates[match.group(1)], plates[match.group(2)]
        if float(upper.get("y", 0)) >= float(lower.get("y", 0)):
            errors.append(
                f"source topology requires upper plate {match.group(1)} above lower plate {match.group(2)} in SVG coordinates"
            )
    return errors


def _model_errors(scene: dict[str, Any], physics_model: dict[str, Any] | None) -> list[str]:
    if not isinstance(physics_model, dict):
        return []
    raw_segments = physics_model.get("trajectory", {}).get("segments", [])
    segments = [item for item in raw_segments if isinstance(item, dict)] if isinstance(raw_segments, list) else []
    expected = {str(item.get("id", "")) for item in segments if str(item.get("id", "")).strip()}
    actual = {str(item.get("id", "")) for item in scene.get("paths", []) if item.get("kind") == "trajectory"}
    errors: list[str] = []
    if expected and expected != actual:
        errors.append("physics-model trajectory segments are not materialized exactly")
    timeline = physics_model.get("event_model", {}).get("timeline", [])
    event_ids = (
        {str(item.get("id", "")) for item in timeline if isinstance(item, dict)}
        if isinstance(timeline, list)
        else set()
    )
    for segment in segments:
        if segment.get("start_event") not in event_ids or segment.get("end_event") not in event_ids:
            errors.append(f"physics-model segment {segment.get('id', '')} references an unknown event")
    return errors


def semantic_gate(
    scene: dict[str, Any],
    visual_facts: dict[str, Any],
    problem_text: str,
    physics_model: dict[str, Any] | None = None,
    *,
    compilation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    error_categories: dict[str, list[str]] = {
        "source-consistency": [],
        "topology-consistency": [],
        "model-consistency": [],
        "safety-provenance": [],
    }
    if scene.get("status") != "completed":
        error_categories["source-consistency"].append("scene is unsupported")
    facts = normalize_visual_facts(visual_facts, visual_facts.get("source_fingerprint", ""))
    fact_by_id = {item["id"]: item for item in facts["diagram_facts"]}
    bound = {
        fact_id
        for field in ("regions", "objects", "paths", "annotations")
        for item in scene.get(field, [])
        for fact_id in item.get("fact_ids", [])
    }
    omitted = {item["fact_id"] for item in scene.get("omissions", [])}
    unknown = sorted((bound | omitted) - set(fact_by_id))
    if unknown:
        error_categories["source-consistency"].append("unknown visual fact IDs: " + ", ".join(unknown))
    conflicting = sorted(bound & omitted)
    if conflicting:
        error_categories["source-consistency"].append(
            "visual facts cannot be both drawn and omitted: " + ", ".join(conflicting)
        )
    required = {fact_id for fact_id, fact in fact_by_id.items() if fact["confidence"] >= 0.75}
    required_drawn = {
        fact_id for fact_id, fact in fact_by_id.items() if fact["confidence"] >= 0.75 and fact["kind"] != "label"
    }
    unaccounted = sorted(required - bound - omitted)
    if unaccounted:
        error_categories["source-consistency"].append(
            "high-confidence visual facts are neither drawn nor explicitly omitted: " + ", ".join(unaccounted)
        )
    missing_drawn = sorted(required_drawn - bound)
    if missing_drawn:
        error_categories["source-consistency"].append(
            "high-confidence physical facts are not drawn: " + ", ".join(missing_drawn)
        )
    if len(scene.get("regions", [])) + len(scene.get("objects", [])) + len(scene.get("paths", [])) < 2:
        error_categories["topology-consistency"].append("scene has too few physical primitives")
    obligations = build_obligations(visual_facts, problem_text)
    signals = obligations["signals"]
    particle_motion = signals["particle_motion"]
    if particle_motion:
        if not any(item.get("kind") == "trajectory" for item in scene.get("paths", [])):
            error_categories["topology-consistency"].append("charged-particle problem requires a trajectory")
        if not any(item.get("kind") in {"magnetic", "electric"} for item in scene.get("regions", [])):
            error_categories["topology-consistency"].append(
                "charged-particle problem requires an electric or magnetic region"
            )
        if not any(item.get("kind") in {"particle", "point"} for item in scene.get("objects", [])):
            error_categories["topology-consistency"].append("charged-particle problem requires a particle or key point")

    scene_corpus = [item.get("title", "") for item in scene.get("panels", [])]
    scene_corpus.extend(item.get("label", "") for item in scene.get("regions", []))
    scene_corpus.extend(item.get("label", "") for item in scene.get("objects", []))
    scene_corpus.extend(item.get("label", "") for item in scene.get("paths", []))
    scene_corpus.extend(item.get("text", "") for item in scene.get("annotations", []))
    scene_corpus_text = " ".join(scene_corpus)
    axis_paths = [item for item in scene.get("paths", []) if item.get("kind") == "axis"]
    field_wave_paths = [item for item in scene.get("paths", []) if item.get("kind") == "field-line"]
    axis_labels = [item.get("label", "") for item in axis_paths]
    wave_labels = [item.get("label", "") for item in field_wave_paths]
    path_labels = [item.get("label", "") for item in scene.get("paths", [])]
    phase_corpus = " ".join(path_labels + [item.get("text", "") for item in scene.get("annotations", [])])

    periodic_trigger = signals["periodic_field"]
    has_b_problem = signals["has_b"]
    has_e_problem = signals["has_e"]
    spatial_trigger = signals["spatial_projection"]
    axis_count = len(axis_paths)
    field_wave_count = len(field_wave_paths)

    if periodic_trigger:
        if axis_count < 2:
            warnings.append("periodic field problem benefits from at least two axis paths")
        required_wave_count = 2 if has_b_problem and has_e_problem else 1
        if field_wave_count < required_wave_count:
            warnings.append(
                f"periodic field problem benefits from at least {required_wave_count} field-line wave path(s)"
            )
        if has_b_problem and not any("B" in label or "磁场" in label for label in wave_labels):
            warnings.append("periodic field problem with B should show a B/磁场 wave label")
        if has_e_problem and not any("E" in label or "电场" in label for label in wave_labels):
            warnings.append("periodic field problem with E should show an E/电场 wave label")
        if not any(token in phase_corpus for token in ("T_B", "T_E", "半周期", "阶段", "t=", "t＝")):
            warnings.append("periodic field problem should show a phase annotation or path label")

    if spatial_trigger:
        spatial_axes = sum(1 for label in axis_labels if any(token in label for token in ("x", "y", "z", "轴")))
        if spatial_axes < 2:
            warnings.append("spatial projection benefits from at least two axis labels containing x/y/z/轴")
        if not any(token in scene_corpus_text for token in ("投影", "空间", "三维", "立体")):
            warnings.append("spatial projection should say 投影/空间/三维/立体 in scene corpus")
    error_categories["topology-consistency"].extend(_topology_errors(scene, facts))
    error_categories["model-consistency"].extend(_model_errors(scene, physics_model))
    errors = [message for category in error_categories.values() for message in category]
    result = {
        "schema": GATE_SCHEMA,
        "teaching_signals": {
            "periodic_field": periodic_trigger,
            "spatial_projection": spatial_trigger,
            "axis_count": axis_count,
            "field_wave_count": field_wave_count,
        },
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "warnings": warnings,
        "soft_revision": {
            "mode": "prompt-only",
            "blocking": False,
            "max_rounds": 1,
            "visual_review_status": "not-run",
            "quality_approved": False,
            "reviewer": "mimo-visual-advisor-or-teacher",
            "executor": "deepseek-flash-bounded-patch",
            "immutable": ["physics-model.json", "visual-facts.json", "compiled-trajectory-points"],
            "prompts": warnings,
        },
        "hard_gate_categories": error_categories,
        "bound_fact_ids": sorted(bound),
        "omitted_fact_ids": sorted(omitted),
        "required_fact_ids": sorted(required),
        "required_drawn_fact_ids": sorted(required_drawn),
        "visual_facts_fingerprint": facts["fingerprint"],
        "scene_fingerprint": _fingerprint(scene),
        "compilation": compilation or {"status": "not-applicable", "model_fingerprint": ""},
    }
    result["diagnostics"] = _semantic_diagnostics(result)
    return result


def _fingerprint(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _circular_arc_path(points: list[tuple[float, float]]) -> str:
    """Return a deterministic SVG circular arc through start, middle, and end."""
    if len(points) != 3:
        raise ValueError("circular arc requires exactly three points")
    (x1, y1), (x2, y2), (x3, y3) = points
    if (x1, y1) == (x2, y2) or (x2, y2) == (x3, y3) or (x1, y1) == (x3, y3):
        raise ValueError("circular arc points must be distinct")
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:
        raise ValueError("circular arc points are collinear or degenerate")
    ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / d
    uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / d
    radius = math.hypot(x1 - ux, y1 - uy)
    if not math.isfinite(ux) or not math.isfinite(uy) or not math.isfinite(radius) or radius <= 0:
        raise ValueError("circular arc has invalid geometry")
    start_angle = math.atan2(y1 - uy, x1 - ux) % math.tau
    mid_angle = math.atan2(y2 - uy, x2 - ux) % math.tau
    end_angle = math.atan2(y3 - uy, x3 - ux) % math.tau
    delta = (end_angle - start_angle) % math.tau
    mid_delta = (mid_angle - start_angle) % math.tau
    if mid_delta < delta:
        sweep = 1
        large_arc = 1 if delta > math.pi else 0
    else:
        sweep = 0
        large_arc = 1 if (math.tau - delta) > math.pi else 0
    return f"M {x1:.1f} {y1:.1f} A {radius:.1f} {radius:.1f} 0 {large_arc} {sweep} {x3:.1f} {y3:.1f}"


def _estimated_text_width(text: str, font_size: float = 13.0) -> float:
    """Estimate text width deterministically without font metrics."""
    return max(8.0, sum(0.56 * font_size if ord(char) < 128 else font_size for char in text))


def _place_label(
    text: str,
    desired_x: float,
    desired_y: float,
    occupied: list[tuple[float, float, float, float]],
    width: float,
    height: float,
    *,
    anchor: str = "start",
) -> tuple[float, float]:
    """Place a label deterministically, preferring non-overlapping candidates."""
    if anchor not in {"start", "middle"}:
        raise ValueError(f"unsupported anchor: {anchor}")
    text_width = _estimated_text_width(text)
    box_height = 16.0
    margin = 4.0
    padding = 4.0
    candidates = (
        (0, 0),
        (0, -20),
        (0, 20),
        (16, -12),
        (-16, -12),
        (16, 14),
        (-16, 14),
        (28, 0),
        (-28, 0),
    )

    def make_candidate(dx: float, dy: float) -> tuple[float, float, tuple[float, float, float, float]]:
        candidate_x = desired_x + dx
        candidate_y = desired_y + dy
        left = candidate_x if anchor == "start" else candidate_x - text_width / 2
        top = candidate_y - 13
        left = max(margin, min(left, width - text_width - margin))
        top = max(margin, min(top, height - box_height - margin))
        bbox = (left, top, left + text_width, top + box_height)
        final_x = left if anchor == "start" else left + text_width / 2
        return final_x, top + 13, bbox

    def padded_overlap(box: tuple[float, float, float, float], other: tuple[float, float, float, float]) -> float:
        overlap_width = max(0.0, min(box[2] + padding, other[2]) - max(box[0] - padding, other[0]))
        overlap_height = max(0.0, min(box[3] + padding, other[3]) - max(box[1] - padding, other[1]))
        return overlap_width * overlap_height

    best: tuple[float, float, tuple[float, float, float, float]] | None = None
    best_overlap = float("inf")
    for dx, dy in candidates:
        candidate = make_candidate(dx, dy)
        total_overlap = sum(padded_overlap(candidate[2], other) for other in occupied)
        if total_overlap == 0:
            occupied.append(candidate[2])
            return candidate[0], candidate[1]
        if total_overlap < best_overlap:
            best = candidate
            best_overlap = total_overlap
    if best is None:
        raise RuntimeError("label candidate set is empty")
    occupied.append(best[2])
    return best[0], best[1]


def render_svg(scene: dict[str, Any]) -> str:
    """Render a normalized, gate-passed scene using an offline-safe SVG subset."""
    width, height = 960, 560
    count = len(scene["panels"])
    gap = 2.0
    margin = 2.5
    available_width = 100 - 2 * margin - gap * (count - 1)
    weights = [2.0 if count == 3 and item["id"] == "motion" else 1.0 for item in scene["panels"]]
    total_weight = sum(weights)
    panels: dict[str, dict[str, Any]] = {}
    cursor = margin
    for item, weight in zip(scene["panels"], weights):
        panel_width = available_width * weight / total_weight
        panels[item["id"]] = {
            **item,
            "x": cursor,
            "y": 9.0,
            "width": panel_width,
            "height": 88.0,
        }
        cursor += panel_width + gap

    def panel_xy(panel_id: str, x: float, y: float) -> tuple[float, float]:
        panel = panels[panel_id]
        local_x = 2 + 0.96 * x
        local_y = 8 + 0.90 * y
        return (panel["x"] + panel["width"] * local_x / 100) * width / 100, (
            panel["y"] + panel["height"] * local_y / 100
        ) * height / 100

    def panel_size(panel_id: str, w: float, h: float) -> tuple[float, float]:
        panel = panels[panel_id]
        return panel["width"] * 0.96 * w * width / 10000, panel["height"] * 0.90 * h * height / 10000

    occupied_labels: list[tuple[float, float, float, float]] = []
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="560" viewBox="0 0 960 560" role="img">',
        f"<title>{html.escape(scene['title'])}</title>",
        '<defs><marker id="physics-arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#334155"/></marker><marker id="trajectory-arrow-p1" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#dc2626"/></marker><marker id="trajectory-arrow-p2" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#7c3aed"/></marker><pattern id="field-cross" width="30" height="30" patternUnits="userSpaceOnUse"><path d="M10,10 L20,20 M20,10 L10,20" stroke="#64748b" stroke-width="1.2"/></pattern><pattern id="field-dot" width="30" height="30" patternUnits="userSpaceOnUse"><circle cx="15" cy="15" r="1.8" fill="#64748b"/></pattern></defs>',
        '<rect width="960" height="560" fill="#ffffff"/>',
        f'<text x="480" y="28" text-anchor="middle" font-family="sans-serif" font-size="20" font-weight="700" fill="#0f172a">{html.escape(scene["title"])}</text>',
    ]
    for raw_panel in scene["panels"]:
        panel = panels[raw_panel["id"]]
        x, y = panel["x"] * width / 100, panel["y"] * height / 100
        w, h = panel["width"] * width / 100, panel["height"] * height / 100
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="10" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5"/>'
        )
        if panel["title"]:
            parts.append(
                f'<text x="{x + 12:.1f}" y="{y + 22:.1f}" font-family="sans-serif" font-size="15" font-weight="700" fill="#334155">{html.escape(panel["title"])}</text>'
            )
    for item in scene["regions"]:
        x, y = panel_xy(item["panel_id"], item["x"], item["y"])
        w, h = panel_size(item["panel_id"], item["width"], item["height"])
        if item["kind"] == "magnetic":
            fill = (
                "url(#field-cross)"
                if item["direction"] == "into-page"
                else "url(#field-dot)"
                if item["direction"] == "out-of-page"
                else "#dbeafe"
            )
            stroke = "#2563eb"
        elif item["kind"] == "electric":
            fill, stroke = "#fef3c7", "#d97706"
        else:
            fill, stroke = "#f1f5f9", "#64748b"
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" fill-opacity="0.72" stroke="{stroke}" stroke-width="1.5" stroke-dasharray="6 4"/>'
        )
        if item["label"]:
            label_x, label_y = _place_label(item["label"], x + 7, y + 18, occupied_labels, width, height)
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y:.1f}" font-family="sans-serif" font-size="13" fill="#334155">{html.escape(item["label"])}</text>'
            )
    for item in scene["paths"]:
        points = [panel_xy(item["panel_id"], point["x"], point["y"]) for point in item["points"]]
        path_id = item["id"].lower()
        if item["kind"] == "trajectory" and (path_id.startswith("p2-") or "part2" in path_id):
            color, marker_id = "#7c3aed", "trajectory-arrow-p2"
        elif item["kind"] == "trajectory":
            color, marker_id = "#dc2626", "trajectory-arrow-p1"
        else:
            color, marker_id = "#334155", "physics-arrow"
        marker = f' marker-end="url(#{marker_id})"' if item["direction"] != "none" else ""
        dash = ' stroke-dasharray="5 4"' if item["kind"] in {"dimension", "axis"} else ""
        stroke_width = 3 if item["kind"] == "trajectory" else 1.8
        geometry = item["geometry"]
        if geometry == "line":
            x1, y1 = points[0]
            x2, y2 = points[1]
            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"{marker}{dash}/>'
            )
        elif geometry == "polyline":
            serialized = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            parts.append(
                f'<polyline points="{serialized}" fill="none" stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"{marker}{dash}/>'
            )
        elif geometry == "smooth":
            commands = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
            for index in range(1, len(points) - 1):
                control = points[index]
                following = points[index + 1]
                midpoint = ((control[0] + following[0]) / 2, (control[1] + following[1]) / 2)
                commands.append(f"Q {control[0]:.1f} {control[1]:.1f} {midpoint[0]:.1f} {midpoint[1]:.1f}")
            commands.append(f"T {points[-1][0]:.1f} {points[-1][1]:.1f}")
            parts.append(
                f'<path d="{" ".join(commands)}" fill="none" stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"{marker}{dash}/>'
            )
        elif geometry == "circular-arc":
            d = _circular_arc_path(points)
            parts.append(
                f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round"{marker}{dash}/>'
            )
        else:
            raise ValueError(f"unsupported path geometry: {geometry}")
        if item["label"]:
            x, y = points[len(points) // 2]
            label_x, label_y = _place_label(item["label"], x + 6, y - 7, occupied_labels, width, height)
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y:.1f}" font-family="sans-serif" font-size="13" fill="{color}">{html.escape(item["label"])}</text>'
            )
    for item in scene["objects"]:
        x, y = panel_xy(item["panel_id"], item["x"], item["y"])
        w, h = panel_size(item["panel_id"], item["width"], item["height"])
        cx, cy = x + w / 2, y + h / 2
        if item["kind"] in {"particle", "point"}:
            is_model_aid = item["id"].startswith(("model-center-", "model-event-"))
            radius = max(2.7, min(w, h) / 2) if is_model_aid else max(4, min(w, h) / 2)
            stroke = (
                "#7c3aed"
                if item["id"].startswith("model-event-p2-")
                else "#dc2626"
                if item["id"].startswith("model-event-p1-")
                else "#0f172a"
            )
            parts.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}" fill="#ffffff" stroke="{stroke}" stroke-width="2"/>'
            )
        elif item["kind"] == "plate":
            parts.append(
                f'<line x1="{x:.1f}" y1="{cy:.1f}" x2="{x + w:.1f}" y2="{cy:.1f}" stroke="#0f172a" stroke-width="4"/>'
            )
        elif item["kind"] == "boundary":
            parts.append(
                f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + w:.1f}" y2="{y + h:.1f}" stroke="#475569" stroke-width="2.5"/>'
            )
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="4" fill="#ffffff" stroke="#0f172a" stroke-width="2"/>'
            )
        has_sign = any(sign in item["label"] for sign in ("+", "−", "-", "正", "负"))
        polarity = (
            "" if has_sign else "+" if item["polarity"] == "positive" else "−" if item["polarity"] == "negative" else ""
        )
        label = " ".join(part for part in (item["label"], polarity) if part)
        if label:
            label_x, label_y = _place_label(
                label, cx, cy - max(7, h / 2 + 5), occupied_labels, width, height, anchor="middle"
            )
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="middle" font-family="sans-serif" font-size="13" font-weight="700" fill="#0f172a">{html.escape(label)}</text>'
            )
    for item in scene["annotations"]:
        x, y = panel_xy(item["panel_id"], item["x"], item["y"])
        label_x, label_y = _place_label(item["text"], x, y, occupied_labels, width, height)
        parts.append(
            f'<text x="{label_x:.1f}" y="{label_y:.1f}" font-family="sans-serif" font-size="13" fill="#334155">{html.escape(item["text"])}</text>'
        )
    parts.append("</svg>")
    svg = "\n".join(parts) + "\n"
    svg_collaboration.validate_svg_safety(svg)
    return svg


def _write_rejection(
    staging: Path,
    payload: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    # Never let an older accepted rendering make a new rejected candidate look
    # successful inside the isolated workspace.
    for relative in (SCENE_PATH, GATE_PATH, SVG_PATH, PROVENANCE_PATH):
        (staging / relative).unlink(missing_ok=True)
    report = {
        "schema": "wuli.physics-diagram-diagnostics.v1",
        "status": "failed",
        "candidate_fingerprint": _fingerprint(payload),
        "diagnostics": diagnostics,
        "repairable": bool(diagnostics)
        and all(item.get("code") in REPAIRABLE_DIAGNOSTIC_CODES for item in diagnostics),
    }
    artifacts = {
        REJECTED_SCENE_PATH: json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        DIAGNOSTICS_PATH: json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    for relative, content in artifacts.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {
        "contract": SCENE_SCHEMA,
        "status": "rejected",
        "rejected_candidate": payload,
        "diagnostic_report": report,
        "stages": [{"name": "physics-diagram-preflight", "status": "failed"}],
    }


def _pointer_parts(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("patch path must be an absolute JSON Pointer")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    if not parts or parts[0] not in {
        "title",
        "message",
        "panels",
        "regions",
        "objects",
        "paths",
        "annotations",
        "omissions",
    }:
        raise ValueError(f"patch path is outside the scene repair boundary: {pointer}")
    return parts


def apply_scene_patches(base_payload: dict[str, Any], patch_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch_payload, dict) or set(patch_payload) - TRANSPORT_METADATA_FIELDS != {
        "status",
        "message",
        "patches",
    }:
        raise ValueError("scene patch fields mismatch")
    if patch_payload.get("status") != "completed":
        raise ValueError(f"provider reported unsupported repair: {patch_payload.get('message', '')}")
    patches = patch_payload.get("patches")
    if not isinstance(patches, list) or not 1 <= len(patches) <= 24:
        raise ValueError("scene repair requires 1..24 patches")
    result = copy.deepcopy(base_payload)
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict) or set(patch) != {"op", "path", "value"}:
            raise ValueError(f"patches[{index}] fields mismatch")
        op = patch["op"]
        if op not in {"add", "remove", "replace"}:
            raise ValueError(f"patches[{index}].op is invalid")
        parts = _pointer_parts(patch["path"])
        parent: Any = result
        for part in parts[:-1]:
            if isinstance(parent, list):
                if not part.isdigit() or int(part) >= len(parent):
                    raise ValueError(f"patches[{index}] path does not exist")
                parent = parent[int(part)]
            elif isinstance(parent, dict) and part in parent:
                parent = parent[part]
            else:
                raise ValueError(f"patches[{index}] path does not exist")
        leaf = parts[-1]
        if isinstance(parent, list):
            if op == "add" and leaf == "-":
                parent.append(copy.deepcopy(patch["value"]))
            elif leaf.isdigit() and int(leaf) < len(parent):
                if op == "remove":
                    parent.pop(int(leaf))
                else:
                    parent[int(leaf)] = copy.deepcopy(patch["value"])
            else:
                raise ValueError(f"patches[{index}] list index is invalid")
        elif isinstance(parent, dict):
            if op in {"remove", "replace"} and leaf not in parent:
                raise ValueError(f"patches[{index}] path does not exist")
            if op == "remove":
                del parent[leaf]
            else:
                parent[leaf] = copy.deepcopy(patch["value"])
        else:
            raise ValueError(f"patches[{index}] parent is not a container")
    if _fingerprint(result) == _fingerprint(base_payload):
        raise ValueError("scene repair made no progress")
    return result


def repair_context(gateway_result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    attempts = gateway_result.get("attempts")
    if not isinstance(attempts, list):
        return None
    for attempt in reversed(attempts):
        materialization = attempt.get("materialization") if isinstance(attempt, dict) else None
        if not isinstance(materialization, dict) or materialization.get("status") != "rejected":
            continue
        candidate = materialization.get("rejected_candidate")
        report = materialization.get("diagnostic_report")
        if isinstance(candidate, dict) and isinstance(report, dict) and report.get("repairable") is True:
            return candidate, report
    return None


def materialize(staging: Path, payload: dict[str, Any], *, model_config: dict[str, Any]) -> dict[str, Any]:
    candidate = {key: value for key, value in payload.items() if key not in TRANSPORT_METADATA_FIELDS}
    facts_path = staging / "visual-facts.json"
    if not facts_path.is_file():
        raise ValueError("visual-facts.json is required for a physics diagram")
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    problem = (staging / "problem.md").read_text(encoding="utf-8")
    model_path = staging / "physics-model.json"
    physics_model = json.loads(model_path.read_text(encoding="utf-8")) if model_path.is_file() else None
    preflight = _aggregate_preflight_diagnostics(candidate)
    if preflight:
        return _write_rejection(staging, candidate, _deduplicate_diagnostics(preflight))
    try:
        scene = normalize_scene(payload)
    except ValueError as exc:
        return _write_rejection(staging, candidate, [_normalization_diagnostic(exc, candidate)])
    if scene["status"] != "completed":
        return _write_rejection(
            staging, candidate, [_diagnostic("scene.unsupported", f"provider reported unsupported: {scene['message']}")]
        )
    scene, compilation = physics_diagram_assets.compile_model_scene(scene, physics_model)
    try:
        scene = normalize_scene({key: value for key, value in scene.items() if key != "schema"})
    except ValueError as exc:
        return _write_rejection(
            staging, candidate, [_diagnostic("scene.model-compilation", str(exc), auto_repairable=False)]
        )
    gate = semantic_gate(
        scene,
        facts,
        problem,
        physics_model,
        compilation=compilation,
    )
    if gate["status"] != "passed":
        return _write_rejection(staging, candidate, gate["diagnostics"])
    svg = render_svg(scene)
    identity = {
        "model_id": str(model_config.get("id", "")).strip(),
        "provider": str(model_config.get("provider", "")).strip(),
    }
    provenance = svg_collaboration.validate_and_bind_svg(
        svg, facts, {**identity, "generation_fingerprint": gate["scene_fingerprint"]}, identity
    )
    artifacts = {
        SCENE_PATH: json.dumps(scene, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        GATE_PATH: json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        SVG_PATH: svg,
        PROVENANCE_PATH: json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    for relative, content in artifacts.items():
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return {
        "contract": SCENE_SCHEMA,
        "status": "completed",
        "payload_digest": gate["scene_fingerprint"],
        "gate": gate,
        "stages": [
            {"name": "physics-diagram-semantic-gate", "status": "completed"},
            {"name": "physics-svg-materialization", "status": "completed"},
        ],
    }


def materialize_patch(
    staging: Path,
    patch_payload: dict[str, Any],
    *,
    base_payload: dict[str, Any],
    model_config: dict[str, Any],
) -> dict[str, Any]:
    try:
        patched = apply_scene_patches(base_payload, patch_payload)
    except ValueError as exc:
        return _write_rejection(
            staging, base_payload, [_diagnostic("scene.patch-invalid", str(exc), auto_repairable=False)]
        )
    result = materialize(staging, patched, model_config=model_config)
    result["repair"] = {
        "mode": "bounded-json-patch",
        "patch_count": len(patch_payload.get("patches", [])),
        "base_fingerprint": _fingerprint(base_payload),
        "result_fingerprint": _fingerprint(patched),
    }
    return result
