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

import os
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

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET = os.getenv("JWT_SECRET", "pennywise-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

security = HTTPBearer()


# ── Google OAuth ──────────────────────────────────────────────────────


def google_auth_url(redirect_uri: str) -> str:
    """Build the Google OAuth authorization URL."""
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
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

    return {
        "email": info["email"],
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
    """Dependency that extracts + validates the JWT and returns the user dict."""
    payload = decode_jwt(creds.credentials)
    user = db.get_user(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    return user
