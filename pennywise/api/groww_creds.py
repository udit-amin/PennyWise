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
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable

from cryptography.fernet import Fernet

logger = logging.getLogger("pennywise.api.groww")

SNAPSHOT_MAX_AGE_S = 2 * 60 * 60  # matches agents.portfolio_manager


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


# ── Per-user token resolution ─────────────────────────────────────────


def _load_credentials(user: dict) -> dict:
    """Decrypt the user's stored Groww credentials, lazily migrating any
    legacy plaintext ``groww_credentials`` map to ciphertext."""
    from pennywise.api import db

    blob = user.get("groww_credentials_enc")
    if blob:
        return decrypt_credentials(blob)

    legacy = user.get("groww_credentials")
    if legacy:
        creds = {k.removeprefix("groww_"): v for k, v in dict(legacy).items()}
        db.set_user_groww_credentials(user["user_id"], encrypt_credentials(creds))
        logger.info("migrated legacy plaintext Groww credentials", extra={"user_id": user["user_id"]})
        return creds

    raise GrowwNotLinked("No Groww credentials stored for this user.")


def _cached_token(user: dict) -> str | None:
    """Return the user's cached daily token if still fresh, else None."""
    blob = user.get("groww_token_cache_enc")
    expires_at = user.get("groww_token_expires_at")
    if not blob or not expires_at:
        return None
    try:
        if datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at):
            return None
    except ValueError:
        return None
    try:
        return _fernet().decrypt(blob.encode()).decode()
    except Exception:
        return None  # key rotated or corrupt — re-exchange below


def resolve_groww_token(user: dict) -> str:
    """Resolve a usable Groww access token for this user, from (in order):
    an explicit stored token, the cached daily token, or a fresh
    api_key/api_secret exchange (cached until 6AM IST expiry).

    NEVER falls back to env vars or ~/.pennywise — those are CLI-only.
    Raises GrowwNotLinked when the user has no stored credentials.
    """
    from pennywise.api import db
    from pennywise.connectors.groww import exchange_for_access_token
    from pennywise.credentials import _next_groww_expiry

    creds = _load_credentials(user)

    if creds.get("token"):
        return creds["token"]

    cached = _cached_token(user)
    if cached:
        return cached

    api_key, api_secret = creds.get("api_key"), creds.get("api_secret")
    if not (api_key and api_secret):
        raise GrowwNotLinked("Stored Groww credentials are incomplete — re-link your account.")

    token = exchange_for_access_token(api_key, api_secret)
    expires_at = _next_groww_expiry().isoformat()
    db.cache_user_groww_token(
        user["user_id"], _fernet().encrypt(token.encode()).decode(), expires_at
    )
    return token


# ── Per-user snapshot resolution ──────────────────────────────────────


def _snapshot_is_fresh(item: dict) -> bool:
    from pennywise.snapshot import Snapshot

    if item.get("source") == "upload":
        return True  # uploads can't auto-refresh; valid until replaced
    return Snapshot.from_dict(item).age_seconds() <= SNAPSHOT_MAX_AGE_S


def has_portfolio_source(user: dict) -> bool:
    """Cheap check: does this user have any way to produce a portfolio?"""
    from pennywise.api import db

    if user.get("groww_credentials_enc") or user.get("groww_credentials"):
        return True
    return db.load_snapshot(user["user_id"]) is not None


def snapshot_provider(user: dict) -> Callable[[], "object"]:
    """Return a zero-arg callable producing this user's tagged Snapshot.

    Resolution: stored per-user snapshot (uploads always valid, Groww-synced
    valid for SNAPSHOT_MAX_AGE_S) → rebuild from the user's own Groww
    credentials → GrowwNotLinked. Sync — call from a worker thread.
    """
    from pennywise.api import db
    from pennywise.connectors.groww import GrowwConnector
    from pennywise.snapshot import Snapshot
    from pennywise.tagging import build_snapshot

    def _get() -> Snapshot:
        item = db.load_snapshot(user["user_id"])
        if item and item.get("fetched_at") and _snapshot_is_fresh(item):
            return Snapshot.from_dict(item)

        token = resolve_groww_token(user)  # raises GrowwNotLinked
        with GrowwConnector(token=token) as connector:
            snap = build_snapshot(connector=connector)
        db.save_snapshot(user["user_id"], {
            "fetched_at": snap.fetched_at,
            "holdings": snap.holdings,
            "positions": snap.positions,
            "source": "groww",
        })
        return snap

    return _get
