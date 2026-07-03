"""OAuth security: CSRF state, redirect_uri allowlist, email-claim checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from pennywise.api import auth as auth_module


@pytest.fixture
def google_configured(monkeypatch):
    monkeypatch.setattr("pennywise.api.routes.auth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(auth_module, "GOOGLE_CLIENT_ID", "test-client-id")


@pytest.fixture
def fake_exchange(monkeypatch):
    """Stub the Google code→profile exchange (no network)."""

    async def _exchange(code, redirect_uri):
        return {"email": "oauth@example.com", "name": "OAuth User", "picture": None}

    monkeypatch.setattr("pennywise.api.routes.auth.exchange_google_code", _exchange)


# ── state token unit behavior ─────────────────────────────────────────


def test_state_round_trip():
    state = auth_module.create_oauth_state()
    auth_module.verify_oauth_state(state)  # should not raise


@pytest.mark.parametrize("bad", [None, "", "garbage", "a.b.c"])
def test_bad_state_rejected(bad):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        auth_module.verify_oauth_state(bad)
    assert exc.value.status_code == 400


def test_expired_state_rejected():
    from fastapi import HTTPException

    expired = jwt.encode(
        {
            "purpose": "oauth_state",
            "nonce": "n",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        auth_module.JWT_SECRET,
        algorithm=auth_module.JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException):
        auth_module.verify_oauth_state(expired)


def test_regular_jwt_not_accepted_as_state(fake_db, test_user):
    """A session JWT signs with the same secret but must not pass as state."""
    from fastapi import HTTPException

    session_token = auth_module.create_jwt(test_user["user_id"], test_user["email"])
    with pytest.raises(HTTPException):
        auth_module.verify_oauth_state(session_token)


# ── email claim ───────────────────────────────────────────────────────


def test_missing_email_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        auth_module._profile_from_id_token({"name": "No Email"})
    assert exc.value.status_code == 401


def test_unverified_email_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        auth_module._profile_from_id_token({"email": "a@b.c", "email_verified": False})


def test_verified_email_accepted():
    profile = auth_module._profile_from_id_token(
        {"email": "a@b.c", "email_verified": True, "name": "A"}
    )
    assert profile == {"email": "a@b.c", "name": "A", "picture": None}


# ── /google/url + POST callback (JSON flow) ───────────────────────────


def test_google_url_returns_state_and_validates_redirect(app_client):
    resp = app_client.get(
        "/api/auth/google/url",
        params={"redirect_uri": "http://localhost:3000/auth/callback"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert f"state={body['state']}" in body["url"].replace("%3D", "=").replace("&amp;", "&") or body["state"] in body["url"]

    resp = app_client.get(
        "/api/auth/google/url",
        params={"redirect_uri": "https://evil.example.com/steal"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "redirect_uri is not allowed."


def test_post_callback_requires_state(app_client):
    resp = app_client.post(
        "/api/auth/google/callback",
        json={"code": "x", "redirect_uri": "http://localhost:3000/auth/callback"},
    )
    assert resp.status_code == 422  # state is a required field


def test_post_callback_rejects_bad_state(app_client, fake_exchange):
    resp = app_client.post(
        "/api/auth/google/callback",
        json={
            "code": "x",
            "redirect_uri": "http://localhost:3000/auth/callback",
            "state": "forged",
        },
    )
    assert resp.status_code == 400


def test_post_callback_rejects_bad_redirect_uri(app_client, fake_exchange):
    resp = app_client.post(
        "/api/auth/google/callback",
        json={
            "code": "x",
            "redirect_uri": "https://evil.example.com/steal",
            "state": auth_module.create_oauth_state(),
        },
    )
    assert resp.status_code == 400


def test_post_callback_happy_path(app_client, fake_db, fake_exchange):
    resp = app_client.post(
        "/api/auth/google/callback",
        json={
            "code": "good-code",
            "redirect_uri": "http://localhost:3000/auth/callback",
            "state": auth_module.create_oauth_state(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "oauth@example.com"
    assert body["access_token"]
    # User actually created.
    assert any(u["email"] == "oauth@example.com" for u in fake_db.users.values())


# ── browser flow: /google/start → /google/callback ───────────────────


def test_start_redirects_with_state_cookie(app_client, google_configured):
    resp = app_client.get("/api/auth/google/start", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://accounts.google.com/")
    assert "state=" in resp.headers["location"]
    assert "pw_oauth_state" in resp.cookies


def test_browser_callback_rejects_missing_cookie(app_client, google_configured, fake_exchange):
    state = auth_module.create_oauth_state()
    app_client.cookies.clear()
    resp = app_client.get("/api/auth/google/callback", params={"code": "x", "state": state})
    assert resp.status_code == 400
    assert "mismatch" in resp.text


def test_browser_callback_rejects_state_cookie_mismatch(app_client, google_configured, fake_exchange):
    app_client.get("/api/auth/google/start", follow_redirects=False)  # sets cookie
    other_state = auth_module.create_oauth_state()
    resp = app_client.get(
        "/api/auth/google/callback", params={"code": "x", "state": other_state}
    )
    assert resp.status_code == 400


def test_browser_callback_happy_path(app_client, fake_db, google_configured, fake_exchange):
    start = app_client.get("/api/auth/google/start", follow_redirects=False)
    state = start.cookies["pw_oauth_state"]
    resp = app_client.get(
        "/api/auth/google/callback", params={"code": "good", "state": state}
    )
    assert resp.status_code == 200
    assert "Signed in!" in resp.text
    assert any(u["email"] == "oauth@example.com" for u in fake_db.users.values())
