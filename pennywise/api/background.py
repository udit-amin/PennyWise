"""In-memory background job runner.

For the prototype we use a simple dict + BackgroundTasks. When horizontal
scaling is needed, graduate to SQS.

Each job runs in a thread (the LangGraph workflow is I/O bound, not CPU).
"""
from __future__ import annotations

import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from pennywise.api import db

logger = logging.getLogger("pennywise.api.jobs")

# Module-level thread pool — shared across all background jobs.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pw-job")


def submit_job(user_id: str, job_id: str, fn: Callable[[], dict]) -> None:
    """Run ``fn()`` in the background. Updates the DynamoDB job record
    with 'running' immediately, then 'completed' or 'failed' on finish."""

    def _wrapper():
        extra = {"user_id": user_id, "job_id": job_id}
        logger.info("job started", extra=extra)
        db.update_job(user_id, job_id, status="running")
        started = time.monotonic()
        try:
            result = fn()
            db.update_job(user_id, job_id, status="completed", result=result)
            logger.info("job completed in %.1fs", time.monotonic() - started, extra=extra)
        except Exception as exc:
            db.update_job(user_id, job_id, status="failed", error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            logger.exception("job failed: %s", exc, extra=extra)

    _pool.submit(_wrapper)
