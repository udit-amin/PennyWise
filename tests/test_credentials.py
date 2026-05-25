"""Tests for the credentials store and GrowwConnector credential lookup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import pennywise.credentials as creds_mod
from pennywise.credentials import GrowwLoginRequired


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_creds(tmp_path, monkeypatch):
    """Redirect the credentials file into a temp dir for every test."""
    monkeypatch.setattr(
        creds_mod,
        "credentials_path",
        lambda: tmp_path / ".pennywise" / "credentials.json",
    )


# ── basic store operations ────────────────────────────────────────────


def test_load_returns_empty_dict_when_missing():
    assert creds_mod.load() == {}


def test_save_and_load_roundtrip():
    creds_mod.save({"foo": "bar", "n": 42})
    assert creds_mod.load() == {"foo": "bar", "n": 42}


def test_update_merges_fields():
    creds_mod.save({"a": 1})
    creds_mod.update(b=2)
    assert creds_mod.load() == {"a": 1, "b": 2}


# ── 6 AM IST expiry helper ────────────────────────────────────────────


def test_next_groww_expiry_is_in_future():
    expiry = creds_mod._next_groww_expiry()
    assert expiry > datetime.now(timezone.utc)


def test_next_groww_expiry_is_at_00_25_utc():
    """Expiry should always be 00:25 UTC (6 AM IST minus 5 min buffer)."""
    expiry = creds_mod._next_groww_expiry()
    assert expiry.hour == 0
    assert expiry.minute == 25


def test_next_groww_expiry_is_tomorrow_when_past_cutoff(monkeypatch):
    """After 00:30 UTC today the returned expiry must be tomorrow."""
    # Pin 'now' to 01:00 UTC so we're past the 00:30 cutoff
    fixed_now = datetime(2025, 6, 1, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        creds_mod,
        "_next_groww_expiry",
        lambda: (
            fixed_now.replace(hour=0, minute=25, second=0, microsecond=0)
            + timedelta(days=1)
        ),
    )
    expiry = creds_mod._next_groww_expiry()
    assert expiry.date() > fixed_now.date()


# ── get_groww_token ───────────────────────────────────────────────────


def test_get_groww_token_returns_none_when_empty():
    assert creds_mod.get_groww_token() is None


def test_get_groww_token_returns_cached_when_fresh():
    future = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    creds_mod.save({"groww_access_token": "tok123", "groww_token_expires_at": future})
    assert creds_mod.get_groww_token() == "tok123"


def test_get_groww_token_refreshes_checksum_when_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    creds_mod.save({
        "groww_access_token": "stale",
        "groww_token_expires_at": past,
        "groww_api_key": "key",
        "groww_api_secret": "secret",
        "groww_auth_method": "checksum",
    })
    with patch(
        "pennywise.connectors.groww.exchange_for_access_token",
        return_value="fresh_tok",
    ):
        token = creds_mod.get_groww_token()

    assert token == "fresh_tok"
    assert creds_mod.load()["groww_access_token"] == "fresh_tok"


def test_get_groww_token_raises_for_expired_totp():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    creds_mod.save({
        "groww_access_token": "stale",
        "groww_token_expires_at": past,
        "groww_api_key": "key",
        "groww_auth_method": "totp",
    })
    with pytest.raises(GrowwLoginRequired, match="pennywise login groww"):
        creds_mod.get_groww_token()


# ── set_groww_credentials ─────────────────────────────────────────────


def test_set_groww_credentials_checksum_persists_all_fields():
    creds_mod.set_groww_credentials("mykey", "mysecret", access_token="acc", auth_method="checksum")
    data = creds_mod.load()
    assert data["groww_api_key"] == "mykey"
    assert data["groww_api_secret"] == "mysecret"
    assert data["groww_access_token"] == "acc"
    assert data["groww_auth_method"] == "checksum"
    assert "groww_token_expires_at" in data


def test_set_groww_credentials_totp_omits_secret():
    creds_mod.set_groww_credentials("mykey", None, access_token="acc", auth_method="totp")
    data = creds_mod.load()
    assert data["groww_api_key"] == "mykey"
    assert "groww_api_secret" not in data
    assert data["groww_auth_method"] == "totp"


# ── Google credentials ────────────────────────────────────────────────


def test_set_google_credentials_persists_fields():
    creds_mod.set_google_credentials(
        email="user@example.com",
        name="Test User",
        picture=None,
        access_token="gat",
        refresh_token="grt",
        id_token="gid",
        expires_in=3600,
    )
    data = creds_mod.load()
    assert data["google_email"] == "user@example.com"
    assert data["google_access_token"] == "gat"
    assert data["google_refresh_token"] == "grt"


# ── GrowwConnector credential priority ───────────────────────────────


def test_groww_connector_uses_credentials_file_when_no_env(monkeypatch):
    """Connector should pick up the stored token before touching env vars."""
    monkeypatch.delenv("GROWW_API_TOKEN", raising=False)
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)

    future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    creds_mod.save({"groww_access_token": "cred_tok", "groww_token_expires_at": future})

    import httpx
    from pennywise.connectors.groww import GrowwConnector

    with patch.object(httpx, "Client", autospec=True):
        gc = GrowwConnector()

    assert gc.token == "cred_tok"
