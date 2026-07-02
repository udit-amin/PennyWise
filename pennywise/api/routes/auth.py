"""Auth routes: Google OAuth login + user info."""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pennywise.api import db
from pennywise.api.auth import (
    GOOGLE_CLIENT_ID,
    create_jwt,
    create_oauth_state,
    current_user,
    exchange_google_code,
    google_auth_url,
    validate_redirect_uri,
    verify_oauth_state,
)
from pennywise.api.ratelimit import limiter

_AUTH_RATE_LIMIT = "10/minute"
_STATE_COOKIE = "pw_oauth_state"
from pennywise.api.models import (
    AuthResponse,
    GoogleCallbackRequest,
    GrowwCredentialRequest,
    GrowwStatusResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8000/api/auth/google/callback",
)


# ── Login page ────────────────────────────────────────────────────────

_LOGIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PennyWise — Sign in</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#0f0f10;color:#e8e8e8;display:flex;align-items:center;
          justify-content:center;min-height:100vh}}
    .card{{background:#1a1a1d;border:1px solid #2a2a2d;border-radius:12px;
           padding:48px 40px;max-width:380px;width:100%;text-align:center}}
    h1{{font-size:1.6rem;font-weight:700;margin-bottom:6px}}
    p{{color:#888;font-size:.9rem;margin-bottom:32px}}
    .btn{{display:inline-flex;align-items:center;gap:10px;background:#fff;
          color:#111;border:none;border-radius:8px;padding:12px 24px;
          font-size:.95rem;font-weight:600;cursor:pointer;
          text-decoration:none;transition:opacity .15s}}
    .btn:hover{{opacity:.85}}
    .btn svg{{width:20px;height:20px}}
    .note{{margin-top:24px;font-size:.8rem;color:#555}}
  </style>
</head>
<body>
  <div class="card">
    <h1>PennyWise</h1>
    <p>Agentic portfolio advisor for Groww</p>
    <a class="btn" href="/api/auth/google/start">
      <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
      </svg>
      Sign in with Google
    </a>
    <p class="note">Your portfolio data stays on your device.</p>
  </div>
</body>
</html>
"""

_SUCCESS_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PennyWise — Signed in</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#0f0f10;color:#e8e8e8;display:flex;align-items:center;
          justify-content:center;min-height:100vh}}
    .card{{background:#1a1a1d;border:1px solid #2a2a2d;border-radius:12px;
           padding:48px 40px;max-width:440px;width:100%;text-align:center}}
    h1{{font-size:1.4rem;font-weight:700;margin-bottom:8px;color:#4ade80}}
    .email{{color:#888;margin-bottom:24px;font-size:.9rem}}
    .token-label{{text-align:left;font-size:.75rem;color:#666;
                  margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}}
    textarea{{width:100%;background:#111;border:1px solid #333;border-radius:6px;
              color:#a3e635;font-family:monospace;font-size:.75rem;padding:10px;
              resize:none;height:80px}}
    .note{{margin-top:16px;font-size:.8rem;color:#555}}
  </style>
</head>
<body>
  <div class="card">
    <h1>Signed in!</h1>
    <p class="email">{email}</p>
    <p class="token-label">PennyWise JWT (copy for API calls)</p>
    <textarea readonly onclick="this.select()">{token}</textarea>
    <p class="note">Token expires in 24 h. Use it as<br>
      <code>Authorization: Bearer &lt;token&gt;</code></p>
  </div>
  <script>
    // Store in localStorage so client-side apps can pick it up
    localStorage.setItem('pennywise_jwt', '{token}');
    localStorage.setItem('pennywise_email', '{email}');
  </script>
</body>
</html>
"""

_ERROR_HTML = """\
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>PennyWise — Auth error</title>
<style>body{{font-family:sans-serif;background:#0f0f10;color:#e8e8e8;
display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#1a1a1d;border:1px solid #f87171;border-radius:12px;
padding:40px;max-width:400px;text-align:center}}
h1{{color:#f87171;margin-bottom:12px}}a{{color:#60a5fa}}</style></head>
<body><div class="card"><h1>Auth error</h1><p>{detail}</p>
<p style="margin-top:16px"><a href="/login">Try again</a></p></div></body></html>
"""


# ── Routes ────────────────────────────────────────────────────────────


@router.get("/google/start", include_in_schema=False)
@limiter.limit(_AUTH_RATE_LIMIT)
async def google_start(request: Request) -> RedirectResponse:
    """Redirect the browser to Google OAuth (used by the login page).

    Sets the CSRF state as an HttpOnly cookie; the callback requires the
    query param and cookie to match (double-submit) AND the state signature
    to verify.
    """
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_CLIENT_ID is not configured on this server.",
        )
    from pennywise import config

    state = create_oauth_state()
    response = RedirectResponse(google_auth_url(_GOOGLE_REDIRECT_URI, state), status_code=302)
    response.set_cookie(
        _STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=config.load().is_prod_like,
    )
    return response


@router.get("/google/callback", response_class=HTMLResponse)
@limiter.limit(_AUTH_RATE_LIMIT)
async def google_callback_browser(
    request: Request,
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> HTMLResponse:
    """Browser-facing OAuth callback (Google redirects here after login).

    Exchanges the code, creates/updates the user in DynamoDB, and returns
    an HTML page that displays the PennyWise JWT and stores it in localStorage.
    """
    if error:
        return HTMLResponse(
            _ERROR_HTML.format(detail=f"Google returned: {error}"), status_code=400
        )
    if not code:
        return HTMLResponse(
            _ERROR_HTML.format(detail="No authorization code received."), status_code=400
        )

    cookie_state = request.cookies.get(_STATE_COOKIE)
    if not state or not cookie_state or state != cookie_state:
        return HTMLResponse(
            _ERROR_HTML.format(detail="Sign-in session mismatch — please try again."),
            status_code=400,
        )
    try:
        verify_oauth_state(state)
        info = await exchange_google_code(code, _GOOGLE_REDIRECT_URI)
    except HTTPException as exc:
        return HTMLResponse(
            _ERROR_HTML.format(detail=exc.detail), status_code=exc.status_code
        )

    user = await asyncio.to_thread(
        db.create_user,
        email=info["email"],
        name=info.get("name"),
        picture=info.get("picture"),
    )
    token = create_jwt(user["user_id"], user["email"])
    response = HTMLResponse(_SUCCESS_HTML.format(email=user["email"], token=token))
    response.delete_cookie(_STATE_COOKIE)
    return response


@router.get("/google/url")
@limiter.limit(_AUTH_RATE_LIMIT)
async def get_google_url(request: Request, redirect_uri: str = Query(...)) -> dict:
    """Return the Google OAuth URL + CSRF state (for JS-driven flows).

    The frontend must echo ``state`` back in the POST callback."""
    validate_redirect_uri(redirect_uri)
    state = create_oauth_state()
    return {"url": google_auth_url(redirect_uri, state), "state": state}


@router.post("/google/callback", response_model=AuthResponse)
@limiter.limit(_AUTH_RATE_LIMIT)
async def google_callback_api(request: Request, body: GoogleCallbackRequest) -> AuthResponse:
    """Exchange Google auth code for a PennyWise JWT (JSON API for frontends)."""
    validate_redirect_uri(body.redirect_uri)
    verify_oauth_state(body.state)
    info = await exchange_google_code(body.code, body.redirect_uri)
    user = await asyncio.to_thread(
        db.create_user,
        email=info["email"],
        name=info.get("name"),
        picture=info.get("picture"),
    )
    token = create_jwt(user["user_id"], user["email"])
    return AuthResponse(
        access_token=token,
        user_id=user["user_id"],
        email=user["email"],
        name=user.get("name"),
        picture=user.get("picture"),
    )


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(current_user)) -> UserResponse:
    """Return the current user from the JWT."""
    return UserResponse(
        user_id=user["user_id"],
        email=user["email"],
        name=user.get("name"),
        picture=user.get("picture"),
    )


def _verify_groww_credentials(creds: dict) -> None:
    """Cheap authenticated Groww call so we reject bad credentials with a 400
    instead of storing garbage. Sync — run in a worker thread."""
    from pennywise.connectors.groww import GrowwConnector, exchange_for_access_token

    if creds.get("api_key") and creds.get("api_secret"):
        exchange_for_access_token(creds["api_key"], creds["api_secret"])
    elif creds.get("token"):
        with GrowwConnector(token=creds["token"]) as g:
            g.holdings()


@router.post("/groww-credentials")
@limiter.limit(_AUTH_RATE_LIMIT)
async def save_groww_credentials(
    request: Request,
    body: GrowwCredentialRequest,
    user: dict = Depends(current_user),
) -> dict:
    """Verify and store Groww API credentials (encrypted at rest)."""
    from pennywise.api.groww_creds import encrypt_credentials

    creds = {
        k: v
        for k, v in (("token", body.token), ("api_key", body.api_key), ("api_secret", body.api_secret))
        if v
    }
    try:
        await asyncio.to_thread(_verify_groww_credentials, creds)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Groww rejected these credentials. Check the API key/secret "
            "(or token) in your Groww trading API settings and try again.",
        )

    await asyncio.to_thread(
        db.set_user_groww_credentials, user["user_id"], encrypt_credentials(creds)
    )
    return {"status": "saved"}


@router.get("/groww-credentials/status", response_model=GrowwStatusResponse)
async def groww_credentials_status(
    user: dict = Depends(current_user),
) -> GrowwStatusResponse:
    """Whether this user has a portfolio source, and which kind."""
    linked = bool(user.get("groww_credentials_enc") or user.get("groww_credentials"))
    snapshot = await asyncio.to_thread(db.load_snapshot, user["user_id"])
    if snapshot:
        return GrowwStatusResponse(
            linked=True,
            source="groww" if linked else snapshot.get("source", "upload"),
            as_of=snapshot.get("fetched_at") or None,
        )
    return GrowwStatusResponse(linked=linked, source="groww" if linked else None)
