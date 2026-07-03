"""Shared fixtures for API integration tests.

Everything is opt-in (nothing autouse), so existing unit tests are untouched.

The routes reach DynamoDB exclusively through module functions on
``pennywise.api.db`` looked up at call time (``db.get_user(...)``), so
monkeypatching attributes on that module intercepts every caller — no moto,
no dynamodb-local.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeDB:
    """In-memory stand-in for pennywise.api.db, mirroring its signatures."""

    def __init__(self) -> None:
        self.users: dict[str, dict] = {}
        self.sessions: dict[tuple[str, str], dict] = {}
        self.snapshots: dict[str, dict] = {}
        self.jobs: dict[tuple[str, str], dict] = {}
        self.cache: dict[str, dict] = {}
        self.counters: dict[str, int] = {}

    # ── users ──────────────────────────────────────────────────────────
    def create_user(self, email, name=None, picture=None):
        for user in self.users.values():
            if user["email"] == email:
                user.update(name=name, picture=picture, updated_at=_now())
                return user
        user_id = str(uuid.uuid4())
        user = {
            "user_id": user_id, "email": email, "name": name,
            "picture": picture, "created_at": _now(), "updated_at": _now(),
            "settings": {},
        }
        self.users[user_id] = user
        return user

    def get_user(self, user_id):
        return self.users.get(user_id)

    def set_user_groww_credentials(self, user_id, enc_blob):
        user = self.users[user_id]
        user["groww_credentials_enc"] = enc_blob
        for key in ("groww_credentials", "groww_token_cache_enc", "groww_token_expires_at"):
            user.pop(key, None)

    def cache_user_groww_token(self, user_id, enc_token, expires_at):
        self.users[user_id]["groww_token_cache_enc"] = enc_token
        self.users[user_id]["groww_token_expires_at"] = expires_at

    # ── sessions ───────────────────────────────────────────────────────
    def save_session(self, user_id, session_id, data):
        from pennywise.api.db import _truncate_history

        self.sessions[(user_id, session_id)] = {
            "user_id": user_id,
            "session_id": session_id,
            "history": _truncate_history(list(data.get("history", []))),
            "model": data.get("model", ""),
            "started_at": data.get("started_at", ""),
            "updated_at": _now(),
            "last_user_message": data.get("last_user_message", ""),
        }

    def load_session(self, user_id, session_id):
        return self.sessions.get((user_id, session_id))

    def list_sessions(self, user_id, limit=20):
        rows = [s for (uid, _), s in self.sessions.items() if uid == user_id]
        rows.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return rows[:limit]

    def delete_session(self, user_id, session_id):
        self.sessions.pop((user_id, session_id), None)

    # ── snapshots ──────────────────────────────────────────────────────
    def save_snapshot(self, user_id, snapshot_dict):
        self.snapshots[user_id] = {
            "user_id": user_id,
            "sk": "LATEST",
            **snapshot_dict,
        }

    def load_snapshot(self, user_id):
        return self.snapshots.get(user_id)

    # ── jobs ───────────────────────────────────────────────────────────
    def create_job(self, user_id, job_type, params=None):
        job_id = str(uuid.uuid4())
        self.jobs[(user_id, job_id)] = {
            "user_id": user_id, "job_id": job_id, "job_type": job_type,
            "params": params or {}, "status": "pending",
            "result": None, "error": None, "created_at": _now(),
        }
        return job_id

    def update_job(self, user_id, job_id, *, status, result=None, error=None, **extra):
        job = self.jobs[(user_id, job_id)]
        job.update(status=status, result=result, error=error, updated_at=_now())
        job.update({k: v for k, v in extra.items() if v is not None})

    def touch_job(self, user_id, job_id):
        self.jobs[(user_id, job_id)]["heartbeat_at"] = _now()

    def list_stale_jobs(self, stale_after_s):
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)
        stale = []
        for job in self.jobs.values():
            if job["status"] not in ("running", "pending"):
                continue
            last = job.get("heartbeat_at") or job.get("created_at") or ""
            try:
                if datetime.fromisoformat(last) < cutoff:
                    stale.append(job)
            except ValueError:
                stale.append(job)
        return stale

    def fail_job_if_still_running(self, user_id, job_id, error):
        job = self.jobs.get((user_id, job_id))
        if job and job["status"] in ("running", "pending"):
            job.update(status="failed", error=error, updated_at=_now())
            return True
        return False

    def get_job(self, user_id, job_id):
        return self.jobs.get((user_id, job_id))

    # ── cache / rate counters ──────────────────────────────────────────
    def cache_get(self, key):
        item = self.cache.get(key)
        if not item:
            return None
        if item.get("ttl", float("inf")) < datetime.now(timezone.utc).timestamp():
            return None
        return item

    def cache_put(self, key, data, ttl_seconds=3600):
        self.cache[key] = {
            "cache_key": key, "data": data, "fetched_at": _now(),
            "ttl": int(datetime.now(timezone.utc).timestamp()) + ttl_seconds,
        }

    def incr_rate_counter(self, scope, user_id, window_start, ttl_s):
        key = f"rl#{scope}#{user_id}#{window_start}"
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    # ── infra ──────────────────────────────────────────────────────────
    def ping(self):
        return None

    def create_tables_if_not_exist(self):
        return None


_PATCHED_FUNCS = [
    "create_user", "get_user", "set_user_groww_credentials", "cache_user_groww_token",
    "save_session", "load_session", "list_sessions", "delete_session",
    "save_snapshot", "load_snapshot",
    "create_job", "update_job", "touch_job", "list_stale_jobs",
    "fail_job_if_still_running", "get_job",
    "cache_get", "cache_put", "incr_rate_counter",
    "ping", "create_tables_if_not_exist",
]


@pytest.fixture
def fake_db(monkeypatch):
    from pennywise.api import db

    fake = FakeDB()
    for name in _PATCHED_FUNCS:
        monkeypatch.setattr(db, name, getattr(fake, name))
    return fake


@pytest.fixture
def app_client(fake_db, monkeypatch):
    """TestClient over a freshly-built app wired to the fake db, in dev env."""
    monkeypatch.setenv("PENNYWISE_ENV", "dev")
    monkeypatch.delenv("DYNAMODB_ENDPOINT", raising=False)

    from fastapi.testclient import TestClient

    from pennywise.api.app import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def test_user(fake_db):
    return fake_db.create_user("test@example.com", name="Test User")


@pytest.fixture
def auth_headers(test_user):
    from pennywise.api.auth import create_jwt

    token = create_jwt(test_user["user_id"], test_user["email"])
    return {"Authorization": f"Bearer {token}"}
