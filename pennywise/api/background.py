"""In-process background jobs, hardened for deploys and crashes.

Simple by design (SQS is the eventual home once >1 task is needed), but no
longer lossy:

* Every running job heartbeats ``heartbeat_at`` to DynamoDB, so a job whose
  process died is distinguishable from one that is merely slow.
* ``reconcile_stale_jobs`` (called at startup) fails jobs whose heartbeat
  went silent — via a conditional write, so a job still running in the old
  task during a rolling deploy is never clobbered, and both uvicorn workers
  can reconcile concurrently without racing.
* A wall-clock timeout marks runaway jobs failed. The underlying thread can
  linger until the fn's own HTTP/LLM timeouts fire (see agents/_llm.py and
  the connector timeouts — those are the backstop); it is not left "running"
  in the eyes of the API.
* Failures store ``TypeName: message`` only; tracebacks go to logs.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Callable

from pennywise.api import db

logger = logging.getLogger("pennywise.api.jobs")

JOB_WORKERS = int(os.getenv("PENNYWISE_JOB_WORKERS", "2"))
JOB_TIMEOUT_S = float(os.getenv("PENNYWISE_JOB_TIMEOUT_S", "600"))
HEARTBEAT_INTERVAL_S = float(os.getenv("PENNYWISE_JOB_HEARTBEAT_S", "30"))
# A job is orphaned if its heartbeat is this stale (>> heartbeat interval).
STALE_AFTER_S = int(os.getenv("PENNYWISE_JOB_STALE_AFTER_S", "300"))

# Module-level thread pool — shared across all background jobs.
_pool = ThreadPoolExecutor(max_workers=JOB_WORKERS, thread_name_prefix="pw-job")


def submit_job(user_id: str, job_id: str, fn: Callable[[], dict]) -> None:
    """Run ``fn`` on the shared pool, tracking status in DynamoDB: 'running'
    immediately (with started_at/heartbeat_at), then 'completed' or 'failed'
    — including failure by timeout."""

    def _wrapper() -> None:
        extra = {"user_id": user_id, "job_id": job_id}
        logger.info("job started", extra=extra)
        now = datetime.now(timezone.utc).isoformat()
        db.update_job(user_id, job_id, status="running", started_at=now, heartbeat_at=now)

        started = time.monotonic()
        inner = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw-job-fn")
        future = inner.submit(fn)
        try:
            while True:
                try:
                    result = future.result(timeout=HEARTBEAT_INTERVAL_S)
                except FuturesTimeoutError:
                    elapsed = time.monotonic() - started
                    if elapsed > JOB_TIMEOUT_S:
                        db.update_job(
                            user_id, job_id, status="failed",
                            error=f"Job timed out after {int(JOB_TIMEOUT_S)}s.",
                        )
                        logger.error("job timed out after %.0fs", elapsed, extra=extra)
                        return
                    try:
                        db.touch_job(user_id, job_id)
                    except Exception:
                        logger.warning("job heartbeat write failed", extra=extra)
                    continue
                except Exception as exc:
                    db.update_job(
                        user_id, job_id, status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    logger.exception("job failed: %s", exc, extra=extra)
                    return

                db.update_job(user_id, job_id, status="completed", result=result)
                logger.info("job completed in %.1fs", time.monotonic() - started, extra=extra)
                return
        finally:
            inner.shutdown(wait=False, cancel_futures=True)

    _pool.submit(_wrapper)


def reconcile_stale_jobs(stale_after_s: int = STALE_AFTER_S) -> int:
    """Fail jobs orphaned by a crash/deploy (stale heartbeat, still marked
    pending/running). Conditional writes make this idempotent and safe while
    an old task is still finishing its jobs. Returns the number reconciled."""
    count = 0
    for job in db.list_stale_jobs(stale_after_s):
        transitioned = db.fail_job_if_still_running(
            job["user_id"], job["job_id"],
            "Job was orphaned by a service restart — please retry.",
        )
        if transitioned:
            count += 1
            logger.warning(
                "reconciled orphaned job",
                extra={"user_id": job["user_id"], "job_id": job["job_id"]},
            )
    return count
