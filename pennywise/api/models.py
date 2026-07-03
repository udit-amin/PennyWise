"""Pydantic request / response models for the PennyWise API."""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ── Auth ──────────────────────────────────────────────────────────────


class GoogleCallbackRequest(BaseModel):
    code: str = Field(..., description="Authorization code from Google OAuth redirect.")
    redirect_uri: str = Field(..., description="The redirect URI used in the OAuth flow.")
    state: str = Field(..., description="CSRF state issued by GET /api/auth/google/url.")


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    name: str | None = None
    picture: str | None = None


class UserResponse(BaseModel):
    user_id: str
    email: str
    name: str | None = None
    picture: str | None = None


class GrowwCredentialRequest(BaseModel):
    token: str | None = None
    api_key: str | None = None
    api_secret: str | None = None

    @model_validator(mode="after")
    def _require_usable_combination(self) -> "GrowwCredentialRequest":
        if self.token or (self.api_key and self.api_secret):
            return self
        raise ValueError(
            "Provide either `token` (a daily access token) or both "
            "`api_key` and `api_secret`."
        )


class GrowwStatusResponse(BaseModel):
    linked: bool
    source: str | None = None  # "groww" | "upload" | None
    as_of: str | None = None   # snapshot fetched_at, when one exists


# ── Portfolio ─────────────────────────────────────────────────────────


class HoldingRow(BaseModel):
    symbol: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap_cr: float | None = None
    quantity: float = 0
    avg_price: float = 0
    ltp: float | None = None
    value: float | None = None
    pnl_pct: float | None = None


class HoldingsResponse(BaseModel):
    count: int
    holdings: list[HoldingRow]


class RiskMetricsResponse(BaseModel):
    risk_metrics: dict
    gaps: dict


class UploadResponse(BaseModel):
    """Result of a holdings-statement upload."""
    count: int
    source: str = "upload"
    as_of: str
    ignored: list[dict] = Field(
        default_factory=list,
        description="Rows that could not be imported, with row number and reason.",
    )


# ── Tools ─────────────────────────────────────────────────────────────


class TechnicalsResponse(BaseModel):
    ticker: str
    last_close: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    vol_30d_ann: float | None = None
    cached: bool = False
    error: str | None = None


class FundamentalsResponse(BaseModel):
    ticker: str
    pe: float | None = None
    pb: float | None = None
    debt_to_equity: float | None = None
    roe: float | None = None
    market_cap_cr: float | None = None
    industry: str | None = None
    broad_sector: str | None = None
    sector: str | None = None
    cached: bool = False
    error: str | None = None


class NewsItem(BaseModel):
    title: str
    link: str
    published: str | None = None


class NewsResponse(BaseModel):
    symbol: str
    count: int
    items: list[NewsItem]
    error: str | None = None


# ── Chat ──────────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    type: str = "message"
    text: str
    session_id: str | None = None


class ChatSessionSummary(BaseModel):
    id: str
    turns: int
    started_at: str | None = None
    last_user_message: str | None = None


# ── Recommendations (background job) ─────────────────────────────────


class RecommendRequest(BaseModel):
    focus: str = "all"


class JobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | completed | failed
    result: dict | None = None
    error: str | None = None
