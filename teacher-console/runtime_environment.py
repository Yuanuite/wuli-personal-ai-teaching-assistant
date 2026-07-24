#!/usr/bin/env python3
"""Local-only Agent runtime settings and deterministic environment resolution."""

from __future__ import annotations

import os
import hashlib
import json
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import kb

SCHEMA_VERSION = 1
PROXY_MODES = {"inherit", "direct", "manual"}
COMMON_PROXY_PORTS = (7890, 7897, 1080)
CHATGPT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
PROXY_ENV_KEYS = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
}


def runtime_settings_path(library: Path) -> Path:
    return Path(library) / "config" / "agent-runtime.json"


def _settings_digest(settings: dict) -> str:
    payload = {
        "codex_path": str(settings.get("codex_path", "")).strip(),
        "proxy": {
            "mode": str(settings.get("proxy", {}).get("mode", "inherit")).strip(),
            "url": str(settings.get("proxy", {}).get("url", "")).strip(),
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _clean_proxy_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https", "socks5", "socks5h"}:
        raise ValueError("代理地址仅支持 http、https、socks5 或 socks5h")
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("网页设置只允许本机回环代理，远程代理请使用系统环境变量")
    if not parsed.port:
        raise ValueError("代理地址必须包含端口")
    if parsed.username or parsed.password:
        raise ValueError("请勿在代理地址中保存用户名或密码")
    return raw


def _clean_codex_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("Codex 路径必须是绝对路径")
    return str(path)


def load_runtime_settings(library: Path) -> dict:
    raw = kb.load_json(runtime_settings_path(library), {})
    proxy = raw.get("proxy") if isinstance(raw.get("proxy"), dict) else {}
    mode = str(proxy.get("mode", "inherit")).strip().lower()
    if mode not in PROXY_MODES:
        mode = "inherit"
    return {
        "schema_version": SCHEMA_VERSION,
        "codex_path": str(raw.get("codex_path", "")).strip(),
        "proxy": {
            "mode": mode,
            "url": str(proxy.get("url", "")).strip(),
        },
    }


def save_runtime_settings(library: Path, data: dict) -> dict:
    proxy = data.get("proxy") if isinstance(data.get("proxy"), dict) else {}
    mode = str(proxy.get("mode", "inherit")).strip().lower()
    if mode not in PROXY_MODES:
        raise ValueError("代理模式必须是 inherit、direct 或 manual")
    proxy_url = _clean_proxy_url(proxy.get("url"))
    if mode == "manual" and not proxy_url:
        raise ValueError("使用本地代理时必须填写代理地址")
    cleaned = {
        "schema_version": SCHEMA_VERSION,
        "codex_path": _clean_codex_path(data.get("codex_path")),
        "proxy": {"mode": mode, "url": proxy_url},
    }
    previous = kb.load_json(runtime_settings_path(library), {})
    previous_probe = previous.get("probe") if isinstance(previous.get("probe"), dict) else {}
    if previous_probe.get("config_digest") == _settings_digest(cleaned):
        cleaned["probe"] = previous_probe
    path = runtime_settings_path(library)
    path.parent.mkdir(parents=True, exist_ok=True)
    kb.write_json(path, cleaned)
    return runtime_settings_public(library)


def resolved_environment(library: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve the current runtime settings on every task, without restarting the server."""
    env = dict(os.environ if base is None else base)
    settings = load_runtime_settings(library)
    codex_path = settings["codex_path"]
    if codex_path:
        env["TEACHER_CONSOLE_CODEX_PATH"] = codex_path
    else:
        env.pop("TEACHER_CONSOLE_CODEX_PATH", None)

    proxy = settings["proxy"]
    if proxy["mode"] == "direct":
        for key in PROXY_ENV_KEYS:
            env.pop(key, None)
    elif proxy["mode"] == "manual":
        for key in PROXY_ENV_KEYS:
            env[key] = proxy["url"]
    return env


def _version(path: str, run: Callable = subprocess.run) -> str:
    if not path:
        return ""
    try:
        result = run([path, "--version"], capture_output=True, text=True, check=False, timeout=4)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return " ".join((result.stdout or result.stderr).strip().splitlines())[:160]


def _codex_candidates(
    settings: dict,
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable = subprocess.run,
) -> list[dict]:
    candidates: list[tuple[str, str]] = []
    configured = settings.get("codex_path", "")
    if configured:
        candidates.append(("已保存", configured))
    if CHATGPT_CODEX.is_file():
        candidates.append(("ChatGPT 内置", str(CHATGPT_CODEX)))
    discovered = which("codex")
    if discovered:
        candidates.append(("PATH", discovered))
    seen: set[str] = set()
    result = []
    for source, raw in candidates:
        path = str(Path(raw).expanduser())
        if path in seen:
            continue
        seen.add(path)
        exists = Path(path).is_file()
        result.append({
            "source": source,
            "path": path,
            "exists": exists,
            "version": _version(path, run) if exists else "",
        })
    return result


def _proxy_alive(url: str, connector: Callable = socket.create_connection) -> bool:
    parsed = urlparse(url)
    try:
        connection = connector((parsed.hostname or "127.0.0.1", int(parsed.port or 0)), timeout=0.18)
        connection.close()
        return True
    except OSError:
        return False


def _proxy_candidates(settings: dict, connector: Callable = socket.create_connection) -> list[dict]:
    urls = []
    configured = settings.get("proxy", {}).get("url", "")
    if configured:
        urls.append(configured)
    urls.extend(f"http://127.0.0.1:{port}" for port in COMMON_PROXY_PORTS)
    seen: set[str] = set()
    result = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        result.append({"url": url, "available": _proxy_alive(url, connector)})
    return result


def runtime_settings_public(
    library: Path,
    *,
    base: dict[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable = subprocess.run,
    connector: Callable = socket.create_connection,
) -> dict:
    settings = load_runtime_settings(library)
    codex_candidates = _codex_candidates(settings, which=which, run=run)
    proxy_candidates = _proxy_candidates(settings, connector)
    selected_codex = settings["codex_path"] or next(
        (item["path"] for item in codex_candidates if item["exists"]),
        "",
    )
    resolved = resolved_environment(library, base)
    active_proxy = resolved.get("HTTPS_PROXY") or resolved.get("https_proxy") or ""
    raw = kb.load_json(runtime_settings_path(library), {})
    probe = raw.get("probe") if isinstance(raw.get("probe"), dict) else {}
    probe_passed = (
        probe.get("status") == "passed"
        and probe.get("config_digest") == _settings_digest(settings)
    )
    return {
        **settings,
        "path": str(runtime_settings_path(library)),
        "exists": runtime_settings_path(library).exists(),
        "codex": {
            "selected_path": selected_codex,
            "available": bool(selected_codex and Path(selected_codex).is_file()),
            "candidates": codex_candidates,
        },
        "proxy_status": {
            "active_url": active_proxy,
            "configured_mode": settings["proxy"]["mode"],
            "candidates": proxy_candidates,
        },
        "probe_status": "passed" if probe_passed else str(probe.get("status", "untested")),
        "probe_passed": probe_passed,
        "probe_message": str(probe.get("message", "")).strip(),
        "probe_checked_at": str(probe.get("checked_at", "")).strip(),
    }


def classify_runtime_probe(snapshot: dict, probe: dict) -> dict:
    live = probe.get("live_probe") if isinstance(probe.get("live_probe"), dict) else {}
    passed = live.get("status") == "passed"
    reason = str(live.get("reason", "")).strip()
    lower = reason.lower()
    if passed:
        code = "ready"
        message = "Codex 运行环境正常，可以执行 Agent 任务。"
    elif not snapshot.get("codex", {}).get("available"):
        code = "codex_missing"
        message = "未找到可用的 Codex CLI，请选择一个已安装的 Codex。"
    elif "login" in lower or "auth" in lower or "401" in lower:
        code = "auth_required"
        message = "Codex 尚未登录或认证已失效，请先在终端执行 codex login。"
    elif "timeout" in lower or "timed out" in lower or "disconnect" in lower or "stream" in lower:
        candidates = snapshot.get("proxy_status", {}).get("candidates", [])
        if any(item.get("available") for item in candidates):
            code = "proxy_available"
            message = "直连失败，但检测到本地代理；选择可用代理并保存后重试。"
        else:
            code = "network_unreachable"
            message = "Codex 无法连接模型服务，请检查网络或启动本地代理。"
    else:
        code = "probe_failed"
        message = reason or "Codex 真实推理测试失败。"
    return {
        "status": "passed" if passed else "failed",
        "code": code,
        "message": message,
        "detail": reason,
        "student_data_sent": False,
    }


def update_runtime_probe_result(library: Path, diagnosis: dict) -> dict:
    path = runtime_settings_path(library)
    raw = kb.load_json(path, load_runtime_settings(library))
    settings = load_runtime_settings(library)
    raw["probe"] = {
        "status": "passed" if diagnosis.get("status") == "passed" else "failed",
        "code": str(diagnosis.get("code", "")).strip(),
        "message": str(diagnosis.get("message", "")).strip(),
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "config_digest": _settings_digest(settings),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    kb.write_json(path, raw)
    return runtime_settings_public(library)
