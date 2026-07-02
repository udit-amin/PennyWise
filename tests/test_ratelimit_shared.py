"""Shared (DynamoDB-backed) fixed-window rate counters."""
from __future__ import annotations

import pennywise.api.ratelimit as ratelimit


def test_chat_counter_blocks_over_limit(fake_db, monkeypatch):
    monkeypatch.setattr(ratelimit, "CHAT_MAX_TURNS", 2)
    assert ratelimit.allow_chat_turn("u1") is True
    assert ratelimit.allow_chat_turn("u1") is True
    assert ratelimit.allow_chat_turn("u1") is False
    assert ratelimit.allow_chat_turn("u2") is True  # per-user isolation


def test_window_rollover_resets_budget(fake_db, monkeypatch):
    monkeypatch.setattr(ratelimit, "CHAT_MAX_TURNS", 1)
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(ratelimit.time, "time", lambda: clock["now"])

    assert ratelimit.allow_chat_turn("u1") is True
    assert ratelimit.allow_chat_turn("u1") is False
    clock["now"] += ratelimit.CHAT_WINDOW_SECONDS  # next window
    assert ratelimit.allow_chat_turn("u1") is True


def test_counter_fails_open_on_db_error(fake_db, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("dynamo down")

    monkeypatch.setattr(ratelimit.db, "incr_rate_counter", _boom)
    assert ratelimit.allow_chat_turn("u1") is True  # availability over strictness


def test_recommendation_counter(fake_db, monkeypatch):
    monkeypatch.setattr(ratelimit, "RECO_MAX_PER_WINDOW", 1)
    assert ratelimit.allow_recommendation("u1") is True
    assert ratelimit.allow_recommendation("u1") is False


def test_recommendations_route_429s(app_client, fake_db, test_user, auth_headers, monkeypatch):
    from pennywise.snapshot import stamp_now

    # Give the user a portfolio so only the rate limit gates the request.
    fake_db.save_snapshot(test_user["user_id"], {
        "fetched_at": stamp_now(),
        "holdings": [{"symbol": "TCS", "quantity": 1, "avg_price": 1, "ltp": 1}],
        "positions": [],
        "source": "upload",
    })
    monkeypatch.setattr(ratelimit, "RECO_MAX_PER_WINDOW", 1)
    monkeypatch.setattr(
        "pennywise.api.routes.recommendations.submit_job", lambda *a, **k: None
    )

    first = app_client.post("/api/recommendations", json={"focus": "all"}, headers=auth_headers)
    assert first.status_code == 200, first.text

    second = app_client.post("/api/recommendations", json={"focus": "all"}, headers=auth_headers)
    assert second.status_code == 429
    assert "limit" in second.json()["detail"].lower()
