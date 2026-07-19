#!/usr/bin/env python3
"""Small persistent background-job manager for local Agent tasks."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


class AgentJobManager:
    def __init__(self, directory: Path, *, max_workers: int = 2):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="teacher-agent")
        self.lock = threading.RLock()
        self.active_by_entry: dict[str, str] = {}
        self._recover_interrupted()

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
                })
                self._write(record)

    def submit(self, kind: str, entry_id: str, callback: Callable[[], dict], *, metadata: dict | None = None) -> dict:
        with self.lock:
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
                "created_at": now_iso(),
            }
            if metadata:
                record.update({key: value for key, value in metadata.items() if key in {"routing_tier"}})
            self.active_by_entry[entry_id] = job_id
            self._write(record)
            self.executor.submit(self._run, job_id, callback)
            return {"status": "queued", "job": self.public(record)}

    def _run(self, job_id: str, callback: Callable[[], dict]) -> None:
        record = self.get(job_id)
        record.update({"status": "running", "started_at": now_iso()})
        with self.lock:
            self._write(record)
        try:
            result = callback()
            record.update({
                "status": "completed" if result.get("status") not in {"failed", "error", "blocked", "awaiting-agent", "unavailable"} else "failed",
                "completed_at": now_iso(),
                "result": result,
            })
            if record["status"] == "failed":
                record["error"] = result.get("message_to_teacher") or result.get("message") or "; ".join(result.get("errors", [])) or "Agent 任务未完成"
        except Exception as exc:  # noqa: BLE001
            record.update({"status": "failed", "completed_at": now_iso(), "error": str(exc)})
        finally:
            with self.lock:
                self._write(record)
                if self.active_by_entry.get(record["entry_id"]) == job_id:
                    self.active_by_entry.pop(record["entry_id"], None)

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
        value = {key: record.get(key) for key in ("id", "kind", "entry_id", "routing_tier", "status", "created_at", "started_at", "completed_at", "error") if record.get(key) is not None}
        value["url"] = f"/api/jobs/{record['id']}"
        if "result" in record:
            result = record["result"] if isinstance(record["result"], dict) else {}
            public_keys = {
                "status", "provider", "message", "message_to_teacher", "returncode", "changed_files",
                "unauthorized_changes", "validation_errors", "resulting_state", "errors", "state",
                "routing_tier", "requested_tier", "model_tier", "model", "usage", "routing_notice",
            }
            value["result"] = {key: result[key] for key in public_keys if key in result}
        return value

    def shutdown(self, *, wait: bool = False) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=True)
