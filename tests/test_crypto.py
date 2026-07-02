"""Tests for the per-user Groww credential encryption (pennywise.api.groww_creds)."""
from __future__ import annotations

import pytest

from pennywise.api import groww_creds


@pytest.fixture(autouse=True)
def _fresh_fernet(monkeypatch):
    """Each test gets a clean cached-Fernet slate."""
    groww_creds._fernet.cache_clear()
    yield
    groww_creds._fernet.cache_clear()


def test_encrypt_decrypt_round_trip(monkeypatch):
    monkeypatch.delenv("PENNYWISE_CRED_KEY", raising=False)
    creds = {"api_key": "gk_live_abc", "api_secret": "shh-secret"}
    blob = groww_creds.encrypt_credentials(creds)
    assert groww_creds.decrypt_credentials(blob) == creds


def test_ciphertext_does_not_contain_plaintext(monkeypatch):
    monkeypatch.delenv("PENNYWISE_CRED_KEY", raising=False)
    blob = groww_creds.encrypt_credentials({"api_key": "SUPERSECRETVALUE"})
    assert "SUPERSECRETVALUE" not in blob


def test_explicit_key_is_used(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PENNYWISE_CRED_KEY", key)
    blob = groww_creds.encrypt_credentials({"a": 1})
    # Decryptable with the raw key directly → proves the env key was used.
    assert Fernet(key.encode()).decrypt(blob.encode()) == b'{"a": 1}'


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_prod_like_requires_key(monkeypatch, env):
    monkeypatch.setenv("PENNYWISE_ENV", env)
    monkeypatch.delenv("PENNYWISE_CRED_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PENNYWISE_CRED_KEY"):
        groww_creds.validate_crypto_config()


def test_dev_derives_key_from_jwt_secret(monkeypatch):
    monkeypatch.setenv("PENNYWISE_ENV", "dev")
    monkeypatch.delenv("PENNYWISE_CRED_KEY", raising=False)
    groww_creds.validate_crypto_config()  # should not raise


def test_malformed_key_fails_at_validate(monkeypatch):
    monkeypatch.setenv("PENNYWISE_ENV", "dev")
    monkeypatch.setenv("PENNYWISE_CRED_KEY", "not-a-fernet-key")
    with pytest.raises(Exception):
        groww_creds.validate_crypto_config()
