import base64
import json
import mimetypes
import os
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from visual_facts import evaluate_gate, normalize_payload


class VisualExtractionError(Exception):
    """Raised when visual extraction cannot be completed safely."""


def _safe_message(message: str) -> str:
    """Return a message that never contains secrets."""
    return message


def _load_image_data_url(image_path: str) -> str:
    """Read a regular file and return a base64 data URL."""
    if not os.path.isfile(image_path):
        raise VisualExtractionError(f"Image file not found or not a regular file: {image_path}")
    try:
        with open(image_path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise VisualExtractionError(f"Could not read image file: {image_path}") from e
    encoded = base64.b64encode(data).decode("ascii")
    mime = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
    return f"data:{mime};base64,{encoded}"


def _extract_json_content(content: str) -> dict:
    """Parse model output: bare JSON or a single JSON markdown fence."""
    if not isinstance(content, str):
        raise VisualExtractionError("Model output content is not a string")
    stripped = content.strip()
    if not stripped:
        raise VisualExtractionError("Model output is empty")
    # Try bare JSON first
    try:
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise VisualExtractionError("Model output is not a JSON object")
        return parsed
    except json.JSONDecodeError:
        pass
    # Single optional JSON fence: full-string match
    fence_pattern = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)
    m = fence_pattern.match(stripped)
    if not m:
        raise VisualExtractionError("Model output is not a JSON object or a single JSON fence")
    try:
        parsed = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise VisualExtractionError("Model output contains invalid JSON") from e
    if not isinstance(parsed, dict):
        raise VisualExtractionError("Model output is not a JSON object")
    return parsed


def _build_upstream_request(config: dict, prompt: str, image_data_urls: list, response_format_supported: bool) -> dict:
    """Build the request body for the OpenAI-compatible endpoint."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                *[{"type": "image_url", "image_url": {"url": url}} for url in image_data_urls],
            ],
        }
    ]
    body: dict = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0,
    }
    if response_format_supported:
        body["response_format"] = {"type": "json_object"}
    return body


def _resolve_api_key(config: dict) -> str:
    """Resolve API key from config api_key or api_key_env."""
    direct = str(config.get("api_key", "")).strip()
    if direct:
        return direct
    env_name = config.get("api_key_env")
    if env_name:
        key = os.environ.get(env_name)
        if key:
            return key
    raise VisualExtractionError("No API key available")


def extract_visual_facts(
    review_payload: dict,
    expected_source_fingerprint: str,
    *,
    model_resolver: Callable[..., Any],
    expected_runtime_identity: dict,
    allow_remote: bool,
    urlopen: Callable[..., Any],
    routing_tier: str = "auto",
) -> dict:
    """Extract visual facts through the registry-routed model."""
    # Validate fingerprint format early
    if not (
        isinstance(expected_source_fingerprint, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", expected_source_fingerprint)
    ):
        raise VisualExtractionError("Invalid expected_source_fingerprint format")

    # Resolve model
    try:
        config = model_resolver("vision", routing_tier=routing_tier)
    except Exception as e:
        raise VisualExtractionError("Model resolution failed") from e
    if not isinstance(config, dict):
        raise VisualExtractionError("Model resolver returned invalid config")

    # Validate provider and traits
    if config.get("provider") != "openai-compatible":
        raise VisualExtractionError("Model provider is not openai-compatible")
    traits = config.get("traits") or {}
    if not traits.get("vision"):
        raise VisualExtractionError("Model does not support vision")
    for field in ("id", "model", "base_url"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise VisualExtractionError(f"Model config is missing {field}")

    # Remote privacy check before reading images or calling URL
    if config.get("remote") and not allow_remote:
        raise VisualExtractionError("Remote model not allowed")

    # Build actual runtime identity from config
    actual_runtime_identity = {
        "model_id": config["id"],
        "provider": config["provider"],
    }

    # Extract input fields
    images = review_payload.get("images") or []
    if not isinstance(images, list) or not images:
        raise VisualExtractionError("At least one source image is required")
    ocr_text = (review_payload.get("ocr") or {}).get("text", "")
    subject = review_payload.get("subject", "")
    source_sha256 = review_payload.get("source_sha256", "")
    required_checks = review_payload.get("required_checks", [])

    # Read images as data URLs (after privacy check)
    image_data_urls = []
    for img in images:
        if not isinstance(img, str):
            raise VisualExtractionError("Image path must be a string")
        image_data_urls.append(_load_image_data_url(img))

    # Build prompt
    prompt = (
        "You are a visual fact extractor. Read the provided image(s) faithfully. "
        "Do not solve or interpret beyond what is visible. "
        "Return a single JSON object with exactly these fields: "
        "reviewed_text (string), printed_facts (list of strings), "
        "diagram_facts (list of objects with id, kind, statement, confidence), "
        "handwriting (list of strings), uncertainties (list of strings). "
        "Every diagram_facts.id must be a unique non-empty string. "
        "Every diagram_facts.kind must be exactly one of: region, object, arrow, label, boundary, geometry. "
        "Never invent another kind or add another field. "
        "Every confidence must be a JSON number from 0 through 1. "
        f"Subject: {subject}\n"
        f"OCR text: {ocr_text}\n"
        f"Source SHA256: {source_sha256}\n"
        f"Required checks: {json.dumps(required_checks)}\n"
    )

    # Build request body
    response_format_supported = True  # assume supported; can be made configurable
    body = _build_upstream_request(config, prompt, image_data_urls, response_format_supported)

    # Resolve API key and build headers
    api_key = _resolve_api_key(config)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Build URL: base_url + /chat/completions exactly once
    base_url = config.get("base_url", "")
    if not base_url:
        raise VisualExtractionError("Missing base_url in config")
    base_url = base_url.rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"

    # Prepare request
    req = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")

    # Call endpoint
    try:
        with urlopen(req, timeout=float(config.get("timeout_seconds") or 30)) as resp:
            response_body = resp.read().decode("utf-8")
    except HTTPError as e:
        raise VisualExtractionError("Upstream request failed") from e
    except URLError as e:
        raise VisualExtractionError("Upstream request failed") from e
    except Exception as e:
        raise VisualExtractionError("Upstream request failed") from e

    # Parse response
    try:
        response_json = json.loads(response_body)
    except json.JSONDecodeError as e:
        raise VisualExtractionError("Malformed endpoint response") from e
    if not isinstance(response_json, dict):
        raise VisualExtractionError("Malformed endpoint response")
    choices = response_json.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        raise VisualExtractionError("Malformed endpoint response")
    first = choices[0]
    if not isinstance(first, dict):
        raise VisualExtractionError("Malformed endpoint response")
    message = first.get("message")
    if not isinstance(message, dict):
        raise VisualExtractionError("Malformed endpoint response")
    content = message.get("content")
    if not isinstance(content, str):
        raise VisualExtractionError("Malformed endpoint response")

    # Parse model output
    parsed = _extract_json_content(content)

    # Select only the five untrusted content fields. Identity and hashes are
    # attached from the trusted runtime/configuration below.
    content_fields = {
        "reviewed_text",
        "printed_facts",
        "diagram_facts",
        "handwriting",
        "uncertainties",
    }
    missing_content = sorted(content_fields - parsed.keys())
    if missing_content:
        raise VisualExtractionError(f"Model output is missing fields: {', '.join(missing_content)}")
    selected = {
        "reviewed_text": parsed.get("reviewed_text", ""),
        "printed_facts": parsed.get("printed_facts", []),
        "diagram_facts": parsed.get("diagram_facts", []),
        "handwriting": parsed.get("handwriting", []),
        "uncertainties": parsed.get("uncertainties", []),
    }

    # Validate types minimally
    if not isinstance(selected["reviewed_text"], str):
        raise VisualExtractionError("reviewed_text must be a string")
    if not isinstance(selected["printed_facts"], list) or not all(
        isinstance(x, str) for x in selected["printed_facts"]
    ):
        raise VisualExtractionError("printed_facts must be a list of strings")
    if not isinstance(selected["diagram_facts"], list):
        raise VisualExtractionError("diagram_facts must be a list")
    if not isinstance(selected["handwriting"], list) or not all(isinstance(x, str) for x in selected["handwriting"]):
        raise VisualExtractionError("handwriting must be a list of strings")
    if not isinstance(selected["uncertainties"], list) or not all(
        isinstance(x, str) for x in selected["uncertainties"]
    ):
        raise VisualExtractionError("uncertainties must be a list of strings")

    # Build raw A2 payload locally
    raw_payload = {
        "schema": "wuli.visual-facts.v1",
        "source_fingerprint": expected_source_fingerprint,
        **selected,
        "model_identity": actual_runtime_identity,
    }

    # Normalize and gate
    try:
        normalized = normalize_payload(raw_payload, expected_source_fingerprint)
        gate_result = evaluate_gate(
            normalized,
            expected_source_fingerprint,
            actual_runtime_identity,
            expected_runtime_identity,
        )
    except Exception as e:
        raise VisualExtractionError("A2 processing failed") from e

    # Build trace
    trace = {
        "model_id": config["id"],
        "provider": config["provider"],
        "upstream_model": config["model"],
        "remote": config.get("remote", False),
        "source_fingerprint": expected_source_fingerprint,
    }

    return {
        "visual_facts": normalized,
        "gate_result": gate_result,
        "trace": trace,
    }


VISION_PROBE_SCHEMA = "wuli.vision-probe.v1"


def _probe_failed(model_id: str, reason: str) -> dict:
    return {
        "schema": VISION_PROBE_SCHEMA,
        "status": "failed",
        "model_id": model_id,
        "reason": reason,
    }


def run_vision_probe(
    config: dict,
    image_path: str,
    *,
    urlopen: Callable[..., Any],
    allow_remote: bool,
) -> dict:
    """Probe a vision-capable model with a synthetic, privacy-free image.

    Uses the same endpoint, image message format, and JSON response contract
    as production extraction so a text probe that passes cannot mask a broken
    vision endpoint. Failures (endpoint 404, auth, malformed JSON, image
    unreadable) are reported as ``status=failed`` with a durable reason.
    """
    model_id = str(config.get("id", "")).strip()
    if config.get("provider") != "openai-compatible":
        return _probe_failed(model_id, "provider is not openai-compatible")
    traits = config.get("traits") or {}
    if not traits.get("vision"):
        return _probe_failed(model_id, "model does not declare vision trait")
    if config.get("remote") and not allow_remote:
        return _probe_failed(model_id, "remote vision probe is disabled")
    for field in ("model", "base_url"):
        if not str(config.get(field, "")).strip():
            return _probe_failed(model_id, f"model config is missing {field}")
    image = str(image_path or "").strip()
    if not image or not os.path.isfile(image):
        return _probe_failed(model_id, "probe image is missing")

    prompt = (
        '你是视觉能力探针。请仅输出一个 JSON 对象：{"ok": true}。如果图片无法读取或不是有效图像，输出 {"ok": false}。'
    )
    try:
        image_data_urls = [_load_image_data_url(image)]
        body = _build_upstream_request(config, prompt, image_data_urls, response_format_supported=True)
        api_key = _resolve_api_key(config)
    except VisualExtractionError as exc:
        return _probe_failed(model_id, str(exc))
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
        return _probe_failed(model_id, f"endpoint returned HTTP {exc.code}")
    except (URLError, OSError) as exc:
        return _probe_failed(model_id, f"network error: {exc}")
    try:
        response_json = json.loads(response_body)
        choices = response_json.get("choices") if isinstance(response_json, dict) else None
        content = (
            choices[0].get("message", {}).get("content", "")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict)
            else ""
        )
        parsed = _extract_json_content(content)
        ok = parsed.get("ok") is True
    except (json.JSONDecodeError, VisualExtractionError, KeyError, IndexError, TypeError):
        return _probe_failed(model_id, "malformed or unexpected probe response")
    if not ok:
        return _probe_failed(model_id, "probe response did not confirm vision capability")
    return {
        "schema": VISION_PROBE_SCHEMA,
        "status": "passed",
        "model_id": model_id,
        "upstream_model": str(config.get("model", "")).strip(),
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason": "synthetic image probe passed",
    }
