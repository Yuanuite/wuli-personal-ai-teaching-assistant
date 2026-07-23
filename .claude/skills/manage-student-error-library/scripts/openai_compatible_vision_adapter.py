#!/usr/bin/env python3
"""Visual-review adapter for a local or explicitly authorized OpenAI-compatible endpoint."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def endpoint_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("VISUAL_REVIEW_BASE_URL must be an http(s) URL")
    if parsed.hostname not in LOOPBACK_HOSTS and os.environ.get("VISUAL_REVIEW_ALLOW_REMOTE") != "true":
        raise PermissionError("non-loopback visual endpoint requires VISUAL_REVIEW_ALLOW_REMOTE=true")
    return f"{base_url.rstrip('/')}/chat/completions"


def prompt(payload: dict) -> str:
    return (
        "你是高中物理题目视觉复核器，只负责忠实读图，不解题。"
        "逐字核对题干、所有小问、公式层级、正负号、上下标、单位，以及图中的区域、箭头、方向、电性和边界。"
        "把手写解答与印刷题干分开，不把学生作答当成题目条件。"
        "只能输出一个 JSON 对象，字段为 review_status、engine、reviewer、reviewed_text、diagram_facts、uncertainties、notes。"
        "完全确定且题干完整时 review_status=passed、uncertainties=[]；任何无法辨认处都必须用 needs-review 并逐项列出。\n\n"
        f"OCR 草稿（仅供比对，不是事实）：\n{payload.get('ocr', {}).get('text', '')}"
    )


def request_body(payload: dict, model: str) -> dict:
    content: list[dict] = [{"type": "text", "text": prompt(payload)}]
    for raw_path in payload.get("images", []):
        path = Path(raw_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        content.append({"type": "image_url", "image_url": {"url": image_data_url(path)}})
    if len(content) == 1:
        raise ValueError("visual review payload contains no images")
    return {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": content}],
    }


def extract_json(response: dict) -> dict:
    choices = response.get("choices", [])
    if not choices:
        raise ValueError("endpoint response contains no choices")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str):
        raise ValueError("endpoint response content is not text")
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    result = json.loads(stripped)
    if not isinstance(result, dict):
        raise ValueError("visual model must return one JSON object")
    result.setdefault("engine", os.environ.get("VISUAL_REVIEW_MODEL", "openai-compatible-vision"))
    result.setdefault("reviewer", "visual-sidecar")
    result.setdefault("diagram_facts", [])
    result.setdefault("uncertainties", [])
    result.setdefault("notes", "")
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        base_url = os.environ["VISUAL_REVIEW_BASE_URL"]
        model = os.environ["VISUAL_REVIEW_MODEL"]
        body = json.dumps(request_body(payload, model), ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("VISUAL_REVIEW_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(endpoint_url(base_url), data=body, headers=headers, method="POST")
        timeout = float(os.environ.get("VISUAL_REVIEW_TIMEOUT_SECONDS", "120"))
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is explicitly gated
            result = extract_json(json.loads(response.read().decode("utf-8")))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (KeyError, ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"visual review adapter error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
