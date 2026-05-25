"""Persistent credential store for PennyWise.

Credentials are kept in ``~/.pennywise/credentials.json``.  The file is
readable only by the current user (mode 0o600).  Token exchange and
auto-refresh happen here so the rest of the codebase just calls
``get_groww_token()``.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def credentials_path() -> Path:
    return Path.home() / ".pennywise" / "credentials.json"


def load() -> dict:
    p = credentials_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    p = credentials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(p)
    # restrict to owner-only read/write
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def update(**fields: Any) -> None:
    data = load()
    data.update(fields)
    save(data)


# ── Groww ─────────────────────────────────────────────────────────────


def get_groww_token() -> str | None:
    """Return a valid Groww access token, auto-refreshing when expired.

    Priority:
      1. Cached daily token in credentials.json (if still fresh)
      2. Re-exchange using stored api_key + api_secret
      3. ``None`` — caller falls back to env vars
    """
    creds = load()

    token = creds.get("groww_access_token")
    expires_at_raw = creds.get("groww_token_expires_at")
    if token and expires_at_raw:
        try:
            exp = datetime.fromisoformat(expires_at_raw)
            if datetime.now(timezone.utc) < exp:
                return token
        except ValueError:
            pass

    api_key = creds.get("groww_api_key")
    api_secret = creds.get("groww_api_secret")
    if api_key and api_secret:
        from pennywise.connectors.groww import exchange_for_access_token
        new_token = exchange_for_access_token(api_key, api_secret)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=23)).isoformat()
        update(groww_access_token=new_token, groww_token_expires_at=expires_at)
        return new_token

    return None


def set_groww_credentials(
    api_key: str,
    api_secret: str,
    *,
    access_token: str,
) -> None:
    """Persist Groww API key, secret, and freshly-minted access token."""
    update(
        groww_api_key=api_key,
        groww_api_secret=api_secret,
        groww_access_token=access_token,
        groww_token_expires_at=(
            datetime.now(timezone.utc) + timedelta(hours=23)
        ).isoformat(),
    )


# ── Google ────────────────────────────────────────────────────────────


def set_google_credentials(
    *,
    email: str,
    name: str | None,
    picture: str | None,
    access_token: str,
    refresh_token: str | None,
    id_token: str | None,
    expires_in: int,
) -> None:
    """Persist Google OAuth tokens."""
    update(
        google_email=email,
        google_name=name,
        google_picture=picture,
        google_access_token=access_token,
        google_refresh_token=refresh_token,
        google_id_token=id_token,
        google_token_expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat(),
    )


def get_google_email() -> str | None:
    return load().get("google_email")


def get_google_id_token() -> str | None:
    """Return the stored Google ID token (used to mint PennyWise JWTs)."""
    return load().get("google_id_token")
