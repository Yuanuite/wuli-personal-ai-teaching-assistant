#!/usr/bin/env python3
"""Small persistent background-job manager for local Agent tasks."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from agent_outcome import build_agent_request_outcome
from log import logger


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


DEFAULT_KIND_LIMITS = {
    # Source cleanup is an entry-local, low-risk batch operation.  Let uploaded
    # batches fan out while keeping same-entry protection in place.
    "source.clean": 4,
    # Higher-risk jobs can touch answers or shared physics semantics. Their
    # static ceiling is four; adaptive canary/recovery decides the live limit.
    "analysis.generate": 4,
    "answer.revise": 4,
    "visualization.model": 4,
}
DEFAULT_KIND_PRIORITIES = {
    "source.clean": 70,
    "analysis.generate": 60,
    "answer.revise": 80,
    "visualization.model": 50,
}
DEFAULT_PROVIDER_LIMITS: dict[str, int] = {}
DEFAULT_ADAPTIVE_CONCURRENCY = {
    "enabled": True,
    "initial": 1,
    "first_success_limit": 2,
    "max_limit": 4,
    "successes_to_max": 2,
}
STRUCTURAL_FAILURE_TYPES = {
    "provider_timeout",
    "provider_rate_limited",
    "provider_budget_exceeded",
    "provider_unavailable",
    "provider_execution_failed",
    "adapter_protocol_error",
    "structured_output_schema_invalid",
    "worker_interrupted",
    "task_exception",
}


def _clean_positive_int(value, fallback: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(number, maximum))


def default_scheduler_config() -> dict:
    return {
        "schema_version": 2,
        "global_max_running": 6,
        "entry_max_running": 1,
        "kind_limits": dict(DEFAULT_KIND_LIMITS),
        "kind_priorities": dict(DEFAULT_KIND_PRIORITIES),
        "provider_limits": dict(DEFAULT_PROVIDER_LIMITS),
        "adaptive_concurrency": dict(DEFAULT_ADAPTIVE_CONCURRENCY),
    }


def normalize_scheduler_config(raw: dict | None) -> dict:
    base = default_scheduler_config()
    if not isinstance(raw, dict):
        return base
    base["global_max_running"] = _clean_positive_int(
        raw.get("global_max_running"), base["global_max_running"], maximum=16
    )
    base["entry_max_running"] = 1
    for key, value in (raw.get("kind_limits") or {}).items():
        base["kind_limits"][str(key)] = _clean_positive_int(value, base["kind_limits"].get(str(key), 1), maximum=16)
    for key, value in (raw.get("kind_priorities") or {}).items():
        base["kind_priorities"][str(key)] = _clean_positive_int(
            value, base["kind_priorities"].get(str(key), 50), minimum=0, maximum=100
        )
    for key, value in (raw.get("provider_limits") or {}).items():
        base["provider_limits"][str(key)] = _clean_positive_int(value, 1, maximum=16)
    adaptive = raw.get("adaptive_concurrency")
    if isinstance(adaptive, dict):
        base["adaptive_concurrency"]["enabled"] = adaptive.get("enabled") is not False
        base["adaptive_concurrency"]["initial"] = _clean_positive_int(
            adaptive.get("initial"), 1, maximum=4
        )
        base["adaptive_concurrency"]["first_success_limit"] = _clean_positive_int(
            adaptive.get("first_success_limit"), 2, maximum=4
        )
        base["adaptive_concurrency"]["max_limit"] = _clean_positive_int(
            adaptive.get("max_limit"), 4, maximum=16
        )
        base["adaptive_concurrency"]["successes_to_max"] = _clean_positive_int(
            adaptive.get("successes_to_max"), 2, maximum=16
        )
    return base


class AgentJobManager:
    def __init__(
        self,
        directory: Path,
        *,
        max_workers: int | None = None,
        kind_limits: dict[str, int] | None = None,
        scheduler_config: dict | None = None,
    ):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        config = normalize_scheduler_config(scheduler_config)
        if max_workers is not None:
            config["global_max_running"] = _clean_positive_int(max_workers, config["global_max_running"], maximum=16)
        if kind_limits:
            for key, value in kind_limits.items():
                config["kind_limits"][key] = _clean_positive_int(value, config["kind_limits"].get(key, 1), maximum=16)
        self.scheduler_config = config
        self.max_workers = config["global_max_running"]
        self.kind_limits = dict(config["kind_limits"])
        self.kind_priorities = dict(config["kind_priorities"])
        self.provider_limits = dict(config["provider_limits"])
        self.adaptive_config = dict(config["adaptive_concurrency"])
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.active_by_entry: dict[str, str] = {}
        self.running_by_kind: dict[str, int] = {}
        self.running_by_provider: dict[str, int] = {}
        self.running_by_group: dict[str, int] = {}
        self._adaptive_states: dict[str, dict[str, int | str]] = {}
        self.pending_callbacks: dict[str, Callable[[], dict]] = {}
        self.queued_jobs: list[str] = []
        self._task_cooldowns: dict[tuple[str, str], tuple[float, str]] = {}  # (entry_id, kind) → (expires_at, reason)
        self._cooldown_seconds = self._read_cooldown_seconds()
        self._sequence = 0
        self._shutdown = False
        self.workers: list[threading.Thread] = []
        self._recover_interrupted()
        for index in range(self.max_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"teacher-agent-{index + 1}", daemon=True)
            worker.start()
            self.workers.append(worker)

    def _path(self, job_id: str) -> Path:
        if not job_id or Path(job_id).name != job_id:
            raise ValueError("invalid job id")
        return self.directory / f"{job_id}.json"

    def _write(self, record: dict) -> None:
        target = self._path(record["id"])
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(target)

    def _recover_interrupted(self) -> None:
        for path in self.directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("status") in {"queued", "running"}:
                record.update({
                    "status": "failed",
                    "completed_at": now_iso(),
                    "error": "教师工作台在任务完成前重启；请重新提交本轮请求。",
                    "failure_type": "worker_interrupted",
                })
                record["outcome"] = build_agent_request_outcome(
                    {"status": "failed", "failure_type": "worker_interrupted"},
                    record=record,
                )
                self._write(record)

    def submit(self, kind: str, entry_id: str, callback: Callable[[], dict], *, metadata: dict | None = None) -> dict:
        with self.lock:
            if self._shutdown:
                return {"status": "blocked", "errors": ["Agent 调度器正在关闭"]}
            active_id = self.active_by_entry.get(entry_id)
            if active_id:
                active = self.get(active_id)
                if active.get("status") in {"queued", "running"}:
                    return {
                        "status": "blocked",
                        "errors": ["这道题已有 Agent 任务正在运行"],
                        "job": self.public(active),
                    }
            remaining, reason = self._task_cooldown_status(entry_id, kind)
            if remaining > 0:
                return {
                    "status": "blocked",
                    "errors": [f"该任务最近执行失败（{reason}），请等待 {remaining} 秒后重试"],
                    "cooldown_remaining_seconds": remaining,
                    "cooldown_reason": reason,
                }
            job_id = uuid.uuid4().hex
            record = {
                "schema_version": 1,
                "id": job_id,
                "kind": kind,
                "entry_id": entry_id,
                "status": "queued",
                "priority": self._priority(kind, metadata or {}),
                "sequence": self._sequence,
                "created_at": now_iso(),
            }
            if metadata:
                record.update({
                    key: value
                    for key, value in metadata.items()
                    if key in {"routing_tier", "model_id", "concurrency_group", "provider", "batch_id", "route_snapshot"}
                })
            self.active_by_entry[entry_id] = job_id
            self.pending_callbacks[job_id] = callback
            self.queued_jobs.append(job_id)
            self._sequence += 1
            self._write(record)
            self.condition.notify_all()
            logger.info(
                "job=%s kind=%s entry=%s status=queued seq=%d", job_id, kind, entry_id, record.get("sequence", 0)
            )
            return {"status": "queued", "job": self.public(record)}

    def _priority(self, kind: str, metadata: dict) -> int:
        if "priority" in metadata:
            return _clean_positive_int(
                metadata.get("priority"), self.kind_priorities.get(kind, 50), minimum=0, maximum=100
            )
        return int(self.kind_priorities.get(kind, 50))

    def _provider_for(self, record: dict) -> str:
        return str(record.get("provider", "") or "")

    @staticmethod
    def _adaptive_group(record: dict) -> str:
        explicit = str(record.get("concurrency_group", "") or "").strip()
        if explicit:
            return explicit
        batch_id = str(record.get("batch_id", "") or "").strip()
        if batch_id:
            return f"batch:{batch_id}"
        return f"kind:{record.get('kind', '')}"

    def _adaptive_state_locked(self, record: dict) -> dict[str, int | str]:
        group = self._adaptive_group(record)
        state = self._adaptive_states.get(group)
        if state is None:
            state = {
                "limit": int(self.adaptive_config["initial"]),
                "successes_at_two": 0,
                "epoch": 0,
                "mode": "canary",
                "degradation_count": 0,
            }
            self._adaptive_states[group] = state
        return state

    def _adaptive_limit_locked(self, record: dict) -> int:
        if not self.adaptive_config.get("enabled", True):
            return self.max_workers
        state = self._adaptive_state_locked(record)
        return min(
            int(state["limit"]),
            int(self.adaptive_config["max_limit"]),
            self.kind_limits.get(str(record.get("kind", "")), self.max_workers),
        )

    @staticmethod
    def _read_cooldown_seconds() -> int:
        import os

        try:
            seconds = int(os.environ.get("TEACHER_CONSOLE_AGENT_FAILURE_COOLDOWN_SECONDS", "30"))
        except ValueError:
            seconds = 300
        return max(30, min(seconds, 3600))

    def _task_cooldown_status(self, entry_id: str, kind: str) -> tuple[int, str]:
        """Return (remaining_seconds, reason) for a task cooldown, or (0, '') if not in cooldown."""
        key = (entry_id, kind)
        with self.lock:
            value = self._task_cooldowns.get(key)
            if not value:
                return (0, "")
            expires_at, reason = value
            remaining = max(0.0, expires_at - time.monotonic())
            if remaining <= 0:
                self._task_cooldowns.pop(key, None)
                return (0, "")
            return (int(remaining), reason)

    def _set_task_cooldown(self, entry_id: str, kind: str, reason: str) -> None:
        with self.lock:
            self._task_cooldowns[(entry_id, kind)] = (
                time.monotonic() + self._cooldown_seconds,
                reason or "任务失败",
            )

    def _clear_task_cooldown(self, entry_id: str, kind: str) -> None:
        with self.lock:
            self._task_cooldowns.pop((entry_id, kind), None)

    def _can_run_locked(self, record: dict) -> bool:
        kind = str(record.get("kind", ""))
        if self.running_by_kind.get(kind, 0) >= self.kind_limits.get(kind, self.max_workers):
            return False
        group = self._adaptive_group(record)
        if self.running_by_group.get(group, 0) >= self._adaptive_limit_locked(record):
            return False
        provider = self._provider_for(record)
        if provider and self.running_by_provider.get(provider, 0) >= self.provider_limits.get(
            provider, self.max_workers
        ):
            return False
        return True

    def _next_runnable_locked(self) -> str | None:
        self.queued_jobs = [job_id for job_id in self.queued_jobs if job_id in self.pending_callbacks]
        candidates: list[tuple[int, int, str]] = []
        for job_id in self.queued_jobs:
            try:
                record = self.get(job_id)
            except FileNotFoundError:
                continue
            if record.get("status") != "queued" or not self._can_run_locked(record):
                continue
            candidates.append((-int(record.get("priority", 50)), int(record.get("sequence", 0)), job_id))
        if not candidates:
            return None
        candidates.sort()
        job_id = candidates[0][2]
        self.queued_jobs.remove(job_id)
        return job_id

    def _worker_loop(self) -> None:
        while True:
            with self.condition:
                while not self._shutdown:
                    job_id = self._next_runnable_locked()
                    if job_id:
                        callback = self.pending_callbacks.pop(job_id)
                        self._mark_running_locked(job_id)
                        break
                    self.condition.wait(0.5)
                else:
                    return
            self._run(job_id, callback)

    def _mark_running_locked(self, job_id: str) -> None:
        record = self.get(job_id)
        kind = str(record.get("kind", ""))
        provider = self._provider_for(record)
        group = self._adaptive_group(record)
        state = self._adaptive_state_locked(record)
        self.running_by_kind[kind] = self.running_by_kind.get(kind, 0) + 1
        self.running_by_group[group] = self.running_by_group.get(group, 0) + 1
        if provider:
            self.running_by_provider[provider] = self.running_by_provider.get(provider, 0) + 1
        record.update({
            "status": "running",
            "started_at": now_iso(),
            "adaptive_concurrency": {
                "group": group,
                "limit_at_start": int(state["limit"]),
                "epoch": int(state["epoch"]),
                "mode": str(state["mode"]),
            },
        })
        self._write(record)

    def _update_adaptive_state_locked(self, record: dict) -> None:
        if not self.adaptive_config.get("enabled", True):
            return
        adaptive = record.get("adaptive_concurrency")
        if not isinstance(adaptive, dict):
            return
        group = str(adaptive.get("group", "") or "")
        state = self._adaptive_states.get(group)
        if not state:
            return
        before = int(state["limit"])
        event = ""
        failure_type = str(record.get("failure_type", "") or "")
        if record.get("status") == "failed" and failure_type in STRUCTURAL_FAILURE_TYPES:
            state["limit"] = 1
            state["successes_at_two"] = 0
            state["epoch"] = int(state["epoch"]) + 1
            state["mode"] = "serial_probe"
            state["degradation_count"] = int(state["degradation_count"]) + 1
            event = "structural_failure_degraded"
        elif (
            record.get("status") == "completed"
            and int(adaptive.get("epoch", -1)) == int(state["epoch"])
        ):
            if before == 1:
                state["limit"] = min(
                    int(self.adaptive_config["first_success_limit"]),
                    int(self.adaptive_config["max_limit"]),
                )
                state["successes_at_two"] = 0
                state["mode"] = "recovering" if str(state["mode"]) == "serial_probe" else "ramp_up"
                event = "serial_probe_succeeded" if str(adaptive.get("mode")) == "serial_probe" else "canary_succeeded"
            elif before == int(self.adaptive_config["first_success_limit"]):
                state["successes_at_two"] = int(state["successes_at_two"]) + 1
                if int(state["successes_at_two"]) >= int(self.adaptive_config["successes_to_max"]):
                    state["limit"] = int(self.adaptive_config["max_limit"])
                    state["mode"] = "full"
                    state["successes_at_two"] = 0
                    event = "consecutive_successes_ramped"
        if event:
            record["scheduler_event"] = {
                "event": event,
                "group": group,
                "concurrency_before": before,
                "concurrency_after": int(state["limit"]),
                "failure_type": failure_type or None,
                "degradation_count": int(state["degradation_count"]),
                "recorded_at": now_iso(),
            }

    def _run(self, job_id: str, callback: Callable[[], dict]) -> None:
        record = self.get(job_id)
        running_provider = self._provider_for(record)
        logger.info("job=%s kind=%s entry=%s status=running", job_id, record.get("kind"), record.get("entry_id"))
        with self.lock:
            self._write(record)
        try:
            result = callback()
            record.update({
                "status": "completed"
                if result.get("status") not in {"failed", "error", "blocked", "awaiting-agent", "unavailable"}
                else "failed",
                "provider": str(result.get("provider", "") or "").strip() or record.get("provider", ""),
                "completed_at": now_iso(),
                "result": result,
            })
            if record["status"] == "failed":
                failure_type = str(result.get("failure_type", "") or "").strip()
                if failure_type:
                    record["failure_type"] = failure_type
                record["error"] = (
                    result.get("message_to_teacher")
                    or result.get("message")
                    or "; ".join(result.get("errors", []))
                    or "Agent 任务未完成"
                )
            record["outcome"] = build_agent_request_outcome(result, record=record)
        except Exception as exc:  # noqa: BLE001
            record.update({
                "status": "failed",
                "completed_at": now_iso(),
                "error": str(exc),
                "failure_type": "task_exception",
            })
            record["outcome"] = build_agent_request_outcome(
                {"status": "failed", "failure_type": "task_exception"},
                record=record,
            )
        finally:
            with self.lock:
                kind = str(record.get("kind", ""))
                provider = self._provider_for(record)
                group = self._adaptive_group(record)
                self.running_by_kind[kind] = max(0, self.running_by_kind.get(kind, 0) - 1)
                self.running_by_group[group] = max(0, self.running_by_group.get(group, 0) - 1)
                if running_provider:
                    self.running_by_provider[running_provider] = max(
                        0, self.running_by_provider.get(running_provider, 0) - 1
                    )
                self._update_adaptive_state_locked(record)
                self._write(record)
                if self.active_by_entry.get(record["entry_id"]) == job_id:
                    self.active_by_entry.pop(record["entry_id"], None)
                # Per-task cooldown: block retry of same (entry, kind) after failure;
                # clear on success so the user can re-submit if needed.
                entry_id = str(record.get("entry_id", ""))
                if record.get("status") == "failed":
                    failure_type = str(record.get("failure_type", "") or "")
                    error = str(record.get("error", "") or "")[:200]
                    reason = f"{failure_type}: {error}" if failure_type else (error or "Agent 任务未完成")
                    self._task_cooldowns[(entry_id, kind)] = (
                        time.monotonic() + self._cooldown_seconds,
                        reason,
                    )
                elif record.get("status") == "completed":
                    self._task_cooldowns.pop((entry_id, kind), None)
                self.condition.notify_all()
            logger.info("job=%s kind=%s status=%s provider=%s", job_id, kind, record.get("status"), provider or "-")

    def get(self, job_id: str) -> dict:
        path = self._path(job_id)
        if not path.is_file():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def latest_for_entry(self, entry_id: str) -> dict | None:
        records = []
        for path in self.directory.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if record.get("entry_id") == entry_id:
                records.append(record)
        return max(records, key=lambda item: (item.get("created_at", ""), item.get("id", "")), default=None)

    def active_for_entry(self, entry_id: str) -> dict | None:
        with self.lock:
            job_id = self.active_by_entry.get(entry_id)
            if not job_id:
                return None
            record = self.get(job_id)
            return record if record.get("status") in {"queued", "running"} else None

    @staticmethod
    def public(record: dict) -> dict:
        value = {
            key: record.get(key)
            for key in (
                "id",
                "kind",
                "entry_id",
                "routing_tier",
                "model_id",
                "concurrency_group",
                "provider",
                "batch_id",
                "route_snapshot",
                "priority",
                "status",
                "created_at",
                "started_at",
                "completed_at",
                "error",
                "failure_type",
                "outcome",
                "adaptive_concurrency",
                "scheduler_event",
            )
            if record.get(key) is not None
        }
        value["url"] = f"/api/jobs/{record['id']}"
        if "result" in record:
            result = record["result"] if isinstance(record["result"], dict) else {}
            public_keys = {
                "status",
                "provider",
                "message",
                "message_to_teacher",
                "returncode",
                "changed_files",
                "failure_type",
                "unauthorized_changes",
                "validation_errors",
                "resulting_state",
                "errors",
                "state",
                "routing_tier",
                "requested_tier",
                "model_tier",
                "model",
                "usage",
                "routing_notice",
                "evidence_context",
                "failure_repair",
                "budget_guard",
            }
            value["result"] = {key: result[key] for key in public_keys if key in result}
        return value

    def shutdown(self, *, wait: bool = False) -> None:
        if wait:
            deadline = time.time() + 30
            with self.condition:
                while (
                    self.pending_callbacks or any(value > 0 for value in self.running_by_kind.values())
                ) and time.time() < deadline:
                    self.condition.wait(0.1)
        with self.condition:
            self._shutdown = True
            self.condition.notify_all()
        if wait:
            deadline = time.time() + 30
            for worker in self.workers:
                remaining = max(0.0, deadline - time.time())
                worker.join(timeout=remaining)
