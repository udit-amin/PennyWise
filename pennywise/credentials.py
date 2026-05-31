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

import base64
import hashlib
import hmac
import json
import os
import stat
import struct
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class GrowwLoginRequired(RuntimeError):
    """Raised when stored Groww credentials are insufficient to refresh the token."""


def _totp(secret_b32: str, *, step: int = 30, digits: int = 6) -> str:
    """Generate a TOTP code from a base32 secret (RFC 6238, HMAC-SHA1)."""
    key = base64.b32decode(secret_b32.upper().replace(" ", ""))
    t = int(_time.time()) // step
    msg = struct.pack(">Q", t)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


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
      2. Checksum: silently re-exchange using stored api_key + api_secret.
      3. TOTP: generate a live 6-digit code from the stored totp_secret and
         exchange — fully automatic, no user input required.
      4. None — no credentials stored; caller falls back to env vars.
    """
    creds = load()

    if _token_is_fresh(creds):
        return creds["groww_access_token"]

    api_key = creds.get("groww_api_key")
    if not api_key:
        return None

    from pennywise.connectors.groww import exchange_for_access_token

    auth_method = creds.get("groww_auth_method", "checksum")

    if auth_method == "totp":
        totp_secret = creds.get("groww_totp_secret")
        if not totp_secret:
            raise GrowwLoginRequired(
                "TOTP secret not found in credentials.\n"
                "Run:  pennywise login groww"
            )
        code = _totp(totp_secret)
        new_token = exchange_for_access_token(api_key, totp_code=code)
    else:
        api_secret = creds.get("groww_api_secret")
        if not api_secret:
            return None
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
    totp_secret: str | None = None,
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
    if totp_secret is not None:
        fields["groww_totp_secret"] = totp_secret
    update(**fields)


def is_logged_in_groww() -> bool:
    """True if Groww API key is stored (token may still need refreshing)."""
    return bool(load().get("groww_api_key"))
