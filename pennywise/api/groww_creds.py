"""Per-user Groww credential handling for the API.

Credentials submitted via ``POST /api/auth/groww-credentials`` are encrypted
at rest (Fernet: AES-128-CBC + HMAC-SHA256) in the users table. The key comes
from ``PENNYWISE_CRED_KEY`` — an AWS Secrets Manager secret in deployed
environments. Dev derives a deterministic key from ``JWT_SECRET`` so
docker-compose works with no extra configuration.

Server code paths NEVER fall back to ``GROWW_API_TOKEN`` env vars or
``~/.pennywise/credentials.json`` — those chains are CLI-only. A user with no
linked Groww account and no uploaded snapshot raises :class:`GrowwNotLinked`,
which the API maps to generic no-portfolio behavior, not a shared default.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from functools import lru_cache

from cryptography.fernet import Fernet


class GrowwNotLinked(Exception):
    """The user has no portfolio source: no Groww credentials and no upload."""


def _derived_dev_key() -> str:
    from pennywise.api.auth import JWT_SECRET

    digest = hashlib.sha256(f"pennywise-cred:{JWT_SECRET}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = os.getenv("PENNYWISE_CRED_KEY") or _derived_dev_key()
    return Fernet(key.encode())


def validate_crypto_config() -> None:
    """Fail closed at startup when the credential key is missing in
    staging/prod (a derived dev key there would tie ciphertext to JWT_SECRET
    rotation and weaken isolation between secrets)."""
    from pennywise import config

    if config.load().is_prod_like and not os.getenv("PENNYWISE_CRED_KEY"):
        raise RuntimeError(
            "Refusing to start in a deployed environment: PENNYWISE_CRED_KEY is "
            "unset. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and store it in Secrets Manager."
        )
    _fernet()  # surface a malformed key at boot, not on the first request


def encrypt_credentials(creds: dict) -> str:
    return _fernet().encrypt(json.dumps(creds).encode()).decode()


def decrypt_credentials(blob: str) -> dict:
    return json.loads(_fernet().decrypt(blob.encode()))
