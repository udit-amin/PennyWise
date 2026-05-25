"""Auth routes: Google OAuth login + user info."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from pennywise.api import db
from pennywise.api.auth import (
    create_jwt,
    current_user,
    exchange_google_code,
    google_auth_url,
)
from pennywise.api.models import (
    AuthResponse,
    GoogleCallbackRequest,
    GrowwCredentialRequest,
    UserResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/google/url")
async def get_google_url(redirect_uri: str = Query(...)) -> dict:
    """Return the Google OAuth URL the frontend should redirect to."""
    return {"url": google_auth_url(redirect_uri)}


@router.post("/google/callback", response_model=AuthResponse)
async def google_callback(body: GoogleCallbackRequest) -> AuthResponse:
    """Exchange Google auth code for a PennyWise JWT."""
    info = await exchange_google_code(body.code, body.redirect_uri)
    user = db.create_user(
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


@router.post("/groww-credentials")
async def save_groww_credentials(
    body: GrowwCredentialRequest,
    user: dict = Depends(current_user),
) -> dict:
    """Store Groww API credentials (encrypted in DynamoDB for now;
    graduate to SSM SecureString in production)."""
    table = db._table("users")
    creds = {}
    if body.token:
        creds["groww_token"] = body.token
    if body.api_key:
        creds["groww_api_key"] = body.api_key
    if body.api_secret:
        creds["groww_api_secret"] = body.api_secret
    table.update_item(
        Key={"user_id": user["user_id"]},
        UpdateExpression="SET groww_credentials = :c",
        ExpressionAttributeValues={":c": creds},
    )
    return {"status": "saved"}
