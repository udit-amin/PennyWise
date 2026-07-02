import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    env: str                    # "dev" | "staging" | "prod"
    groww_token: str | None
    anthropic_api_key: str | None
    fmp_api_key: str | None
    llm_model: str
    hhi_flag: float
    top_name_flag: float
    large_cap_floor_cr: float   # AMFI top-100 floor; H1 2025 ≈ ₹80,000 Cr
    mid_cap_floor_cr: float     # AMFI top-250 floor; H1 2025 ≈ ₹28,000 Cr
    reasoning_effort: str       # "low" | "medium" | "high"
    google_redirect_uri: str    # OAuth redirect URI registered in Google Cloud Console
    allowed_redirect_uris: tuple[str, ...]  # exact-match OAuth redirect allowlist

    @property
    def is_prod_like(self) -> bool:
        """True for any deployed environment (staging/prod) where dev defaults
        for secrets must be rejected."""
        return self.env in ("staging", "prod")


def _allowed_redirect_uris(env: str, google_redirect_uri: str) -> tuple[str, ...]:
    """OAuth redirect allowlist. Explicit env var wins; otherwise prod-like
    environments allow exactly the registered redirect URI, while dev also
    accepts the common local frontend callbacks."""
    raw = os.getenv("PENNYWISE_ALLOWED_REDIRECT_URIS")
    if raw:
        return tuple(u.strip() for u in raw.split(",") if u.strip())
    if env in ("staging", "prod"):
        return (google_redirect_uri,)
    return (
        google_redirect_uri,
        "http://localhost:8000/api/auth/google/callback",
        "http://localhost:3000/auth/callback",
        "http://localhost:5173/auth/callback",
    )


def load() -> Settings:
    env = os.getenv("PENNYWISE_ENV", "dev")
    google_redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8000/api/auth/google/callback",
    )
    return Settings(
        env=env,
        groww_token=os.getenv("GROWW_API_TOKEN"),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        fmp_api_key=os.getenv("FMP_API_KEY"),
        llm_model=os.getenv("PENNYWISE_LLM_MODEL", "claude-opus-4-8"),
        hhi_flag=float(os.getenv("PENNYWISE_HHI_FLAG", "0.25")),
        top_name_flag=float(os.getenv("PENNYWISE_TOP_NAME_FLAG", "0.20")),
        large_cap_floor_cr=float(os.getenv("PENNYWISE_LARGE_CAP_FLOOR_CR", "80000")),
        mid_cap_floor_cr=float(os.getenv("PENNYWISE_MID_CAP_FLOOR_CR", "28000")),
        reasoning_effort=os.getenv("PENNYWISE_REASONING_EFFORT", "medium"),
        google_redirect_uri=google_redirect_uri,
        allowed_redirect_uris=_allowed_redirect_uris(env, google_redirect_uri),
    )
