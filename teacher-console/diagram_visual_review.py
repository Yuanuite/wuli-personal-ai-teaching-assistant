"""MiMo 静态图软评审模块（契约 wuli.diagram-visual-review.v1）。

对安全渲染出的静态物理图做只读软评审：只评价可读性、遮挡、层次与
辅助性（标签避碰、箭头清晰度、图例），不输出物理结论、不改写 visual
facts、不评价/批准答案质量、不修改图片或 SVG。模型只输出
``{"suggestions": [...]}``，本模块从中选出建议并与可信运行时身份
（config 中的 model id / upstream model）组装为最终契约。

失败关闭与 ``run_vision_probe`` 一致，并复用其上游请求构造：
- 环境/配置级关闭（provider 非 openai-compatible、缺失 vision trait、
  remote 未授权、缺少 model/base_url 字段）→ ``status=blocked``；
- 操作级失败（图片缺失、HTTP 404 / 网络错误、非法 JSON 或建议形状
  不合法）→ ``status=failed``；
- 模型正常返回合法 suggestions（可为空数组）→ ``status=passed``。

隐私：本模块不写任何日志；输出只包含建议与身份/时间字段，绝不包含
api_key 或 base64 data URL。
"""

import json
import os
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request

from visual_extraction import (
    VisualExtractionError,
    _build_upstream_request,
    _extract_json_content,
    _load_image_data_url,
    _resolve_api_key,
)

DIAGRAM_VISUAL_REVIEW_SCHEMA = "wuli.diagram-visual-review.v1"
ALLOWED_SEVERITIES = ("suggestion", "warning")

REVIEW_PROMPT = (
    "你是静态物理图的可读性评审员。请只评价以下四个方面，不要涉及任何其他内容：\n"
    "1. 可读性：文字、符号、数字与曲线是否清晰易读；\n"
    "2. 遮挡：元素之间是否相互遮挡、重叠到难以辨认；\n"
    "3. 层次：主次关系与层级组织是否清楚；\n"
    "4. 辅助性：标签是否避碰、箭头是否清晰、图例是否完整可用。\n"
    "禁止输出物理结论，禁止改写图中已有的视觉事实，禁止评价或批准答案质量，"
    "禁止修改图片或 SVG。\n"
    '请只输出一个 JSON 对象：{"suggestions": [{"code": "建议代码", '
    '"severity": "suggestion" 或 "warning", "message": "中文建议"}]}。'
    "没有问题时 suggestions 为空数组。"
)


def _review_result(status: str, model_id: str, upstream_model: str, reason: str, suggestions: list) -> dict:
    return {
        "schema": DIAGRAM_VISUAL_REVIEW_SCHEMA,
        "status": status,
        "suggestions": suggestions,
        "model_id": model_id,
        "upstream_model": upstream_model,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason": reason,
    }


def _review_blocked(model_id: str, upstream_model: str, reason: str) -> dict:
    return _review_result("blocked", model_id, upstream_model, reason, [])


def _review_failed(model_id: str, upstream_model: str, reason: str) -> dict:
    return _review_result("failed", model_id, upstream_model, reason, [])


def _validate_suggestions(parsed: dict) -> list:
    """校验模型输出的 suggestions 数组并返回契约形状的副本。

    只接受 ``{"suggestions": [...]}`` 结构：每项必须是带非空
    ``code``、合法 ``severity``（suggestion|warning）与非空 ``message``
    的对象。任何越界都按失败关闭处理（抛 VisualExtractionError）。
    """
    if not isinstance(parsed, dict):
        raise VisualExtractionError("review response is not a JSON object")
    suggestions = parsed.get("suggestions")
    if not isinstance(suggestions, list):
        raise VisualExtractionError("review response is missing suggestions list")
    cleaned = []
    for item in suggestions:
        if not isinstance(item, dict):
            raise VisualExtractionError("suggestion item is not an object")
        code = item.get("code")
        severity = item.get("severity")
        message = item.get("message")
        if not isinstance(code, str) or not code.strip():
            raise VisualExtractionError("suggestion item is missing code")
        if severity not in ALLOWED_SEVERITIES:
            raise VisualExtractionError("suggestion item has invalid severity")
        if not isinstance(message, str) or not message.strip():
            raise VisualExtractionError("suggestion item is missing message")
        cleaned.append({"code": code, "severity": severity, "message": message})
    return cleaned


def run_diagram_visual_review(
    config: dict,
    image_path: str,
    *,
    urlopen: callable,
    allow_remote: bool,
) -> dict:
    """对静态物理图执行一次 MiMo 软评审，返回 wuli.diagram-visual-review.v1。

    与生产视觉链路使用同一 endpoint、图片消息格式与 JSON 响应契约
    （复用 visual_extraction 的辅助函数），因此模型能力或 endpoint 的
    任何损坏都会在此处如实暴露为 failed，而不是静默放行。
    """
    model_id = str(config.get("id", "")).strip()
    upstream_model = str(config.get("model", "")).strip()
    if config.get("provider") != "openai-compatible":
        return _review_blocked(model_id, upstream_model, "provider is not openai-compatible")
    traits = config.get("traits") or {}
    if not traits.get("vision"):
        return _review_blocked(model_id, upstream_model, "model does not declare vision trait")
    if config.get("remote") and not allow_remote:
        return _review_blocked(model_id, upstream_model, "remote diagram review is disabled")
    for field in ("model", "base_url"):
        if not str(config.get(field, "")).strip():
            return _review_blocked(model_id, upstream_model, f"model config is missing {field}")

    image = str(image_path or "").strip()
    if not image or not os.path.isfile(image):
        return _review_failed(model_id, upstream_model, "review image is missing")

    try:
        image_data_urls = [_load_image_data_url(image)]
        body = _build_upstream_request(config, REVIEW_PROMPT, image_data_urls, response_format_supported=True)
        api_key = _resolve_api_key(config)
    except VisualExtractionError as exc:
        return _review_failed(model_id, upstream_model, str(exc))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base_url = str(config.get("base_url", "")).rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")

    try:
        with urlopen(request, timeout=float(config.get("timeout_seconds") or 30)) as resp:
            response_body = resp.read().decode("utf-8")
    except HTTPError as exc:
        return _review_failed(model_id, upstream_model, f"endpoint returned HTTP {exc.code}")
    except (URLError, OSError) as exc:
        return _review_failed(model_id, upstream_model, f"network error: {exc}")

    try:
        response_json = json.loads(response_body)
        choices = response_json.get("choices") if isinstance(response_json, dict) else None
        content = (
            choices[0].get("message", {}).get("content", "")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else ""
        )
        parsed = _extract_json_content(content)
        suggestions = _validate_suggestions(parsed)
    except (json.JSONDecodeError, VisualExtractionError, KeyError, IndexError, TypeError):
        return _review_failed(model_id, upstream_model, "malformed or unexpected review response")

    return _review_result("passed", model_id, upstream_model, "diagram readability review passed", suggestions)
