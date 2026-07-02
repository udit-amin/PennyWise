"""Rate limiting to cap Anthropic (Opus) spend per user.

Spend-capping limits (chat turns, recommendation runs) use a fixed-window
counter in DynamoDB (the cache table — TTL already enabled), so they hold
across uvicorn workers and future multi-task deployments. Counters fail
OPEN on DynamoDB errors: availability beats strictness for a spend cap.
Fixed windows are a deliberate simplification — worst case a user gets a
2x burst straddling a window boundary.

Brute-force damping on the auth endpoints stays on ``slowapi`` (IP/user
keyed, in-memory): those limits only need to be roughly right, so the
per-process slack is acceptable there.
"""
from __future__ import annotations

import logging
import os
import time

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from pennywise.api import db
from pennywise.api.auth import decode_jwt

logger = logging.getLogger("pennywise.api.ratelimit")


def _user_or_ip(request: Request) -> str:
    """Key limits by authenticated user_id, falling back to client IP."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            return decode_jwt(auth[7:]).get("sub") or get_remote_address(request)
        except Exception:
            pass
    return get_remote_address(request)


# slowapi limiter for IP-keyed limits on cheap endpoints (auth flows).
# Default limits intentionally empty — apply explicitly per route.
limiter = Limiter(key_func=_user_or_ip)


# ── Shared fixed-window counters (DynamoDB) ───────────────────────────

CHAT_MAX_TURNS = int(os.getenv("PENNYWISE_CHAT_TURNS_PER_WINDOW", "30"))
CHAT_WINDOW_SECONDS = int(os.getenv("PENNYWISE_CHAT_WINDOW_SECONDS", "3600"))
RECO_MAX_PER_WINDOW = int(os.getenv("PENNYWISE_RECO_MAX_PER_HOUR", "5"))
RECO_WINDOW_SECONDS = 3600


def _allow(scope: str, user_id: str, max_hits: int, window_s: int) -> bool:
    """Record a hit and return whether the caller is within the window's
    budget. Blocking (DynamoDB write) — call from a worker thread."""
    window_start = int(time.time() // window_s)
    try:
        hits = db.incr_rate_counter(scope, user_id, window_start, window_s * 2)
    except Exception as exc:
        logger.warning("rate counter unavailable (%s) — failing open", exc)
        return True
    return hits <= max_hits


def allow_chat_turn(user_id: str) -> bool:
    """May ``user_id`` run another chat turn now? Records the hit."""
    return _allow("chat", user_id, CHAT_MAX_TURNS, CHAT_WINDOW_SECONDS)


def allow_recommendation(user_id: str) -> bool:
    """May ``user_id`` start another recommendation workflow? Records the hit."""
    return _allow("reco", user_id, RECO_MAX_PER_WINDOW, RECO_WINDOW_SECONDS)
