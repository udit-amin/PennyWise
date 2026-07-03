"""Background job hardening: heartbeats, timeouts, orphan reconciliation."""
from __future__ import annotations

import importlib
import time
from datetime import datetime, timedelta, timezone

import pytest

import pennywise.api.background as background


def _wait_for_status(fake_db, user_id, job_id, statuses, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = fake_db.get_job(user_id, job_id)
        if job and job["status"] in statuses:
            return job
        time.sleep(0.02)
    pytest.fail(f"job never reached {statuses}: {fake_db.get_job(user_id, job_id)}")


@pytest.fixture
def fast_background(fake_db, monkeypatch):
    """Reload the module with tiny heartbeat/timeout windows for fast tests."""
    monkeypatch.setenv("PENNYWISE_JOB_HEARTBEAT_S", "0.05")
    monkeypatch.setenv("PENNYWISE_JOB_TIMEOUT_S", "0.5")
    importlib.reload(background)
    yield background
    monkeypatch.delenv("PENNYWISE_JOB_HEARTBEAT_S")
    monkeypatch.delenv("PENNYWISE_JOB_TIMEOUT_S")
    importlib.reload(background)


def test_job_success_records_result_and_timestamps(fake_db, fast_background):
    job_id = fake_db.create_job("u1", "recommendations")
    fast_background.submit_job("u1", job_id, lambda: {"answer": 42})

    job = _wait_for_status(fake_db, "u1", job_id, {"completed"})
    assert job["result"] == {"answer": 42}
    assert job["started_at"]
    assert job["heartbeat_at"]


def test_job_failure_stores_name_and_message_only(fake_db, fast_background):
    def _boom():
        raise ValueError("bad input")

    job_id = fake_db.create_job("u1", "recommendations")
    fast_background.submit_job("u1", job_id, _boom)

    job = _wait_for_status(fake_db, "u1", job_id, {"failed"})
    assert job["error"] == "ValueError: bad input"
    assert "Traceback" not in job["error"]


def test_job_heartbeats_while_running(fake_db, fast_background):
    job_id = fake_db.create_job("u1", "recommendations")
    fast_background.submit_job("u1", job_id, lambda: time.sleep(0.3) or {"ok": True})

    time.sleep(0.15)  # a few heartbeat ticks in
    running = fake_db.get_job("u1", job_id)
    first_beat = running["heartbeat_at"]
    assert running["status"] == "running"
    assert first_beat

    _wait_for_status(fake_db, "u1", job_id, {"completed"})


def test_job_timeout_marks_failed(fake_db, fast_background):
    job_id = fake_db.create_job("u1", "recommendations")
    fast_background.submit_job("u1", job_id, lambda: time.sleep(5) or {})

    job = _wait_for_status(fake_db, "u1", job_id, {"failed"}, timeout_s=3.0)
    assert "timed out" in job["error"]


def test_reconcile_fails_only_orphans(fake_db):
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(minutes=20)).isoformat()
    fresh = now.isoformat()

    orphan_running = fake_db.create_job("u1", "recommendations")
    fake_db.jobs[("u1", orphan_running)].update(status="running", heartbeat_at=stale)

    orphan_pending = fake_db.create_job("u1", "recommendations")
    fake_db.jobs[("u1", orphan_pending)].update(status="pending", created_at=stale)

    alive = fake_db.create_job("u2", "recommendations")
    fake_db.jobs[("u2", alive)].update(status="running", heartbeat_at=fresh)

    done = fake_db.create_job("u2", "recommendations")
    fake_db.jobs[("u2", done)].update(status="completed", heartbeat_at=stale)

    reconciled = background.reconcile_stale_jobs(stale_after_s=300)

    assert reconciled == 2
    assert fake_db.get_job("u1", orphan_running)["status"] == "failed"
    assert "orphaned" in fake_db.get_job("u1", orphan_running)["error"]
    assert fake_db.get_job("u1", orphan_pending)["status"] == "failed"
    assert fake_db.get_job("u2", alive)["status"] == "running"
    assert fake_db.get_job("u2", done)["status"] == "completed"


def test_reconcile_is_idempotent(fake_db):
    stale = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    job_id = fake_db.create_job("u1", "recommendations")
    fake_db.jobs[("u1", job_id)].update(status="running", heartbeat_at=stale)

    assert background.reconcile_stale_jobs(stale_after_s=300) == 1
    assert background.reconcile_stale_jobs(stale_after_s=300) == 0  # nothing left
