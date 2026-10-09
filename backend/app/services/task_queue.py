"""Background work behind a tiny TaskQueue interface (FastAPI BackgroundTasks today; swappable for Redis/RQ).

CPU-heavy processing runs in worker threads, never on the event loop, and a process-wide semaphore caps how
many resumes are analysed at once (MAX_CONCURRENT_JOBS).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from fastapi import BackgroundTasks, FastAPI

from app.core.config import get_settings

log = logging.getLogger(__name__)
_slots = threading.BoundedSemaphore(max(1, get_settings().max_concurrent_jobs))
_executor: ThreadPoolExecutor | None = None


class TaskQueue(Protocol):
    def enqueue(self, fn: Callable[..., Any], *args: Any) -> None: ...


def _limited(fn: Callable[..., Any], *args: Any) -> None:
    with _slots:
        try:
            fn(*args)
        except Exception:  # a failing task must never take the worker down
            log.exception("background task failed", extra={"task": getattr(fn, "__name__", str(fn))})


class BackgroundTasksQueue:
    """Per-request queue backed by FastAPI BackgroundTasks (sync functions run in Starlette's thread pool)."""

    def __init__(self, tasks: BackgroundTasks):
        self.tasks = tasks

    def enqueue(self, fn: Callable[..., Any], *args: Any) -> None:
        self.tasks.add_task(_limited, fn, *args)


class ThreadQueue:
    """Process-wide queue for work that is not tied to a request (e.g. resuming interrupted jobs at startup)."""

    def enqueue(self, fn: Callable[..., Any], *args: Any) -> None:
        global _executor
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max(1, get_settings().max_concurrent_jobs), thread_name_prefix="screener"
            )
        _executor.submit(_limited, fn, *args)


def recover_interrupted(app: FastAPI) -> None:
    """BackgroundTasks do not survive a restart: re-queue resumes left PENDING / PROCESSING."""
    try:
        from app.services.resume_service import pending_resume_ids, process_resume

        ids = pending_resume_ids()
    except Exception as exc:  # database not reachable yet — not fatal for startup
        log.warning("could not check for interrupted resumes", extra={"error": exc.__class__.__name__})
        return
    queue = ThreadQueue()
    for resume_id in ids:
        queue.enqueue(process_resume, resume_id, app.state.resources)
    if ids:
        log.info("re-queued interrupted resumes", extra={"count": len(ids)})
