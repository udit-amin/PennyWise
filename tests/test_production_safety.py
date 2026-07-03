"""Tests for production-safety guards: fail-closed auth config and rate limits."""
from __future__ import annotations

import importlib

import pytest

import pennywise.api.auth as auth
import pennywise.api.ratelimit as ratelimit
import pennywise.config as config


# ── fail-closed auth config ───────────────────────────────────────────


def test_dev_allows_default_secret(monkeypatch):
    """Dev keeps the convenient defaults — no exception."""
    monkeypatch.setenv("PENNYWISE_ENV", "dev")
    monkeypatch.setattr(auth, "JWT_SECRET", auth._DEV_JWT_SECRET)
    auth.validate_auth_config()  # should not raise


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_prod_rejects_default_jwt_secret(monkeypatch, env):
    monkeypatch.setenv("PENNYWISE_ENV", env)
    monkeypatch.setattr(auth, "JWT_SECRET", auth._DEV_JWT_SECRET)
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "secret")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.validate_auth_config()


def test_prod_rejects_missing_google_creds(monkeypatch):
    monkeypatch.setenv("PENNYWISE_ENV", "prod")
    monkeypatch.setattr(auth, "JWT_SECRET", "a-real-strong-secret")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "")
    with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
        auth.validate_auth_config()


def test_prod_passes_with_good_config(monkeypatch):
    monkeypatch.setenv("PENNYWISE_ENV", "prod")
    monkeypatch.setattr(auth, "JWT_SECRET", "a-real-strong-secret")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "id")
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_SECRET", "secret")
    auth.validate_auth_config()  # should not raise


def test_default_model_is_current(monkeypatch):
    """The code default model id should be the current Opus, not the stale one.
    (A local .env may override PENNYWISE_LLM_MODEL; we assert the built-in.)"""
    monkeypatch.delenv("PENNYWISE_LLM_MODEL", raising=False)
    assert config.load().llm_model == "claude-opus-4-8"


# ── chat-turn rate limit ──────────────────────────────────────────────


def test_chat_turn_rate_limit(monkeypatch, fake_db):
    # Re-import with a small window so the test is fast and isolated. The
    # counter itself lives in (fake) DynamoDB — shared across workers.
    monkeypatch.setenv("PENNYWISE_CHAT_TURNS_PER_WINDOW", "3")
    importlib.reload(ratelimit)
    try:
        assert ratelimit.allow_chat_turn("user-x") is True
        assert ratelimit.allow_chat_turn("user-x") is True
        assert ratelimit.allow_chat_turn("user-x") is True
        assert ratelimit.allow_chat_turn("user-x") is False  # 4th over limit
        # A different user is unaffected.
        assert ratelimit.allow_chat_turn("user-y") is True
    finally:
        monkeypatch.delenv("PENNYWISE_CHAT_TURNS_PER_WINDOW", raising=False)
        importlib.reload(ratelimit)
