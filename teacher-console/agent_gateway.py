#!/usr/bin/env python3
"""Provider-neutral local gateway for scoped teacher-console Agent tasks."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from log import logger

CONSOLE_DIR = Path(__file__).resolve().parent
OPENAI_ADAPTER = CONSOLE_DIR / "providers" / "openai_compatible_agent_adapter.py"
PROVIDER_NAMES = {"adapter", "legacy-command", "openai-compatible", "codex", "claude"}
ROUTING_TIERS = {"auto", "economy", "expert"}
BASE_ENV_KEYS = {
    "HOME",
    "USER",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


def classify_agent_failure(result: dict) -> str:
    """Return a stable, low-cardinality failure code for one Gateway result/attempt."""
    status = str(result.get("status", "")).strip().lower()
    if status == "completed":
        return ""

    unauthorized = result.get("unauthorized_changes")
    unauthorized = unauthorized if isinstance(unauthorized, list) else []
    if any(str(item).startswith("canonical:") for item in unauthorized):
        return "canonical_changed"
    if unauthorized:
        return "unauthorized_change"

    validation = result.get("validation_errors")
    validation = validation if isinstance(validation, list) else []
    attempts = result.get("attempts")
    attempts = attempts if isinstance(attempts, list) else []
    text_parts = [
        result.get("message"),
        result.get("error"),
        result.get("stderr"),
        *validation,
    ]
    for attempt in attempts:
        if isinstance(attempt, dict):
            text_parts.extend((attempt.get("error"), attempt.get("stderr")))
    text = " ".join(str(value) for value in text_parts if value).lower()

    if "timeout" in text or "timed out" in text or "超时" in text:
        return "provider_timeout"
    if "rate limit" in text or "rate_limit" in text or "429" in text or "限流" in text:
        return "provider_rate_limited"
    if "truncated" in text or "截断" in text:
        return "output_truncated"
    if validation:
        return "candidate_validation_failed"
    if status == "unavailable" or "没有可用" in text:
        return "provider_unavailable"
    if result.get("parse_error") or "adapter output" in text or "adapter 输出" in text:
        return "adapter_protocol_error"
    if result.get("requires_change") and not result.get("changed_files"):
        return "candidate_no_change"
    returncode = result.get("returncode")
    if isinstance(returncode, int) and returncode != 0:
        return "provider_execution_failed"
    return "provider_failed"


@dataclass(frozen=True)
class Provider:
    name: str
    mode: str
    command: tuple[str, ...]
    available: bool
    reason: str = ""
    version: str = ""


def _command_exists(token: str, which: Callable[[str], str | None]) -> bool:
    path = Path(token).expanduser()
    return path.is_file() if path.is_absolute() or "/" in token else bool(which(token))


def _first_line(value: str, limit: int = 240) -> str:
    return " ".join(value.strip().splitlines())[:limit]


def _probe_version(binary: str, run: Callable = subprocess.run) -> str:
    try:
        completed = run(
            [binary, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return _first_line(completed.stdout or completed.stderr)


def _probe_flags(
    binary: str, arguments: list[str], required: tuple[str, ...], run: Callable = subprocess.run
) -> tuple[bool, str]:
    try:
        completed = run([binary, *arguments], text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"CLI 探测失败：{exc}"
    output = f"{completed.stdout}\n{completed.stderr}"
    missing = [flag for flag in required if flag not in output]
    if completed.returncode != 0:
        return False, f"CLI help 退出码 {completed.returncode}"
    if missing:
        return False, "CLI 缺少所需参数：" + ", ".join(missing)
    return True, ""


def _is_loopback_url(raw: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(raw).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


class AgentGateway:
    """Select providers, run one scoped task, and fail over before any mutation."""

    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable = subprocess.run,
    ):
        self.environ = os.environ if environ is None else environ
        self.which = which
        self.run_process = run
        self._health_lock = threading.Lock()
        self._health_cache: dict | None = None
        self._health_checked = 0.0
        self._provider_failures: dict[str, tuple[float, str]] = {}

    def _runtime_failure(self, provider: str) -> str:
        with self._health_lock:
            value = self._provider_failures.get(provider)
            if not value:
                return ""
            expires_at, reason = value
            if expires_at <= time.monotonic():
                self._provider_failures.pop(provider, None)
                self._health_cache = None
                return ""
            return reason

    def _mark_runtime_failure(self, provider: str, reason: str) -> None:
        try:
            seconds = int(self.environ.get("TEACHER_CONSOLE_AGENT_FAILURE_COOLDOWN_SECONDS", "300"))
        except ValueError:
            seconds = 300
        seconds = max(30, min(seconds, 3600))
        with self._health_lock:
            self._provider_failures[provider] = (time.monotonic() + seconds, _first_line(reason, 500) or "运行失败")
            self._health_cache = None

    def _clear_runtime_failure(self, provider: str) -> None:
        with self._health_lock:
            self._provider_failures.pop(provider, None)
            self._health_cache = None

    def _task_environ(self, task: dict | None = None) -> dict[str, str]:
        env = dict(self.environ)
        config = task.get("model_config") if isinstance(task, dict) else None
        if not isinstance(config, dict):
            return env
        provider = str(config.get("provider", "")).strip()
        if provider:
            env["TEACHER_CONSOLE_AGENT_PROVIDER"] = provider
        if provider == "openai-compatible":
            for key, target in (
                ("base_url", "TEACHER_CONSOLE_AGENT_API_BASE_URL"),
                ("model", "TEACHER_CONSOLE_AGENT_API_MODEL"),
                ("timeout_seconds", "TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS"),
            ):
                value = str(config.get(key, "")).strip()
                if value:
                    env[target] = value
            api_key = str(config.get("api_key", "")).strip()
            if api_key:
                env["TEACHER_CONSOLE_AGENT_API_KEY"] = api_key
                return env
            api_key_env = str(config.get("api_key_env", "")).strip() or "TEACHER_CONSOLE_AGENT_API_KEY"
            if api_key_env in self.environ:
                env["TEACHER_CONSOLE_AGENT_API_KEY"] = self.environ[api_key_env]
        elif provider == "claude":
            api_key = str(config.get("api_key", "")).strip()
            if api_key:
                env["ANTHROPIC_API_KEY"] = api_key
                return env
            api_key_env = str(config.get("api_key_env", "")).strip() or "ANTHROPIC_API_KEY"
            if api_key_env in self.environ:
                env["ANTHROPIC_API_KEY"] = self.environ[api_key_env]
        return env

    @staticmethod
    def _task_without_secrets(task: dict) -> dict:
        safe = dict(task)
        # Inline context is materialized as a read-only staging file before the
        # child starts. Do not duplicate it in adapter stdin or CLI metadata.
        safe.pop("context_payloads", None)
        config = safe.get("model_config")
        if isinstance(config, dict):
            safe_config = dict(config)
            safe_config.pop("api_key", None)
            safe["model_config"] = safe_config
        return safe

    def providers(
        self,
        *,
        include_versions: bool = False,
        ignore_runtime_failures: bool = False,
        environ: dict[str, str] | None = None,
    ) -> list[Provider]:
        env = self.environ if environ is None else environ
        providers: list[Provider] = []
        adapter = env.get("TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND", "").strip()
        if adapter:
            tokens = tuple(shlex.split(adapter))
            available = bool(tokens) and _command_exists(tokens[0], self.which)
            providers.append(
                Provider("adapter", "json-adapter", tokens, available, "" if available else "适配器命令不存在")
            )

        legacy = env.get("TEACHER_CONSOLE_AGENT_COMMAND", "").strip()
        if legacy:
            tokens = tuple(shlex.split(legacy))
            available = bool(tokens) and _command_exists(tokens[0], self.which)
            providers.append(
                Provider("legacy-command", "legacy-command", tokens, available, "" if available else "自定义命令不存在")
            )

        api_base = env.get("TEACHER_CONSOLE_AGENT_API_BASE_URL", "").strip()
        api_model = env.get("TEACHER_CONSOLE_AGENT_API_MODEL", "").strip()
        if api_base or api_model:
            ready = bool(api_base and api_model and OPENAI_ADAPTER.is_file())
            reason = "" if ready else "API provider 需要同时配置 BASE_URL 与 MODEL"
            if ready and not _is_loopback_url(api_base) and env.get("TEACHER_CONSOLE_AGENT_ALLOW_REMOTE") != "true":
                ready, reason = False, "远程 API 尚未通过环境变量隐私门禁"
            providers.append(
                Provider(
                    "openai-compatible", "json-adapter", (sys.executable, str(OPENAI_ADAPTER)), ready, reason, api_model
                )
            )

        for name in ("codex", "claude"):
            binary = self.which(name)
            available = bool(binary)
            version = _probe_version(binary, self.run_process) if binary and include_versions else ""
            reason = "" if available else f"未找到 {name} 可执行文件"
            if binary and include_versions:
                arguments = ["exec", "--help"] if name == "codex" else ["--help"]
                required = (
                    ("--sandbox", "--ephemeral", "--cd", "--ignore-user-config")
                    if name == "codex"
                    else ("--print", "--permission-mode", "--no-session-persistence", "--safe-mode")
                )
                available, reason = _probe_flags(binary, arguments, required, self.run_process)
            providers.append(Provider(name, "cli", (binary,) if binary else (), available, reason, version))
        result: list[Provider] = []
        for item in providers:
            runtime_failure = "" if ignore_runtime_failures else self._runtime_failure(item.name)
            if item.available and runtime_failure:
                result.append(
                    Provider(
                        item.name,
                        item.mode,
                        item.command,
                        False,
                        f"运行失败后暂时降级：{runtime_failure}",
                        item.version,
                    )
                )
            else:
                result.append(item)
        return result

    def health(self, *, force: bool = False) -> dict:
        with self._health_lock:
            if not force and self._health_cache is not None and time.monotonic() - self._health_checked < 30:
                return dict(self._health_cache)
        requested = self.environ.get("TEACHER_CONSOLE_AGENT_PROVIDER", "auto").strip() or "auto"
        providers = self.providers(include_versions=True)
        selected = next((item.name for item in self._ordered(providers, requested) if item.available), None)
        api_base = self.environ.get("TEACHER_CONSOLE_AGENT_API_BASE_URL", "").strip()

        def details(item: Provider) -> dict:
            if item.name == "openai-compatible":
                data_locality = "local" if api_base and _is_loopback_url(api_base) else "remote"
            elif item.name in {"codex", "claude"}:
                data_locality = "provider-dependent"
            else:
                data_locality = "declared-by-adapter"
            return {
                "name": item.name,
                "mode": item.mode,
                "available": item.available,
                "reason": item.reason,
                "version": item.version,
                "execution_locality": "local-process",
                "data_locality": data_locality,
                "model": item.version if item.name == "openai-compatible" else "",
                "routing_models": (
                    {
                        "standard": self.environ.get("TEACHER_CONSOLE_AGENT_API_MODEL", "").strip(),
                        "economy": self.environ.get("TEACHER_CONSOLE_AGENT_API_ECONOMY_MODEL", "").strip(),
                        "expert": self.environ.get("TEACHER_CONSOLE_AGENT_API_EXPERT_MODEL", "").strip(),
                    }
                    if item.name == "openai-compatible"
                    else {}
                ),
                "required_env": (
                    ["TEACHER_CONSOLE_AGENT_API_BASE_URL", "TEACHER_CONSOLE_AGENT_API_MODEL"]
                    if item.name == "openai-compatible"
                    else []
                ),
                "capabilities": {
                    "task_types": ["analysis.generate", "answer.revise", "visualization.model"],
                    "filesystem": item.mode != "json-adapter",
                    "structured_output": item.mode == "json-adapter",
                    "vision": False,
                },
            }

        result = {
            "available": selected is not None,
            "selected": selected,
            "mode": requested,
            "reason": ""
            if selected
            else (
                f"未知 provider：{requested}"
                if requested != "auto" and requested not in PROVIDER_NAMES
                else "没有可用 provider"
            ),
            "providers": [details(item) for item in providers],
        }
        with self._health_lock:
            self._health_cache = result
            self._health_checked = time.monotonic()
        return dict(result)

    def _ordered(self, providers: list[Provider], requested: str) -> list[Provider]:
        if requested != "auto":
            if requested not in PROVIDER_NAMES:
                return []
            return [item for item in providers if item.name == requested]
        priority = {
            name: index
            for index, name in enumerate(("adapter", "openai-compatible", "legacy-command", "codex", "claude"))
        }
        return sorted(providers, key=lambda item: priority.get(item.name, 99))

    def probe(
        self,
        provider_name: str = "",
        *,
        timeout_seconds: int = 120,
        allow_remote: bool = False,
        model_config: dict | None = None,
    ) -> dict:
        """Run an explicit no-student-data connectivity probe for one provider."""
        base_task = {"model_config": model_config or {}}
        task_environ = self._task_environ(base_task)
        requested = (
            provider_name.strip() or task_environ.get("TEACHER_CONSOLE_AGENT_PROVIDER", "auto").strip() or "auto"
        )
        providers = self.providers(include_versions=True, ignore_runtime_failures=True, environ=task_environ)
        candidates = [item for item in self._ordered(providers, requested) if item.available]
        if not candidates:
            health = self.health(force=True)
            health["live_probe"] = {
                "status": "failed",
                "provider": provider_name or None,
                "reason": f"没有可探测的 provider：{requested}",
            }
            return health

        provider = candidates[0]
        timeout = max(10, min(int(timeout_seconds), 120))
        with tempfile.TemporaryDirectory(prefix="wuli-agent-probe-") as directory:
            task = {
                "schema_version": 1,
                "id": f"probe-{int(time.time())}",
                "kind": "gateway.probe",
                "entry_id": "no-student-data",
                "entry_dir": directory,
                "working_dir": directory,
                "prompt": "这是不含学生数据的连通探测。不要读取或写入文件，只回复 GATEWAY_PROBE_OK。",
                "allowed_paths": [],
                "denied_paths": ["**"],
                "requires_change": False,
                "allow_remote": allow_remote,
                "probe": True,
                "model_config": model_config or {},
            }
            safe_task = self._task_without_secrets(task)
            command = self._command(provider, task)
            try:
                completed = self.run_process(
                    command,
                    cwd=directory,
                    text=True,
                    input=json.dumps(safe_task, ensure_ascii=False) if provider.mode == "json-adapter" else "",
                    capture_output=True,
                    check=False,
                    timeout=timeout,
                    env=self._provider_environment(provider, task_environ),
                )
                if provider.mode == "json-adapter":
                    try:
                        payload = json.loads(completed.stdout)
                        responsive = isinstance(payload, dict) and payload.get("status") == "completed"
                    except (ValueError, json.JSONDecodeError):
                        responsive = False
                elif provider.mode == "legacy-command":
                    responsive = completed.returncode == 0
                else:
                    responsive = "GATEWAY_PROBE_OK" in completed.stdout
                passed = completed.returncode == 0 and responsive
                reason = (
                    ""
                    if passed
                    else _first_line(completed.stderr or completed.stdout or f"退出码 {completed.returncode}", 500)
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                completed = None
                passed = False
                reason = str(exc)

        if passed:
            self._clear_runtime_failure(provider.name)
        else:
            self._mark_runtime_failure(provider.name, reason)
        health = self.health(force=True)
        health["live_probe"] = {
            "status": "passed" if passed else "failed",
            "provider": provider.name,
            "reason": reason,
            "timeout_seconds": timeout,
            "student_data_sent": False,
        }
        return health

    def _command(self, provider: Provider, task: dict) -> list[str]:
        prompt = str(task["prompt"])
        entry = Path(task["entry_dir"]).resolve()
        working = Path(task.get("working_dir") or entry).resolve()
        request_path = Path(task["request_path"]).resolve() if task.get("request_path") else None
        if provider.name == "codex":
            return [
                provider.command[0],
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only" if task.get("probe") else "workspace-write",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--color",
                "never",
                "-C",
                str(working),
                prompt,
            ]
        if provider.name == "claude":
            tokens = [
                provider.command[0],
                "--print",
                "--permission-mode",
                "plan" if task.get("probe") else "acceptEdits",
                "--no-session-persistence",
                "--safe-mode",
                "--strict-mcp-config",
            ]
            model_config = task.get("model_config")
            if isinstance(model_config, dict):
                model = str(model_config.get("model", "")).strip()
                if model:
                    tokens.extend(["--model", model])
            tokens.append(prompt)
            return tokens
        if provider.mode == "legacy-command":
            replacements = {
                "{entry}": str(entry),
                "{entry_id}": entry.name,
                "{prompt}": prompt,
                "{request}": str(request_path) if request_path else "",
            }
            tokens = list(provider.command)
            for source, target in replacements.items():
                tokens = [token.replace(source, target) for token in tokens]
            return tokens
        return list(provider.command)

    def _provider_environment(self, provider: Provider, environ: dict[str, str] | None = None) -> dict[str, str]:
        env_source = self.environ if environ is None else environ
        keys = set(BASE_ENV_KEYS)
        if provider.name == "codex":
            prefixes: tuple[str, ...] = ("CODEX_", "OPENAI_")
        elif provider.name == "claude":
            prefixes = ("CLAUDE_", "ANTHROPIC_")
        elif provider.name == "openai-compatible":
            prefixes = ("TEACHER_CONSOLE_AGENT_API_", "TEACHER_CONSOLE_AGENT_ALLOW_REMOTE")
        else:
            prefixes = ("TEACHER_CONSOLE_AGENT_ADAPTER_",)
        keys.update(name for name in env_source if any(name.startswith(prefix) for prefix in prefixes))
        extra = env_source.get("TEACHER_CONSOLE_AGENT_ENV_ALLOWLIST", "")
        keys.update(name.strip() for name in extra.split(",") if name.strip())
        return {name: str(env_source[name]) for name in keys if name in env_source}

    @staticmethod
    def _snapshot(entry: Path) -> dict[str, str]:
        state: dict[str, str] = {}
        if not entry.exists():
            return state
        for path in sorted(entry.rglob("*")):
            relative = str(path.relative_to(entry))
            if path.is_symlink():
                state[relative] = f"symlink:{os.readlink(path)}"
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                state[relative] = digest.hexdigest()
        return state

    @staticmethod
    def _changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
        return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))

    @staticmethod
    def _allowed(relative: str, patterns: list[str], denied: list[str] | None = None) -> bool:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            return False
        normalized = path.as_posix()
        for pattern in denied or []:
            if fnmatch.fnmatchcase(normalized, pattern):
                return False
            if pattern.endswith("/**") and normalized.startswith(pattern[:-3].rstrip("/") + "/"):
                return False
        for pattern in patterns:
            if fnmatch.fnmatchcase(normalized, pattern):
                return True
            if pattern.endswith("/**") and normalized.startswith(pattern[:-3].rstrip("/") + "/"):
                return True
        return False

    def _apply_proposals(self, entry: Path, payload: dict, allowed: list[str], denied: list[str]) -> list[str]:
        entry_root = entry.resolve()
        raw_files = payload.get("files", [])
        if isinstance(raw_files, dict):
            files = [{"path": path, "content": content} for path, content in raw_files.items()]
        elif isinstance(raw_files, list):
            files = raw_files
        else:
            raise ValueError("adapter files must be an object or array")
        proposals: list[tuple[Path, str]] = []
        for item in files:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("path"), str)
                or not isinstance(item.get("content"), str)
            ):
                raise ValueError("each adapter file needs string path and content")
            relative = item["path"]
            if not self._allowed(relative, allowed, denied):
                raise PermissionError(f"adapter proposed disallowed file: {relative}")
            target = (entry_root / relative).resolve()
            target.relative_to(entry_root)
            proposals.append((target, item["content"]))
        for target, content in proposals:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.agent-tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        return [str(target.relative_to(entry_root)) for target, _content in proposals]

    @classmethod
    def _copy_entry(cls, source: Path, target: Path, input_paths: list[str], hidden: list[str]) -> None:
        """Build a regular-file-only, explicit input view for a provider."""
        target.mkdir(parents=True)
        ignored_names = {".visualization-previous", "publication-draft", "publication-assets", "__pycache__"}
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            if any(part in ignored_names or part.startswith(".visualization-build-") for part in Path(relative).parts):
                continue
            if not cls._allowed(relative, input_paths, hidden):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)

    @staticmethod
    def _copy_context(task: dict, staging: Path) -> None:
        for relative, raw_source in task.get("context_files", {}).items():
            target = (staging / relative).resolve()
            target.relative_to(staging.resolve())
            source = Path(raw_source).resolve()
            if not source.is_file() or source.is_symlink():
                raise FileNotFoundError(f"Agent context file unavailable: {source}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for relative, payload in task.get("context_payloads", {}).items():
            if not str(relative).startswith(".agent-context/"):
                raise PermissionError(f"inline Agent context must stay under .agent-context/: {relative}")
            context_root = (staging / ".agent-context").resolve()
            target = (staging / str(relative)).resolve()
            try:
                target.relative_to(context_root)
            except ValueError as exc:
                raise PermissionError(f"inline Agent context escaped .agent-context/: {relative}") from exc
            if isinstance(payload, str):
                content = payload
            elif isinstance(payload, (dict, list)):
                content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            else:
                raise TypeError(f"unsupported inline Agent context payload: {relative}")
            if len(content) > 100_000:
                raise ValueError(f"inline Agent context exceeds 100000 characters: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _hide_paths(staging: Path, hidden: list[str]) -> None:
        for relative in hidden:
            target = (staging / relative).resolve()
            target.relative_to(staging.resolve())
            if target.is_file() or target.is_symlink():
                target.unlink()

    @staticmethod
    def _restore_paths(entry: Path, staging: Path, hidden: list[str]) -> None:
        for relative in hidden:
            source = (entry / relative).resolve()
            source.relative_to(entry.resolve())
            if not source.is_file() or source.is_symlink():
                continue
            target = (staging / relative).resolve()
            target.relative_to(staging.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    @staticmethod
    def _promote(entry: Path, staging: Path, changed: list[str]) -> None:
        prepared: list[tuple[Path, Path]] = []
        backups: dict[Path, bytes | None] = {}
        for relative in changed:
            source = (staging / relative).resolve()
            source.relative_to(staging.resolve())
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"Agent 不得删除文件或提交符号链接：{relative}")
            target = (entry / relative).resolve()
            target.relative_to(entry.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.agent-promote")
            temporary.write_bytes(source.read_bytes())
            prepared.append((temporary, target))
            backups[target] = target.read_bytes() if target.is_file() and not target.is_symlink() else None
        promoted: list[Path] = []
        try:
            for temporary, target in prepared:
                temporary.replace(target)
                promoted.append(target)
        except Exception:
            for target in reversed(promoted):
                previous = backups[target]
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(previous)
            raise
        finally:
            for temporary, _target in prepared:
                temporary.unlink(missing_ok=True)

    def run(self, task: dict, validator: Callable[[Path, list[str]], list[str]] | None = None) -> dict:
        required = {"schema_version", "id", "kind", "entry_dir", "prompt", "allowed_paths", "input_paths"}
        missing = sorted(required - task.keys())
        if missing:
            raise ValueError(f"agent task missing fields: {', '.join(missing)}")
        if task.get("schema_version") != 1:
            raise ValueError("unsupported agent task schema")
        routing_tier = str(task.get("routing_tier", "auto")).strip().lower() or "auto"
        if routing_tier not in ROUTING_TIERS:
            raise ValueError(f"unsupported routing tier: {routing_tier}")
        task = dict(task)
        task["routing_tier"] = routing_tier
        route_id = task.get("id", "?")
        logger.info(
            "gateway task=%s kind=%s entry=%s routing=%s",
            route_id,
            task.get("kind"),
            task.get("entry_id"),
            routing_tier,
        )
        model_config = task.get("model_config") if isinstance(task.get("model_config"), dict) else {}
        model_metadata = {
            key: str(model_config.get(source, "")).strip()
            for key, source in (("model_id", "id"), ("model_display_name", "display_name"))
            if str(model_config.get(source, "")).strip()
        }
        evidence_context = task.get("evidence_context")
        if isinstance(evidence_context, dict):
            try:
                reference_count = int(evidence_context.get("reference_count", 0))
            except (TypeError, ValueError):
                reference_count = 0
            model_metadata["evidence_context"] = {
                "status": str(evidence_context.get("status", "unavailable"))[:40],
                "reference_count": max(0, min(reference_count, 20)),
                "task_type": str(evidence_context.get("task_type", task.get("kind", "")))[:80],
            }
        entry = Path(task["entry_dir"]).resolve()
        if not entry.is_dir():
            raise FileNotFoundError(entry)
        allowed = [str(item) for item in task["allowed_paths"]]
        denied = [str(item) for item in task.get("denied_paths", [])]
        hidden = [str(item) for item in task.get("hidden_paths", [])]
        input_paths = [str(item) for item in task["input_paths"]]
        task_environ = self._task_environ(task)
        requested = task_environ.get("TEACHER_CONSOLE_AGENT_PROVIDER", "auto").strip() or "auto"
        candidates = [item for item in self._ordered(self.providers(environ=task_environ), requested) if item.available]
        api_base = task_environ.get("TEACHER_CONSOLE_AGENT_API_BASE_URL", "").strip()
        if api_base and not _is_loopback_url(api_base) and task.get("allow_remote") is not True:
            candidates = [item for item in candidates if item.name != "openai-compatible"]
        if not candidates:
            result = {
                "status": "unavailable",
                "provider": None,
                "routing_tier": routing_tier,
                "attempts": [],
                "changed_files": [],
                "unauthorized_changes": [],
                "message": "没有可用的 Agent provider；任务可保留为人工处理。",
                **model_metadata,
            }
            result["failure_type"] = classify_agent_failure(result)
            logger.info("gateway task=%s status=unavailable providers=0", route_id)
            return result

        provider_names = [p.name for p in candidates]
        logger.info("gateway task=%s status=routing candidates=%s", route_id, provider_names)
        canonical_before = self._snapshot(entry)
        workspace_parent = Path(task.get("workspace_root") or entry.parent.parent / ".cache" / "agent-workspaces")
        workspace_parent.mkdir(parents=True, exist_ok=True)
        attempts: list[dict] = []
        task_timeout = max(10, min(int(task.get("timeout_seconds", 1800)), 1800))
        try:
            configured_timeout = int(self.environ.get("TEACHER_CONSOLE_AGENT_ATTEMPT_TIMEOUT_SECONDS", "600"))
        except ValueError:
            configured_timeout = 600
        timeout = min(task_timeout, max(30, min(configured_timeout, 1800)))
        with tempfile.TemporaryDirectory(prefix=f"{task['id']}-", dir=workspace_parent) as workspace_name:
            staging = Path(workspace_name) / entry.name
            self._copy_entry(entry, staging, input_paths, hidden)
            self._hide_paths(staging, hidden)
            self._copy_context(task, staging)
            runtime_task = dict(task)
            runtime_task["entry_dir"] = str(staging)
            runtime_task["working_dir"] = str(staging)
            runtime_task["prompt"] = (
                str(task["prompt"]).replace(str(task["entry_dir"]), str(staging)).replace(str(entry), str(staging))
            )
            if task.get("request_path"):
                request_path = Path(task["request_path"]).resolve()
                try:
                    runtime_task["request_path"] = str(staging / request_path.relative_to(entry))
                except ValueError:
                    runtime_task["request_path"] = str(request_path)
            runtime_task_for_child = self._task_without_secrets(runtime_task)
            before = self._snapshot(staging)
            for provider in candidates:
                self._hide_paths(staging, hidden)
                command = self._command(provider, runtime_task_for_child)
                try:
                    completed = self.run_process(
                        command,
                        cwd=str(staging),
                        text=True,
                        # Always close child stdin. Recent Codex builds otherwise
                        # try to collect "additional input" from a long-lived
                        # teacher-console terminal after accepting the positional
                        # prompt, which can leave a background job apparently
                        # running forever.
                        input=json.dumps(runtime_task_for_child, ensure_ascii=False)
                        if provider.mode == "json-adapter"
                        else "",
                        capture_output=True,
                        check=False,
                        timeout=timeout,
                        env=self._provider_environment(provider, task_environ),
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    self._mark_runtime_failure(provider.name, str(exc))
                    attempt = {"provider": provider.name, "status": "failed", "error": str(exc)[:1000]}
                    attempt["failure_type"] = classify_agent_failure(attempt)
                    attempts.append(attempt)
                    continue

                payload: dict = {}
                parse_error = ""
                if provider.mode == "json-adapter" and completed.returncode == 0:
                    try:
                        payload = json.loads(completed.stdout)
                        if not isinstance(payload, dict):
                            raise ValueError("adapter output is not an object")
                        if payload.get("status") == "completed":
                            self._apply_proposals(staging, payload, allowed, denied)
                    except (ValueError, PermissionError, json.JSONDecodeError) as exc:
                        parse_error = str(exc)

                after = self._snapshot(staging)
                changed = self._changed(before, after)
                unauthorized = [name for name in changed if not self._allowed(name, allowed, denied)]
                deleted = [name for name in changed if name in before and name not in after]
                adapter_ok = provider.mode != "json-adapter" or (
                    not parse_error and payload.get("status") == "completed"
                )
                changed_enough = not task.get("requires_change") or bool(changed)
                succeeded = (
                    completed.returncode == 0 and adapter_ok and not unauthorized and not deleted and changed_enough
                )
                validation_errors: list[str] = []
                if succeeded and validator:
                    self._restore_paths(entry, staging, hidden)
                    validation_errors = validator(staging, changed)
                    succeeded = not validation_errors
                canonical_changed = self._changed(canonical_before, self._snapshot(entry))
                if canonical_changed:
                    succeeded = False
                    unauthorized.extend(f"canonical:{name}" for name in canonical_changed)
                attempt: dict[str, Any] = {
                    "provider": provider.name,
                    "status": "completed" if succeeded else "failed",
                    "returncode": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-2000:],
                    "changed_files": changed,
                    "unauthorized_changes": unauthorized,
                    "validation_errors": validation_errors,
                    "requires_change": bool(task.get("requires_change")),
                }
                if parse_error:
                    attempt["error"] = str(parse_error)
                    attempt["parse_error"] = True
                if deleted:
                    attempt["error"] = "Agent 不得删除文件：" + ", ".join(deleted)
                # Structured telemetry from JSON adapter payload
                if payload and isinstance(payload, dict):
                    raw_usage = payload.get("usage")
                    if isinstance(raw_usage, dict):
                        usage_clean = {k: v for k, v in raw_usage.items() if isinstance(v, int) and v >= 0}
                        if usage_clean:
                            attempt["token_usage"] = usage_clean
                    adapter_msg = payload.get("message", "")
                    if isinstance(adapter_msg, str) and adapter_msg.strip():
                        attempt["adapter_message"] = adapter_msg.strip()[:2000]
                attempt["failure_type"] = classify_agent_failure(attempt)
                attempts.append(attempt)

                # --- zero-token script repair for correctable failures ---
                if not succeeded and changed and not canonical_changed and validation_errors:
                    from script_repair import apply_script_repairs  # noqa: E402

                    fix_descriptions = apply_script_repairs(staging, entry, validation_errors, attempt)
                    if fix_descriptions:
                        after_fix = self._snapshot(staging)
                        fix_changed = self._changed(before, after_fix)
                        re_errors = validator(staging, fix_changed) if validator else []
                        if not re_errors:
                            succeeded = True
                            changed = fix_changed
                            attempt["status"] = "completed"
                            attempt["validation_errors"] = []
                            attempt["script_repair"] = fix_descriptions
                            attempt.pop("failure_type", None)
                # --- end script repair ---

                if succeeded:
                    self._clear_runtime_failure(provider.name)
                    self._promote(entry, staging, changed)
                    result = {
                        "status": "completed",
                        "provider": provider.name,
                        "routing_tier": routing_tier,
                        "message": str(payload.get("message", "")) if payload else _first_line(completed.stdout, 1000),
                        "returncode": completed.returncode,
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-2000:],
                        "changed_files": changed,
                        "unauthorized_changes": [],
                        "validation_errors": [],
                        "attempts": attempts,
                        **model_metadata,
                    }
                    if payload:
                        metadata = self._adapter_metadata(payload)
                        result.update(metadata)
                    return result
                if parse_error or unauthorized or deleted:
                    self._mark_runtime_failure(
                        provider.name, parse_error or "; ".join(unauthorized) or attempt.get("error", "候选越权")
                    )
                elif completed.returncode != 0 and not changed:
                    self._mark_runtime_failure(
                        provider.name, completed.stderr or completed.stdout or f"退出码 {completed.returncode}"
                    )
                if changed or canonical_changed:
                    result = {
                        "status": "failed",
                        "provider": provider.name,
                        "routing_tier": routing_tier,
                        "message": "Agent 候选未通过范围或内容校验，canonical 条目未提升本轮候选。",
                        "returncode": completed.returncode,
                        "stdout": completed.stdout[-4000:],
                        "stderr": completed.stderr[-2000:],
                        "changed_files": changed,
                        "unauthorized_changes": unauthorized,
                        "validation_errors": validation_errors,
                        "attempts": attempts,
                        **model_metadata,
                    }
                    result["failure_type"] = classify_agent_failure(result)
                    logger.info(
                        "gateway task=%s status=failed reason=validation changed=%s unauthorized=%s",
                        route_id,
                        len(changed),
                        len(unauthorized),
                    )
                    return result

        last = attempts[-1] if attempts else {}
        result = {
            "status": "failed",
            "provider": last.get("provider"),
            "routing_tier": routing_tier,
            "message": "所有 Agent provider 均在修改文件前失败；任务已安全停止。",
            "returncode": last.get("returncode"),
            "stdout": last.get("stdout", ""),
            "stderr": last.get("stderr", last.get("error", "")),
            "changed_files": [],
            "unauthorized_changes": [],
            "attempts": attempts,
            **model_metadata,
        }
        result["failure_type"] = str(last.get("failure_type") or classify_agent_failure(result))
        logger.info("gateway task=%s status=failed reason=exhausted attempts=%d", route_id, len(attempts))
        return result

    @staticmethod
    def _adapter_metadata(payload: dict) -> dict:
        metadata: dict = {}
        for key in ("model", "model_tier", "requested_tier", "routing_notice"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                metadata[key] = value.strip()[:500]
        raw_usage = payload.get("usage")
        if isinstance(raw_usage, dict):
            usage = {}
            for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens"):
                value = raw_usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[key] = value
            if usage:
                metadata["usage"] = usage
        return metadata
