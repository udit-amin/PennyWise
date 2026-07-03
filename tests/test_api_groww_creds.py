"""Per-user Groww credential flow: encrypted storage, token resolution,
snapshot provider, and the strict no-fallback behavior."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pennywise.api import groww_creds
from pennywise.snapshot import Snapshot, stamp_now


@pytest.fixture(autouse=True)
def _fresh_fernet(monkeypatch):
    monkeypatch.delenv("PENNYWISE_CRED_KEY", raising=False)
    groww_creds._fernet.cache_clear()
    yield
    groww_creds._fernet.cache_clear()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── resolve_groww_token ───────────────────────────────────────────────


def test_unlinked_user_raises(fake_db, test_user):
    with pytest.raises(groww_creds.GrowwNotLinked):
        groww_creds.resolve_groww_token(test_user)


def test_explicit_token_wins(fake_db, test_user, monkeypatch):
    fake_db.set_user_groww_credentials(
        test_user["user_id"], groww_creds.encrypt_credentials({"token": "T-EXPLICIT"})
    )
    monkeypatch.setattr(
        "pennywise.connectors.groww.exchange_for_access_token",
        lambda *a, **k: pytest.fail("must not exchange when a token is stored"),
    )
    assert groww_creds.resolve_groww_token(fake_db.get_user(test_user["user_id"])) == "T-EXPLICIT"


def test_key_secret_exchanges_and_caches(fake_db, test_user, monkeypatch):
    fake_db.set_user_groww_credentials(
        test_user["user_id"],
        groww_creds.encrypt_credentials({"api_key": "K", "api_secret": "S"}),
    )
    calls = []
    monkeypatch.setattr(
        "pennywise.connectors.groww.exchange_for_access_token",
        lambda key, secret, **k: calls.append((key, secret)) or "T-MINTED",
    )
    user = fake_db.get_user(test_user["user_id"])
    assert groww_creds.resolve_groww_token(user) == "T-MINTED"
    assert calls == [("K", "S")]

    # Token cache persisted, encrypted (not the raw token).
    stored = fake_db.get_user(test_user["user_id"])
    assert stored["groww_token_cache_enc"] != "T-MINTED"
    assert stored["groww_token_expires_at"]


def test_fresh_cached_token_reused(fake_db, test_user, monkeypatch):
    fake_db.set_user_groww_credentials(
        test_user["user_id"],
        groww_creds.encrypt_credentials({"api_key": "K", "api_secret": "S"}),
    )
    fake_db.cache_user_groww_token(
        test_user["user_id"],
        groww_creds._fernet().encrypt(b"T-CACHED").decode(),
        _iso(datetime.now(timezone.utc) + timedelta(hours=3)),
    )
    monkeypatch.setattr(
        "pennywise.connectors.groww.exchange_for_access_token",
        lambda *a, **k: pytest.fail("must not exchange while the cache is fresh"),
    )
    assert groww_creds.resolve_groww_token(fake_db.get_user(test_user["user_id"])) == "T-CACHED"


def test_stale_cached_token_reexchanged(fake_db, test_user, monkeypatch):
    fake_db.set_user_groww_credentials(
        test_user["user_id"],
        groww_creds.encrypt_credentials({"api_key": "K", "api_secret": "S"}),
    )
    fake_db.cache_user_groww_token(
        test_user["user_id"],
        groww_creds._fernet().encrypt(b"T-OLD").decode(),
        _iso(datetime.now(timezone.utc) - timedelta(hours=1)),
    )
    monkeypatch.setattr(
        "pennywise.connectors.groww.exchange_for_access_token", lambda *a, **k: "T-NEW"
    )
    assert groww_creds.resolve_groww_token(fake_db.get_user(test_user["user_id"])) == "T-NEW"


def test_legacy_plaintext_credentials_migrated(fake_db, test_user):
    user = fake_db.get_user(test_user["user_id"])
    user["groww_credentials"] = {"groww_api_key": "K", "groww_api_secret": "S"}

    creds = groww_creds._load_credentials(user)
    assert creds == {"api_key": "K", "api_secret": "S"}

    migrated = fake_db.get_user(test_user["user_id"])
    assert "groww_credentials" not in migrated
    assert groww_creds.decrypt_credentials(migrated["groww_credentials_enc"]) == creds


# ── snapshot_provider ─────────────────────────────────────────────────


def _seed_snapshot(fake_db, user_id, *, source, fetched_at):
    fake_db.save_snapshot(user_id, {
        "fetched_at": fetched_at,
        "holdings": [{"symbol": "TCS", "quantity": 5, "avg_price": 3000, "ltp": 4000}],
        "positions": [],
        "source": source,
    })


def test_uploaded_snapshot_never_expires(fake_db, test_user):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _seed_snapshot(fake_db, test_user["user_id"], source="upload", fetched_at=old)
    snap = groww_creds.snapshot_provider(fake_db.get_user(test_user["user_id"]))()
    assert isinstance(snap, Snapshot)
    assert snap.holdings[0]["symbol"] == "TCS"


def test_fresh_groww_snapshot_reused(fake_db, test_user):
    _seed_snapshot(fake_db, test_user["user_id"], source="groww", fetched_at=stamp_now())
    snap = groww_creds.snapshot_provider(fake_db.get_user(test_user["user_id"]))()
    assert snap.holdings[0]["symbol"] == "TCS"


def test_stale_groww_snapshot_without_creds_raises(fake_db, test_user):
    old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    _seed_snapshot(fake_db, test_user["user_id"], source="groww", fetched_at=old)
    with pytest.raises(groww_creds.GrowwNotLinked):
        groww_creds.snapshot_provider(fake_db.get_user(test_user["user_id"]))()


def test_has_portfolio_source(fake_db, test_user):
    user = fake_db.get_user(test_user["user_id"])
    assert groww_creds.has_portfolio_source(user) is False
    _seed_snapshot(fake_db, test_user["user_id"], source="upload", fetched_at=stamp_now())
    assert groww_creds.has_portfolio_source(user) is True


# ── chat tools degrade gracefully without a portfolio ────────────────


def test_tool_impls_not_linked_degrades():
    from pennywise.chat import make_tool_impls

    def _raiser():
        raise groww_creds.GrowwNotLinked()

    impls = make_tool_impls(_raiser)
    for tool in ("get_holdings", "get_risk_metrics", "list_recommendations"):
        result = impls[tool]({})
        assert result["error"] == "groww_not_linked"


def test_tool_impls_use_injected_snapshot():
    from pennywise.chat import make_tool_impls

    snap = Snapshot(
        fetched_at=stamp_now(),
        holdings=[{
            "symbol": "INFY", "quantity": 10, "avg_price": 1400, "ltp": 1600,
            "sector": "IT", "broad_sector": "IT", "industry": "IT Services",
            "market_cap_cr": 600000,
        }],
    )
    impls = make_tool_impls(lambda: snap)
    result = impls["get_holdings"]({})
    assert result["count"] == 1
    assert result["holdings"][0]["symbol"] == "INFY"


# ── API surface ───────────────────────────────────────────────────────


def test_save_credentials_encrypts_at_rest(app_client, fake_db, test_user, auth_headers, monkeypatch):
    monkeypatch.setattr(
        "pennywise.connectors.groww.exchange_for_access_token", lambda *a, **k: "T-OK"
    )
    resp = app_client.post(
        "/api/auth/groww-credentials",
        json={"api_key": "K", "api_secret": "SENSITIVE-S"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    stored = fake_db.get_user(test_user["user_id"])
    blob = stored["groww_credentials_enc"]
    assert "SENSITIVE-S" not in blob
    assert groww_creds.decrypt_credentials(blob) == {"api_key": "K", "api_secret": "SENSITIVE-S"}


def test_save_credentials_rejects_bad_creds(app_client, auth_headers, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("groww said no")

    monkeypatch.setattr("pennywise.connectors.groww.exchange_for_access_token", _boom)
    resp = app_client.post(
        "/api/auth/groww-credentials",
        json={"api_key": "K", "api_secret": "BAD"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "groww said no" not in resp.text  # internals don't leak


def test_save_credentials_empty_body_rejected(app_client, auth_headers):
    resp = app_client.post("/api/auth/groww-credentials", json={}, headers=auth_headers)
    assert resp.status_code == 422


def test_status_endpoint(app_client, fake_db, test_user, auth_headers):
    resp = app_client.get("/api/auth/groww-credentials/status", headers=auth_headers)
    assert resp.json() == {"linked": False, "source": None, "as_of": None}

    _seed_snapshot(fake_db, test_user["user_id"], source="upload", fetched_at="2026-07-01T00:00:00Z")
    resp = app_client.get("/api/auth/groww-credentials/status", headers=auth_headers)
    assert resp.json() == {"linked": True, "source": "upload", "as_of": "2026-07-01T00:00:00Z"}


def test_portfolio_endpoints_409_when_unlinked(app_client, auth_headers):
    resp = app_client.get("/api/portfolio/holdings", headers=auth_headers)
    assert resp.status_code == 409
    assert "upload" in resp.json()["detail"]

    resp = app_client.post("/api/recommendations", json={"focus": "all"}, headers=auth_headers)
    assert resp.status_code == 409


def test_portfolio_endpoints_require_auth(app_client):
    assert app_client.get("/api/portfolio/holdings").status_code in (401, 403)
    assert app_client.post("/api/recommendations", json={"focus": "all"}).status_code in (401, 403)
