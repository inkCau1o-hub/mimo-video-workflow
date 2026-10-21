from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from production_schema import load_json, save_json
from .security import redact


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobCancelledError(RuntimeError):
    pass


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    project_id: str = ""
    activity_id: str = ""
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    logs: List[str] = field(default_factory=list)
    cancel_requested: bool = False
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    committed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return redact({
            "job_id": self.job_id,
            "job_type": self.job_type,
            "project_id": self.project_id,
            "activity_id": self.activity_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "logs": list(self.logs),
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        })


class JobContext:
    def __init__(self, manager: "JobManager", record: JobRecord):
        self._manager = manager
        self.record = record

    def update(self, progress: Optional[float] = None, message: str = "") -> None:
        self._manager.update(self.record.job_id, progress=progress, message=message)

    def log(self, message: str) -> None:
        self._manager.log(self.record.job_id, message)

    def is_cancelled(self) -> bool:
        return self._manager.is_cancel_requested(self.record.job_id)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelledError("job cancelled")

    def commit(self, action: Callable[[], Any]) -> Any:
        with self._manager._lock:
            self.raise_if_cancelled()
            result = action()
            self.record.committed = True
            return result


class JobManager:
    def __init__(self, run_inline: bool = False, storage_path: Optional[str | Path] = None):
        self.run_inline = bool(run_inline)
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.RLock()
        self.storage_path = Path(storage_path) if storage_path else None
        if self.storage_path and self.storage_path.exists():
            try:
                for item in load_json(self.storage_path).get("jobs", [])[-100:]:
                    record = JobRecord(**item)
                    if record.status in {"queued", "running"}:
                        record.status = "failed"
                        record.error = "服务已重启，未完成的任务需要重新提交。"
                        record.message = "interrupted"
                    self._jobs[record.job_id] = record
            except (OSError, ValueError, TypeError):
                # A broken history must not prevent opening production projects.
                pass
            self._persist()

    def _persist(self) -> None:
        if self.storage_path:
            records = list(self._jobs.values())[-100:]
            save_json({"jobs": [record.to_dict() for record in records]}, self.storage_path)

    def submit(
        self,
        job_type: str,
        project_id: str,
        func: Callable[[JobContext], Any],
        run_inline: Optional[bool] = None,
        activity_id: str = "",
    ) -> JobRecord:
        record = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:10]}",
            job_type=job_type,
            project_id=project_id,
            activity_id=activity_id,
        )
        with self._lock:
            self._jobs[record.job_id] = record
            self._persist()

        should_run_inline = self.run_inline if run_inline is None else bool(run_inline)
        if should_run_inline:
            self._run(record.job_id, func)
        else:
            thread = threading.Thread(target=self._run, args=(record.job_id, func), daemon=True)
            thread.start()
        return self.get(record.job_id)

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"job not found: {job_id}")
            return self._jobs[job_id]

    def list(self) -> List[JobRecord]:
        with self._lock:
            return list(self._jobs.values())

    def cancel(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self.get(job_id)
            if record.committed or record.status in {"succeeded", "failed", "cancelled"}:
                return record
            record.cancel_requested = True
            record.logs.append("cancel requested")
            record.message = "cancelling"
            if record.status == "queued":
                record.status = "cancelled"
                record.message = "cancelled"
            record.updated_at = _now_iso()
            self._persist()
            return record

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return self.get(job_id).cancel_requested

    def update(self, job_id: str, progress: Optional[float] = None, message: str = "") -> None:
        with self._lock:
            record = self.get(job_id)
            if progress is not None:
                record.progress = max(0.0, min(100.0, float(progress)))
            if message:
                record.message = redact(message)
            record.updated_at = _now_iso()
            self._persist()

    def log(self, job_id: str, message: str) -> None:
        with self._lock:
            record = self.get(job_id)
            record.logs.append(redact(str(message)))
            record.logs = record.logs[-200:]
            record.updated_at = _now_iso()
            self._persist()

    def _run(self, job_id: str, func: Callable[[JobContext], Any]) -> None:
        record = self.get(job_id)
        with self._lock:
            if record.cancel_requested:
                record.status = "cancelled"
                record.message = "cancelled"
                record.updated_at = _now_iso()
                self._persist()
                return
            record.status = "running"
            record.message = "running"
            record.updated_at = _now_iso()
            self._persist()

        context = JobContext(self, record)
        try:
            context.raise_if_cancelled()
            result = func(context)
            context.raise_if_cancelled()
        except JobCancelledError as exc:
            with self._lock:
                record.status = "cancelled"
                record.error = redact(str(exc))
                record.message = "cancelled"
                record.updated_at = _now_iso()
                self._persist()
            return
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.error = redact(str(exc))
                record.logs.append(f"{type(exc).__name__}: {record.error}")
                record.message = "failed"
                record.updated_at = _now_iso()
                self._persist()
            return

        with self._lock:
            if record.cancel_requested:
                record.status = "cancelled"
                record.message = "cancelled"
            else:
                record.status = "succeeded"
                record.progress = 100.0
                record.message = "succeeded"
                record.result = redact(result)
            record.updated_at = _now_iso()
            self._persist()
