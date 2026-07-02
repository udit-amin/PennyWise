"""Google OAuth + JWT auth for PennyWise API.

Flow:
  1. Frontend calls GET /api/auth/google/url → gets the Google OAuth URL
  2. User clicks → Google login → redirected back with auth code
  3. Frontend calls POST /api/auth/google/callback with the code
  4. Backend exchanges code for tokens → verifies ID token → creates/updates
     user in DynamoDB → returns a PennyWise JWT
  5. All subsequent API calls include Authorization: Bearer <jwt>
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt

from pennywise.api import db

_DEV_JWT_SECRET = "pennywise-dev-secret-change-me"

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", _DEV_JWT_SECRET)
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

security = HTTPBearer()


def validate_auth_config() -> None:
    """Fail closed on insecure config in deployed environments.

    Called at application startup. In ``staging``/``prod`` a missing or
    default ``JWT_SECRET`` would let anyone forge tokens, and missing Google
    OAuth credentials would silently break login — so we refuse to boot.
    Dev keeps the convenient defaults.
    """
    from pennywise import config

    if not config.load().is_prod_like:
        return

    errors: list[str] = []
    if not JWT_SECRET or JWT_SECRET == _DEV_JWT_SECRET:
        errors.append("JWT_SECRET is unset or the dev default — set a strong random secret.")
    if not GOOGLE_CLIENT_ID:
        errors.append("GOOGLE_CLIENT_ID is unset.")
    if not GOOGLE_CLIENT_SECRET:
        errors.append("GOOGLE_CLIENT_SECRET is unset.")
    if errors:
        raise RuntimeError(
            "Refusing to start in a deployed environment with insecure auth config:\n  - "
            + "\n  - ".join(errors)
        )


# ── Google OAuth ──────────────────────────────────────────────────────

OAUTH_STATE_TTL_MINUTES = 10


def create_oauth_state() -> str:
    """Mint a self-validating CSRF state token (RFC 6749 §10.12).

    Signed with the existing JWT secret, so it verifies statelessly across
    all workers/tasks — no server-side session store needed."""
    payload = {
        "purpose": "oauth_state",
        "nonce": uuid.uuid4().hex,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_oauth_state(state: str | None) -> None:
    """Raise 400 unless ``state`` is one we minted recently."""
    detail = "Invalid or expired OAuth state — restart the sign-in flow."
    if not state:
        raise HTTPException(status_code=400, detail="Missing OAuth state parameter.")
    try:
        payload = jwt.decode(state, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=400, detail=detail)
    if payload.get("purpose") != "oauth_state":
        raise HTTPException(status_code=400, detail=detail)


def validate_redirect_uri(uri: str) -> None:
    """Exact-match the redirect_uri against the configured allowlist. A
    client-chosen redirect_uri would let an attacker receive the token."""
    from pennywise import config

    if uri not in config.load().allowed_redirect_uris:
        raise HTTPException(status_code=400, detail="redirect_uri is not allowed.")


def google_auth_url(redirect_uri: str, state: str) -> str:
    """Build the Google OAuth authorization URL."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code(code: str, redirect_uri: str) -> dict:
    """Exchange the authorization code for tokens and verify the ID token.

    Returns: {"email": str, "name": str | None, "picture": str | None}
    """
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        resp = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google token exchange failed: {resp.text}")

    tokens = resp.json()
    raw_id_token = tokens.get("id_token")
    if not raw_id_token:
        raise HTTPException(status_code=400, detail="No id_token in Google response.")

    # Verify the ID token
    try:
        info = google_id_token.verify_oauth2_token(
            raw_id_token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid Google ID token: {e}")

    return _profile_from_id_token(info)


def _profile_from_id_token(info: dict) -> dict:
    """Extract the user profile, rejecting tokens without a verified email —
    the email is our user identity key (users table email-index)."""
    email = info.get("email")
    if not email or not info.get("email_verified", False):
        raise HTTPException(
            status_code=401,
            detail="Google account did not provide a verified email address.",
        )
    return {
        "email": email,
        "name": info.get("name"),
        "picture": info.get("picture"),
    }


# ── JWT ───────────────────────────────────────────────────────────────


def create_jwt(user_id: str, email: str) -> str:
    """Mint a PennyWise JWT."""
    payload = {
        "sub": user_id,
        "email": email,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and verify a PennyWise JWT. Raises on expiry/tampering."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )


# ── FastAPI dependency ────────────────────────────────────────────────


async def current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """Dependency that extracts + validates the JWT and returns the user dict.

    The DynamoDB lookup runs in a worker thread — this dependency executes on
    every authenticated request and must not block the event loop."""
    payload = decode_jwt(creds.credentials)
    user = await asyncio.to_thread(db.get_user, payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    return user
