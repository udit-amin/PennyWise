"""Tests for the credentials store and GrowwConnector credential lookup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import pennywise.credentials as creds_mod


# ── credentials.py ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_creds(tmp_path, monkeypatch):
    """Redirect the credentials file into a temp dir for every test."""
    monkeypatch.setattr(
        creds_mod,
        "credentials_path",
        lambda: tmp_path / ".pennywise" / "credentials.json",
    )


def test_load_returns_empty_dict_when_missing():
    assert creds_mod.load() == {}


def test_save_and_load_roundtrip():
    creds_mod.save({"foo": "bar", "n": 42})
    assert creds_mod.load() == {"foo": "bar", "n": 42}


def test_update_merges_fields():
    creds_mod.save({"a": 1})
    creds_mod.update(b=2)
    assert creds_mod.load() == {"a": 1, "b": 2}


def test_get_groww_token_returns_none_when_empty():
    assert creds_mod.get_groww_token() is None


def test_get_groww_token_returns_cached_when_fresh():
    future = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
    creds_mod.save({"groww_access_token": "tok123", "groww_token_expires_at": future})
    assert creds_mod.get_groww_token() == "tok123"


def test_get_groww_token_refreshes_when_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    creds_mod.save({
        "groww_access_token": "stale",
        "groww_token_expires_at": past,
        "groww_api_key": "key",
        "groww_api_secret": "secret",
    })
    with patch(
        "pennywise.connectors.groww.exchange_for_access_token",
        return_value="fresh_tok",
    ):
        token = creds_mod.get_groww_token()

    assert token == "fresh_tok"
    # new token should be persisted
    assert creds_mod.load()["groww_access_token"] == "fresh_tok"


def test_set_groww_credentials_persists_all_fields():
    creds_mod.set_groww_credentials("mykey", "mysecret", access_token="acc")
    data = creds_mod.load()
    assert data["groww_api_key"] == "mykey"
    assert data["groww_api_secret"] == "mysecret"
    assert data["groww_access_token"] == "acc"
    assert "groww_token_expires_at" in data


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

    # Patch httpx.Client so no real requests are made
    import httpx
    from pennywise.connectors.groww import GrowwConnector

    with patch.object(httpx, "Client", autospec=True):
        gc = GrowwConnector()

    assert gc.token == "cred_tok"
