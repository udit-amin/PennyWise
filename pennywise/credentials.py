"""Persistent credential store for PennyWise.

Credentials are kept in ``~/.pennywise/credentials.json``.  The file is
readable only by the current user (mode 0o600).  Token exchange and
auto-refresh happen here so the rest of the codebase just calls
``get_groww_token()``.

Groww token expiry:
  Daily tokens expire at 6:00 AM IST (00:30 UTC) every day.  We store the
  next 00:30 UTC timestamp minus a 5-minute safety buffer so auto-refresh
  happens slightly before the real expiry.

TOTP vs checksum:
  Checksum auth (api_key + api_secret) can auto-refresh silently — the
  secret is stored and the checksum is re-computed on demand.
  TOTP auth cannot auto-refresh (the 6-digit code rotates every 30 s).
  When a TOTP token expires, ``get_groww_token()`` raises ``GrowwLoginRequired``
  so callers can display a clear re-login message.
"""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class GrowwLoginRequired(RuntimeError):
    """Raised when a TOTP-based Groww token has expired and cannot be silently refreshed."""


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
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def update(**fields: Any) -> None:
    data = load()
    data.update(fields)
    save(data)


# ── Groww expiry helpers ───────────────────────────────────────────────


def _next_groww_expiry() -> datetime:
    """Return the next 6:00 AM IST (00:30 UTC) minus a 5-min buffer."""
    now = datetime.now(timezone.utc)
    # 6:00 AM IST = 00:30 UTC
    expiry_today = now.replace(hour=0, minute=30, second=0, microsecond=0)
    if now >= expiry_today:
        expiry_today += timedelta(days=1)
    return expiry_today - timedelta(minutes=5)


def _token_is_fresh(creds: dict) -> bool:
    token = creds.get("groww_access_token")
    expires_at_raw = creds.get("groww_token_expires_at")
    if not token or not expires_at_raw:
        return False
    try:
        exp = datetime.fromisoformat(expires_at_raw)
        return datetime.now(timezone.utc) < exp
    except ValueError:
        return False


# ── Groww ─────────────────────────────────────────────────────────────


def get_groww_token() -> str | None:
    """Return a valid Groww access token, auto-refreshing when expired.

    Priority:
      1. Cached token in credentials.json — if still fresh, return it.
      2. Checksum auth: silently re-exchange using stored api_key + api_secret.
      3. TOTP auth: raise GrowwLoginRequired (cannot refresh without a live code).
      4. None — no credentials stored; caller falls back to env vars.
    """
    creds = load()

    if _token_is_fresh(creds):
        return creds["groww_access_token"]

    api_key = creds.get("groww_api_key")
    if not api_key:
        return None

    auth_method = creds.get("groww_auth_method", "checksum")

    if auth_method == "totp":
        raise GrowwLoginRequired(
            "Groww token has expired and cannot be refreshed automatically "
            "(TOTP codes rotate every 30 s).\n"
            "Run:  pennywise login groww"
        )

    # checksum — silent refresh
    api_secret = creds.get("groww_api_secret")
    if not api_secret:
        return None

    from pennywise.connectors.groww import exchange_for_access_token
    new_token = exchange_for_access_token(api_key, api_secret)
    update(
        groww_access_token=new_token,
        groww_token_expires_at=_next_groww_expiry().isoformat(),
    )
    return new_token


def set_groww_credentials(
    api_key: str,
    api_secret: str | None,
    *,
    access_token: str,
    auth_method: str = "checksum",
) -> None:
    """Persist Groww credentials and the freshly-minted access token."""
    fields: dict[str, Any] = {
        "groww_api_key": api_key,
        "groww_auth_method": auth_method,
        "groww_access_token": access_token,
        "groww_token_expires_at": _next_groww_expiry().isoformat(),
    }
    if api_secret is not None:
        fields["groww_api_secret"] = api_secret
    update(**fields)


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
    return load().get("google_id_token")
