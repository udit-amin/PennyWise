"""Rate limiting to cap Anthropic (Opus) spend per user.

Two surfaces, two mechanisms:

* HTTP routes (e.g. ``POST /api/recommendations``) use ``slowapi`` — already a
  project dependency — via the ``limiter`` below, keyed by authenticated user.
* The chat WebSocket can't use slowapi (no ``Request``), so it uses a small
  in-memory sliding-window check (``allow_chat_turn``). This is per-instance;
  with the current single-task deployment that's exact. When we move to >1 task
  (alongside the SQS job runner) this graduates to a shared store (Redis).
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from pennywise.api.auth import decode_jwt


def _user_or_ip(request: Request) -> str:
    """Key limits by authenticated user_id, falling back to client IP."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            return decode_jwt(auth[7:]).get("sub") or get_remote_address(request)
        except Exception:
            pass
    return get_remote_address(request)


# Default limits intentionally empty — apply explicitly per expensive route.
limiter = Limiter(key_func=_user_or_ip)

# Per-route limit strings (env-overridable for staging vs prod tuning).
RECOMMENDATIONS_LIMIT = os.getenv("PENNYWISE_RECO_RATE_LIMIT", "5/hour")


# ── In-memory window for the chat WebSocket ───────────────────────────

CHAT_MAX_TURNS = int(os.getenv("PENNYWISE_CHAT_TURNS_PER_WINDOW", "30"))
CHAT_WINDOW_SECONDS = int(os.getenv("PENNYWISE_CHAT_WINDOW_SECONDS", "3600"))

_chat_hits: dict[str, deque[float]] = defaultdict(deque)


def allow_chat_turn(user_id: str) -> bool:
    """Return True if ``user_id`` may run another chat turn now; records the
    hit when allowed. Sliding window of ``CHAT_MAX_TURNS`` per window."""
    now = time.monotonic()
    cutoff = now - CHAT_WINDOW_SECONDS
    hits = _chat_hits[user_id]
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= CHAT_MAX_TURNS:
        return False
    hits.append(now)
    return True
