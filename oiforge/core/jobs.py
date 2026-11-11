"""Zadania w tle z postępem i zdarzeniami przyrostowymi (polling z UI przez GET /api/jobs/<id>?since=K)."""
from __future__ import annotations

import concurrent.futures as cf
import os
import threading
import time
import traceback
import uuid

_MAX_JOBS = 300
_pool = cf.ThreadPoolExecutor(max_workers=max(4, min(16, (os.cpu_count() or 4))), thread_name_prefix="job")
_jobs: dict[str, "Job"] = {}
_guard = threading.Lock()


class Job:
    def __init__(self, title: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.status = "running"
        self.progress_value = 0.0
        self.message = ""
        self.events: list = []
        self.result = None
        self.error: str | None = None
        self.trace: str | None = None
        self.cancelled = False
        self.created = time.time()
        self.finished: float | None = None
        self._lock = threading.Lock()

    # --- API dla funkcji zadania
    def progress(self, fraction: float, message: str | None = None) -> None:
        with self._lock:
            self.progress_value = max(0.0, min(1.0, float(fraction)))
            if message is not None:
                self.message = message

    def push(self, event: dict) -> None:
        with self._lock:
            self.events.append(event)

    def log(self, message: str, level: str = "info") -> None:
        self.push({"type": "log", "level": level, "message": message, "t": round(time.time() - self.created, 3)})

    # --- odczyt
    def snapshot(self, since: int = 0) -> dict:
        with self._lock:
            since = max(0, int(since or 0))
            d = {"id": self.id, "title": self.title, "status": self.status, "progress": self.progress_value,
                 "message": self.message, "events": self.events[since:], "next": len(self.events),
                 "elapsed": round((self.finished or time.time()) - self.created, 3)}
            if self.status == "done":
                d["result"] = self.result
            if self.status == "error":
                d["error"] = self.error
                d["trace"] = self.trace
            return d


def _run(job: Job, fn, args, kwargs):
    try:
        res = fn(job, *args, **kwargs)
        with job._lock:
            job.result = res
            job.status = "cancelled" if job.cancelled else "done"
            if job.status == "done":
                job.progress_value = 1.0
    except Exception as e:  # noqa: BLE001
        with job._lock:
            job.status = "error"
            job.error = getattr(e, "message", None) or str(e) or e.__class__.__name__
            job.trace = traceback.format_exc()
    finally:
        job.finished = time.time()


def submit(fn, *args, title: str = "", **kwargs) -> str:
    """Uruchamia fn(job, *args, **kwargs) w tle. Zwraca id zadania."""
    job = Job(title)
    with _guard:
        _jobs[job.id] = job
        if len(_jobs) > _MAX_JOBS:
            for jid in sorted(_jobs, key=lambda k: _jobs[k].created)[: len(_jobs) - _MAX_JOBS]:
                if _jobs[jid].status != "running":
                    _jobs.pop(jid, None)
    _pool.submit(_run, job, fn, args, kwargs)
    return job.id


def get(job_id: str) -> Job | None:
    with _guard:
        return _jobs.get(job_id)


def cancel(job_id: str) -> bool:
    j = get(job_id)
    if not j:
        return False
    j.cancelled = True
    return True


def run_sync(fn, *args, title: str = "", **kwargs):
    """Uruchamia funkcję zadania synchronicznie (przydatne w testach). Zwraca (job, wynik)."""
    job = Job(title)
    _run(job, fn, args, kwargs)
    if job.status == "error":
        raise RuntimeError(job.error)
    return job, job.result
