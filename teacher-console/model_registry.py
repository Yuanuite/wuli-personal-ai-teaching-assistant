"""Local model registry: storage, validation, probe, and config for Agent providers.

Extracted from server.py so Agent Gateway and future tools can
query / register models without importing the full HTTP server.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_CONSOLE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _CONSOLE_DIR.parent
_SKILL_SCRIPTS = _PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(_SKILL_SCRIPTS))

import kb  # noqa: E402

MODEL_ID_MAX = 80
TRAIT_KEYS = {"vision", "agent"}
LIBRARY: Path = _PROJECT_ROOT / "student-error-library"


def normalize_model_id(value) -> str:
    model_id = str(value or "auto").strip()
    if not model_id or model_id == "auto":
        return "auto"
    if len(model_id) > MODEL_ID_MAX or any(ch in model_id for ch in "/\\\0"):
        raise ValueError("model_id contains unsupported characters")
    return model_id


def _normalize_routing_tier(value) -> str:
    tier = str(value or "auto").strip().lower()
    if tier not in {"auto", "economy", "expert"}:
        raise ValueError("routing_tier must be auto, economy, or expert")
    return tier


def _registry_path() -> Path:
    return LIBRARY / "config" / "model-registry.json"


def remote_agent_allowed() -> bool:
    config = kb.load_json(LIBRARY / "config.json", {})
    return config.get("privacy", {}).get("allow_remote_agent") is True


def _stored_api_key(raw: dict) -> str:
    return str(raw.get("api_key", "")).strip()


def _model_probe_digest(raw: dict) -> str:
    provider = str(raw.get("provider", "")).strip()
    has_api = provider in {"openai-compatible", "claude"}
    api_key_env = str(raw.get("api_key_env", "")).strip()
    if not api_key_env:
        api_key_env = "ANTHROPIC_API_KEY" if provider == "claude" else "TEACHER_CONSOLE_AGENT_API_KEY"
    payload = {
        "provider": provider,
        "base_url": str(raw.get("base_url", "")).strip() if has_api else "",
        "model": str(raw.get("model", "")).strip() if has_api else "",
        "api_key_env": api_key_env if has_api else "",
        "api_key_digest": hashlib.sha256(_stored_api_key(raw).encode("utf-8")).hexdigest()
        if has_api and _stored_api_key(raw)
        else "",
        "remote": bool(raw.get("remote", False)) if has_api else False,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _model_probe(raw: dict) -> dict:
    probe = raw.get("probe") if isinstance(raw.get("probe"), dict) else {}
    current_digest = _model_probe_digest(raw)
    status = str(probe.get("status", "")).strip()
    passed = status == "passed" and str(probe.get("config_digest", "")) == current_digest
    return {
        "status": "passed" if passed else (status or "untested"),
        "passed": passed,
        "message": str(probe.get("message", "")).strip(),
        "checked_at": str(probe.get("checked_at", "")).strip(),
        "provider": str(probe.get("provider", "")).strip(),
        "config_digest": str(probe.get("config_digest", "")).strip(),
        "current_digest": current_digest,
    }


def _public_model_entry(raw: dict, *, kind: str = "") -> dict[str, Any]:
    model_id = normalize_model_id(raw.get("id"))
    provider = str(raw.get("provider", "")).strip()
    capabilities = [str(item) for item in raw.get("capabilities", []) if str(item).strip()]
    base_url = str(raw.get("base_url", "")).strip()
    remote = bool(raw.get("remote", False))
    if provider in {"openai-compatible", "claude"} and base_url and (
        urlparse(base_url).hostname or ""
    ).lower() not in {"127.0.0.1", "localhost", "::1"}:
        remote = True
    api_key_env = str(raw.get("api_key_env", "")).strip()
    if not api_key_env:
        api_key_env = "ANTHROPIC_API_KEY" if provider == "claude" else "TEACHER_CONSOLE_AGENT_API_KEY"
    has_api = provider in {"openai-compatible", "claude"}
    api_key_configured = has_api and bool(_stored_api_key(raw) or os.environ.get(api_key_env))
    probe = _model_probe(raw)
    errors: list[str] = []
    if provider not in {"openai-compatible", "adapter", "codex", "claude"}:
        errors.append("unsupported provider")
    if provider == "openai-compatible":
        if not base_url:
            errors.append("missing base_url")
        if not str(raw.get("model", "")).strip():
            errors.append("missing model")
    if kind and kind != "gateway.probe" and capabilities and kind not in capabilities:
        errors.append(f"unsupported task {kind}")
    if remote and not remote_agent_allowed():
        errors.append("project remote privacy gate is off")
    if provider in {"openai-compatible", "claude"} and remote and not api_key_configured:
        errors.append("missing API key")
    if kind != "gateway.probe" and raw.get("enabled") is not False and not probe["passed"]:
        errors.append("model has not passed connection test")
    traits_raw = raw.get("traits") if isinstance(raw.get("traits"), dict) else {}
    traits = {
        key: bool(traits_raw.get(key)) for key in TRAIT_KEYS
    }
    raw_probe = raw.get("probe") if isinstance(raw.get("probe"), dict) else {}
    vision_probe = raw_probe.get("vision") if isinstance(raw_probe.get("vision"), dict) else {}
    return {
        "id": model_id,
        "display_name": str(raw.get("display_name") or raw.get("name") or raw.get("model") or model_id),
        "provider": provider,
        "base_url": base_url if has_api else "",
        "model": str(raw.get("model", "")).strip(),
        "model_tier": str(raw.get("model_tier", raw.get("tier", "selected"))).strip() or "selected",
        "tags": [str(item) for item in raw.get("tags", []) if str(item).strip()],
        "capabilities": capabilities,
        "recommended_for": [str(item) for item in raw.get("recommended_for", []) if str(item).strip()],
        "description": str(raw.get("description", "")).strip(),
        "remote": remote,
        "data_locality": "remote" if remote else str(raw.get("data_locality", "local")).strip() or "local",
        "api_key_env": api_key_env if has_api else "",
        "api_key_configured": api_key_configured if has_api else None,
        "api_key_saved": bool(_stored_api_key(raw)) if has_api else None,
        "probe_status": probe["status"],
        "probe_passed": probe["passed"],
        "probe_message": probe["message"],
        "probe_checked_at": probe["checked_at"],
        "enabled": raw.get("enabled") is not False,
        "available": raw.get("enabled") is not False and not errors,
        "reason": "；".join(errors),
        "traits": traits,
        "vision_probe": {
            "status": str(vision_probe.get("status", "untested")).strip() or "untested",
            "schema": str(vision_probe.get("schema", "")).strip(),
            "message": str(vision_probe.get("message", "")).strip(),
            "checked_at": str(vision_probe.get("checked_at", "")).strip(),
        },
    }


def model_registry_public(*, kind: str = "") -> dict:
    path = _registry_path()
    registry = kb.load_json(path, {"schema_version": 1, "models": [], "defaults": {}})
    raw_models = registry.get("models", []) if isinstance(registry.get("models"), list) else []
    models = []
    for raw in raw_models:
        if isinstance(raw, dict):
            try:
                models.append(_public_model_entry(raw, kind=kind))
            except ValueError:
                continue
    return {
        "schema_version": 1,
        "path": str(path),
        "exists": path.exists(),
        "models": models,
        "defaults": registry.get("defaults", {}) if isinstance(registry.get("defaults"), dict) else {},
    }


def model_registry_settings() -> dict:
    path = _registry_path()
    registry = kb.load_json(path, {"schema_version": 1, "models": [], "defaults": {}})
    if registry.get("schema_version") != 1:
        registry["schema_version"] = 1
    registry.setdefault("defaults", {})
    registry.setdefault("models", [])
    sanitized_models = []
    for raw in registry.get("models", []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item.pop("api_key", None)
        probe = _model_probe(raw)
        item["probe_status"] = probe["status"]
        item["probe_passed"] = probe["passed"]
        item["probe_message"] = probe["message"]
        item["probe_checked_at"] = probe["checked_at"]
        item_provider = str(item.get("provider", "")).strip()
        if item_provider in {"openai-compatible", "claude"}:
            default_env = "ANTHROPIC_API_KEY" if item_provider == "claude" else "TEACHER_CONSOLE_AGENT_API_KEY"
            api_key_env = str(item.get("api_key_env", default_env)).strip() or default_env
            item["api_key_configured"] = bool(_stored_api_key(raw) or os.environ.get(api_key_env))
            item["api_key_saved"] = bool(_stored_api_key(raw))
        sanitized_models.append(item)
    registry["models"] = sanitized_models
    registry["path"] = str(path)
    registry["exists"] = path.exists()
    return registry


def _clean_string_list(values) -> list[str]:
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",")]
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def save_model_registry_settings(data: dict) -> dict:
    defaults = data.get("defaults", {}) if isinstance(data.get("defaults"), dict) else {}
    previous = kb.load_json(_registry_path(), {"models": []})
    previous_by_id = {
        str(item.get("id", "")).strip(): item
        for item in previous.get("models", [])
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    cleaned = {
        "schema_version": 1,
        "defaults": {
            str(key).strip(): normalize_model_id(value)
            for key, value in defaults.items()
            if str(key).strip() in {
                "economy",
                "expert",
                "vision",
                "agent",
                "source.clean",
                "analysis.generate",
                "diagram.scene",
                "answer.revise",
                "claim.verify",
                "visualization.model",
            }
        },
        "models": [],
    }
    seen: set[str] = set()
    for raw in data.get("models", []):
        if not isinstance(raw, dict):
            continue
        model_id = normalize_model_id(raw.get("id"))
        if model_id == "auto" or model_id in seen:
            continue
        seen.add(model_id)
        provider = str(raw.get("provider", "openai-compatible")).strip() or "openai-compatible"
        if provider not in {"openai-compatible", "adapter", "codex", "claude"}:
            raise ValueError(f"unsupported provider for {model_id}: {provider}")
        entry = {
            "id": model_id,
            "display_name": str(raw.get("display_name") or raw.get("name") or raw.get("model") or model_id).strip(),
            "provider": provider,
            "enabled": raw.get("enabled") is not False,
            "model_tier": str(raw.get("model_tier", raw.get("tier", "selected"))).strip() or "selected",
            "tags": _clean_string_list(raw.get("tags", [])),
            "capabilities": _clean_string_list(raw.get("capabilities", [])),
            "traits": raw.get("traits") if isinstance(raw.get("traits"), dict) else {},
            "recommended_for": _clean_string_list(raw.get("recommended_for", [])),
            "description": str(raw.get("description", "")).strip(),
        }
        if provider in {"openai-compatible", "claude"}:
            api_key = str(raw.get("api_key", "")).strip()
            previous_key = _stored_api_key(previous_by_id.get(model_id, {}))
            entry["model"] = str(raw.get("model", "")).strip()
            entry.update({
                "base_url": str(raw.get("base_url", "")).strip(),
                "remote": bool(raw.get("remote", False)),
            })
            default_env = "ANTHROPIC_API_KEY" if provider == "claude" else "TEACHER_CONSOLE_AGENT_API_KEY"
            entry["api_key_env"] = str(raw.get("api_key_env", default_env)).strip() or default_env
            if api_key:
                entry["api_key"] = api_key
            elif raw.get("clear_api_key") is True:
                pass
            elif previous_key:
                entry["api_key"] = previous_key
            timeout = str(raw.get("timeout_seconds", "")).strip()
            if timeout:
                entry["timeout_seconds"] = timeout
        previous_probe = previous_by_id.get(model_id, {}).get("probe")
        if isinstance(previous_probe, dict) and previous_probe.get("config_digest") == _model_probe_digest(entry):
            entry["probe"] = previous_probe
        cleaned["models"].append(entry)
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    kb.write_json(path, cleaned)
    return model_registry_public()


def resolve_model_id_for_task(kind: str, routing_tier: str, model_id: str | None) -> str:
    if model_id is None:
        return "auto"
    model_id = normalize_model_id(model_id)
    if model_id != "auto":
        return model_id
    defaults = kb.load_json(_registry_path(), {"defaults": {}}).get("defaults", {})
    if not isinstance(defaults, dict):
        return "auto"
    tier = _normalize_routing_tier(routing_tier)
    if tier in {"economy", "expert"}:
        return normalize_model_id(defaults.get(tier))
    return normalize_model_id(defaults.get(kind))


def _model_to_config(raw: dict, public: dict) -> dict:
    """Build a model_config dict from a raw registry entry and its public entry."""
    config = {
        "id": public["id"],
        "display_name": public["display_name"],
        "provider": public["provider"],
        "model": public["model"],
        "model_tier": public["model_tier"],
        "remote": public["remote"],
        "data_locality": public["data_locality"],
        "traits": public.get("traits", {}),
    }
    if public["provider"] in {"openai-compatible", "claude"}:
        if public["provider"] == "openai-compatible":
            config.update({
                "base_url": str(raw.get("base_url", "")).strip(),
                "api_key_env": public["api_key_env"],
                "api_key": _stored_api_key(raw),
                "timeout_seconds": str(raw.get("timeout_seconds", "")).strip(),
            })
        else:
            config.update({
                "base_url": str(raw.get("base_url", "")).strip(),
                "api_key_env": public["api_key_env"],
                "api_key": _stored_api_key(raw),
                "timeout_seconds": str(raw.get("timeout_seconds", "")).strip(),
            })
    return config


def model_config_for_task(kind: str, model_id: str, routing_tier: str = "auto") -> dict | None:
    model_id = resolve_model_id_for_task(kind, routing_tier, model_id)
    if model_id == "auto":
        registry = kb.load_json(_registry_path(), {"models": []})
        for raw in registry.get("models", []):
            if not isinstance(raw, dict):
                continue
            try:
                public = _public_model_entry(raw, kind=kind)
            except ValueError:
                continue
            if public["enabled"] and public["available"]:
                return _model_to_config(raw, public)
        return None
    registry = kb.load_json(_registry_path(), {"models": []})
    for raw in registry.get("models", []):
        if not isinstance(raw, dict):
            continue
        try:
            public = _public_model_entry(raw, kind=kind)
        except ValueError:
            continue
        if public["id"] != model_id:
            continue
        if not public["enabled"]:
            raise ValueError(f"模型 {public['display_name']} 已禁用")
        if not public["available"]:
            raise ValueError(f"模型 {public['display_name']} 暂不可用：{public['reason']}")
        return _model_to_config(raw, public)
    raise ValueError(f"未找到模型配置：{model_id}")


def update_model_probe_result(model_id: str, result: dict) -> dict:
    model_id = normalize_model_id(model_id)
    if model_id == "auto":
        raise ValueError("model_id is required")
    registry = kb.load_json(_registry_path(), {"schema_version": 1, "defaults": {}, "models": []})
    found = False
    for raw in registry.get("models", []):
        if not isinstance(raw, dict) or normalize_model_id(raw.get("id")) != model_id:
            continue
        found = True
        passed = result.get("live_probe", {}).get("status") == "passed"
        raw["probe"] = {
            "status": "passed" if passed else "failed",
            "provider": str(result.get("live_probe", {}).get("provider", "")).strip(),
            "message": str(
                result.get("live_probe", {}).get("reason", "") or ("连通检测通过" if passed else "连通检测失败")
            ).strip(),
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "config_digest": _model_probe_digest(raw),
        }
        break
    if not found:
        raise ValueError(f"未找到模型配置：{model_id}")
    kb.write_json(_registry_path(), registry)
    return model_registry_settings()


def record_vision_probe(model_id: str, result: dict) -> dict:
    """Persist a vision probe result (``wuli.vision-probe.v1``) per model.

    The vision probe uses the same endpoint and image message format as
    production extraction; a failed probe marks the model unavailable for
    ``vision`` routing regardless of its text connection probe.
    """
    model_id = normalize_model_id(model_id)
    if model_id == "auto":
        raise ValueError("model_id is required")
    registry = kb.load_json(_registry_path(), {"schema_version": 1, "defaults": {}, "models": []})
    found = False
    for raw in registry.get("models", []):
        if not isinstance(raw, dict) or normalize_model_id(raw.get("id")) != model_id:
            continue
        found = True
        probe = raw.get("probe") if isinstance(raw.get("probe"), dict) else {}
        probe["vision"] = {
            "status": "passed" if result.get("status") == "passed" else "failed",
            "schema": str(result.get("schema", "")).strip(),
            "message": str(result.get("reason", "")).strip(),
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "config_digest": _model_probe_digest(raw),
        }
        raw["probe"] = probe
        break
    if not found:
        raise ValueError(f"未找到模型配置：{model_id}")
    kb.write_json(_registry_path(), registry)
    return model_registry_settings()


def _declared_traits(registry: dict, model_id: str) -> dict:
    """Return the declared trait map for a model id (empty when unknown)."""
    for raw in registry.get("models", []):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("id", "")).strip() != model_id:
            continue
        traits_raw = raw.get("traits") if isinstance(raw.get("traits"), dict) else {}
        return {key: bool(traits_raw.get(key)) for key in TRAIT_KEYS}
    return {}


def resolve_model_id_for_trait(trait: str, routing_tier: str = "auto", model_id: str | None = None) -> str:
    """Resolve model ID for a given capability trait (vision / agent).

    Fail-closed trait policy: the resolved model must declare
    ``traits.<trait>=true``. A cost-tier override (economy/expert) never
    substitutes a text-only model for a visual task — it falls back to the
    trait-specific default — and an explicit model that lacks the trait fails
    with a stable error instead of degrading silently.
    """
    if trait not in TRAIT_KEYS:
        raise ValueError(f"unsupported trait: {trait}")
    registry = kb.load_json(_registry_path(), {"models": [], "defaults": {}})
    if model_id is not None:
        resolved = normalize_model_id(model_id)
        if resolved != "auto":
            if not _declared_traits(registry, resolved).get(trait):
                raise ValueError(f"模型 {resolved} 不具备 {trait} 能力（traits.{trait}=false）")
            return resolved
    defaults = registry.get("defaults", {})
    if not isinstance(defaults, dict):
        return "auto"
    tier = _normalize_routing_tier(routing_tier)
    if tier in {"economy", "expert"}:
        tier_model = normalize_model_id(defaults.get(tier))
        if _declared_traits(registry, tier_model).get(trait):
            return tier_model
        return normalize_model_id(defaults.get(trait))
    return normalize_model_id(defaults.get(trait))


def _vision_probe_allowed(public: dict) -> bool:
    status = str(public.get("vision_probe", {}).get("status", "untested"))
    return status != "failed"


def model_config_for_trait(trait: str, model_id: str | None = None, routing_tier: str = "auto") -> dict | None:
    """Get full model config for a given capability trait."""
    model_id = resolve_model_id_for_trait(trait, routing_tier, model_id)
    if model_id == "auto":
        registry = kb.load_json(_registry_path(), {"models": []})
        for raw in registry.get("models", []):
            if not isinstance(raw, dict):
                continue
            try:
                public = _public_model_entry(raw)
            except ValueError:
                continue
            traits = public.get("traits", {})
            if (
                public["enabled"]
                and public["available"]
                and traits.get(trait)
                and (trait != "vision" or _vision_probe_allowed(public))
            ):
                return _model_to_config(raw, public)
        return None
    registry = kb.load_json(_registry_path(), {"models": []})
    for raw in registry.get("models", []):
        if not isinstance(raw, dict):
            continue
        try:
            public = _public_model_entry(raw)
        except ValueError:
            continue
        if public["id"] != model_id:
            continue
        if not public["enabled"]:
            raise ValueError(f"模型 {public['display_name']} 已禁用")
        if not public["available"]:
            raise ValueError(f"模型 {public['display_name']} 暂不可用：{public['reason']}")
        if trait == "vision" and not _vision_probe_allowed(public):
            raise ValueError(
                f"模型 {public['display_name']} 视觉探针未通过"
                f"（vision_probe={public.get('vision_probe', {}).get('status')}），不能用于视觉任务"
            )
        return _model_to_config(raw, public)
    raise ValueError(f"未找到模型配置：{model_id}")
