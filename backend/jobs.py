from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    project_id: str = ""
    status: str = "queued"
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    logs: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "job_type": self.job_type,
            "project_id": self.project_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "logs": list(self.logs),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobContext:
    def __init__(self, manager: "JobManager", record: JobRecord):
        self._manager = manager
        self.record = record

    def update(self, progress: Optional[float] = None, message: str = "") -> None:
        self._manager.update(self.record.job_id, progress=progress, message=message)

    def log(self, message: str) -> None:
        self._manager.log(self.record.job_id, message)


class JobManager:
    def __init__(self, run_inline: bool = False):
        self.run_inline = bool(run_inline)
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        job_type: str,
        project_id: str,
        func: Callable[[JobContext], Any],
        run_inline: Optional[bool] = None,
    ) -> JobRecord:
        record = JobRecord(
            job_id=f"job_{uuid.uuid4().hex[:10]}",
            job_type=job_type,
            project_id=project_id,
        )
        with self._lock:
            self._jobs[record.job_id] = record

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

    def update(self, job_id: str, progress: Optional[float] = None, message: str = "") -> None:
        with self._lock:
            record = self.get(job_id)
            if progress is not None:
                record.progress = max(0.0, min(100.0, float(progress)))
            if message:
                record.message = message
            record.updated_at = _now_iso()

    def log(self, job_id: str, message: str) -> None:
        with self._lock:
            record = self.get(job_id)
            record.logs.append(str(message))
            record.updated_at = _now_iso()

    def _run(self, job_id: str, func: Callable[[JobContext], Any]) -> None:
        record = self.get(job_id)
        with self._lock:
            record.status = "running"
            record.message = "running"
            record.updated_at = _now_iso()

        context = JobContext(self, record)
        try:
            result = func(context)
        except Exception as exc:
            with self._lock:
                record.status = "failed"
                record.error = str(exc)
                record.logs.append(traceback.format_exc())
                record.message = "failed"
                record.updated_at = _now_iso()
            return

        with self._lock:
            record.status = "succeeded"
            record.progress = 100.0
            record.message = "succeeded"
            record.result = result
            record.updated_at = _now_iso()
