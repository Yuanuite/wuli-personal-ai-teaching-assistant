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

from log import logger


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


DEFAULT_KIND_LIMITS = {
    # Source cleanup is an entry-local, low-risk batch operation.  Let uploaded
    # batches fan out while keeping same-entry protection in place.
    "source.clean": 4,
    # Higher-risk jobs can touch answers or shared physics semantics; keep them
    # conservative until their own batch semantics are explicitly designed.
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


def _clean_positive_int(value, fallback: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(minimum, min(number, maximum))


def default_scheduler_config() -> dict:
    return {
        "schema_version": 1,
        "global_max_running": 6,
        "entry_max_running": 1,
        "kind_limits": dict(DEFAULT_KIND_LIMITS),
        "kind_priorities": dict(DEFAULT_KIND_PRIORITIES),
        "provider_limits": dict(DEFAULT_PROVIDER_LIMITS),
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
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.active_by_entry: dict[str, str] = {}
        self.running_by_kind: dict[str, int] = {}
        self.running_by_provider: dict[str, int] = {}
        self.pending_callbacks: dict[str, Callable[[], dict]] = {}
        self.queued_jobs: list[str] = []
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
                    if key in {"routing_tier", "model_id", "concurrency_group", "provider", "batch_id"}
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

    def _can_run_locked(self, record: dict) -> bool:
        kind = str(record.get("kind", ""))
        if self.running_by_kind.get(kind, 0) >= self.kind_limits.get(kind, self.max_workers):
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
        self.running_by_kind[kind] = self.running_by_kind.get(kind, 0) + 1
        if provider:
            self.running_by_provider[provider] = self.running_by_provider.get(provider, 0) + 1
        record.update({"status": "running", "started_at": now_iso()})
        self._write(record)

    def _run(self, job_id: str, callback: Callable[[], dict]) -> None:
        record = self.get(job_id)
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
                record["error"] = (
                    result.get("message_to_teacher")
                    or result.get("message")
                    or "; ".join(result.get("errors", []))
                    or "Agent 任务未完成"
                )
        except Exception as exc:  # noqa: BLE001
            record.update({
                "status": "failed",
                "completed_at": now_iso(),
                "error": str(exc),
                "failure_type": "task_exception",
            })
        finally:
            with self.lock:
                kind = str(record.get("kind", ""))
                provider = self._provider_for(record)
                self.running_by_kind[kind] = max(0, self.running_by_kind.get(kind, 0) - 1)
                if provider:
                    self.running_by_provider[provider] = max(0, self.running_by_provider.get(provider, 0) - 1)
                self._write(record)
                if self.active_by_entry.get(record["entry_id"]) == job_id:
                    self.active_by_entry.pop(record["entry_id"], None)
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
                "priority",
                "status",
                "created_at",
                "started_at",
                "completed_at",
                "error",
                "failure_type",
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
