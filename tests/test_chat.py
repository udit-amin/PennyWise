"""Tests for the chat module's tool implementations.

We don't test the live REPL loop (that requires an Anthropic key + network);
we test the deterministic tool functions Claude would call. The chat layer
exists to glue these tools to the LLM — once each tool is right, the chat
loop is essentially a thin Anthropic SDK adapter.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pennywise import chat as chat_mod
from pennywise.snapshot import Snapshot


def _fake_snapshot() -> Snapshot:
    return Snapshot(
        fetched_at="2025-01-01T00:00:00",
        holdings=[
            {
                "symbol": "HDFCBANK", "quantity": 10, "avg_price": 1500, "ltp": 1700,
                "broad_sector": "Financials", "sector": "Financial Services",
                "industry_raw": "Private Sector Bank", "market_cap_cr": 1_200_000,
            },
            {
                "symbol": "INFY", "quantity": 20, "avg_price": 1400, "ltp": 1300,
                "broad_sector": "Information Technology", "sector": "Information Technology",
                "industry_raw": "IT - Software", "market_cap_cr": 700_000,
            },
        ],
        positions=[],
    )


@pytest.fixture(autouse=True)
def _patch_snapshot():
    with patch.object(chat_mod, "_snapshot", _fake_snapshot):
        yield


def test_get_holdings_orders_by_value_desc():
    out = chat_mod.tool_get_holdings()
    assert out["count"] == 2
    syms = [r["symbol"] for r in out["holdings"]]
    # INFY value 20 * 1300 = 26,000 > HDFCBANK 10 * 1700 = 17,000
    assert syms == ["INFY", "HDFCBANK"]
    # Sanity-check the derived fields
    infy = out["holdings"][0]
    assert infy["value"] == pytest.approx(26_000)
    assert infy["pnl_pct"] == pytest.approx((1300 / 1400 - 1) * 100)


def test_get_risk_metrics_returns_sector_weights():
    out = chat_mod.tool_get_risk_metrics()
    weights = out["risk_metrics"]["sector_weights"]
    assert pytest.approx(sum(weights.values()), rel=1e-6) == 1.0
    assert "Financial Services" in weights
    assert "Information Technology" in weights


def test_analyze_ticker_found():
    out = chat_mod.tool_analyze_ticker("HDFCBANK")
    assert out["held"] is True
    assert out["sector"] == "Financial Services"
    assert out["market_cap_cr"] == 1_200_000
    assert out["pnl_pct"] == pytest.approx((1700 / 1500 - 1) * 100)


def test_analyze_ticker_case_insensitive_and_missing():
    assert chat_mod.tool_analyze_ticker("hdfcbank")["held"] is True
    assert chat_mod.tool_analyze_ticker("NOTHELD")["held"] is False


def test_tool_specs_have_required_shape():
    names = {t["name"] for t in chat_mod.TOOL_SPECS}
    assert names == {
        "get_holdings",
        "get_risk_metrics",
        "analyze_ticker",
        "fetch_technicals",
        "fetch_fundamentals",
        "fetch_news",
        "list_recommendations",
    }
    for spec in chat_mod.TOOL_SPECS:
        assert "description" in spec and "input_schema" in spec
        assert spec["input_schema"]["type"] == "object"
    # Every spec name has an impl
    assert names == set(chat_mod.TOOL_IMPLS)


def test_fetch_technicals_caches_per_session():
    """Second call for the same symbol must not re-hit the network."""
    chat_mod._TECHNICALS_CACHE.clear()
    calls = {"n": 0}

    class FakeClient:
        def fetch(self, sym):
            calls["n"] += 1
            from pennywise.connectors.yfinance_client import Technicals
            return Technicals(sym, 100.0, 95.0, 90.0, 60.0, 1.2, 1.0, 0.18)

    with patch("pennywise.connectors.yfinance_client.YFinanceClient", FakeClient):
        first = chat_mod.tool_fetch_technicals("INFY")
        second = chat_mod.tool_fetch_technicals("INFY")

    assert calls["n"] == 1
    assert first["rsi_14"] == 60.0
    assert second.get("cached") is True


def test_fetch_fundamentals_handles_errors_gracefully():
    """A failing scraper must not crash the chat — error surfaces to Claude."""
    chat_mod._FUNDAMENTALS_CACHE.clear()

    class Boom:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def fetch(self, sym):
            raise RuntimeError("429 too many requests")

    with patch("pennywise.connectors.screener.ScreenerScraper", Boom):
        out = chat_mod.tool_fetch_fundamentals("INFY")

    assert "error" in out
    assert "RuntimeError" in out["error"]


def test_clear_live_caches_wipes_both():
    chat_mod._TECHNICALS_CACHE["X"] = {"x": 1}
    chat_mod._FUNDAMENTALS_CACHE["Y"] = {"y": 2}
    chat_mod._clear_live_caches()
    assert len(chat_mod._TECHNICALS_CACHE) == 0
    assert len(chat_mod._FUNDAMENTALS_CACHE) == 0


# ────────────────────────── session persistence ──────────────────────────


def test_session_save_load_roundtrip(tmp_path, monkeypatch):
    """A saved session can be listed and reloaded with identical history."""
    monkeypatch.setenv("PENNYWISE_CHATS_DIR", str(tmp_path))

    sess = chat_mod.ChatSession(
        client=None,  # type: ignore[arg-type] — save() doesn't touch the client
        model="claude-test",
        history=[
            {"role": "user", "content": "am I over-concentrated?"},
            {"role": "assistant", "content": [{"type": "text", "text": "Yes — 34% banks."}]},
            {"role": "user", "content": "trim what?"},
        ],
        session_id=chat_mod._new_session_id(),
    )
    path = sess.save()
    assert path.exists()

    listed = chat_mod.list_sessions()
    assert len(listed) == 1
    assert listed[0]["id"] == sess.session_id
    assert listed[0]["turns"] == 3
    assert listed[0]["last_user_message"] == "trim what?"

    data = chat_mod.load_session_file(sess.session_id)
    assert data is not None
    assert len(data["history"]) == 3
    assert data["history"][0]["content"] == "am I over-concentrated?"
    # Assistant block was already a dict — should round-trip cleanly
    assert data["history"][1]["content"][0]["text"] == "Yes — 34% banks."


def test_latest_session_returns_newest(tmp_path, monkeypatch):
    monkeypatch.setenv("PENNYWISE_CHATS_DIR", str(tmp_path))
    older = chat_mod.ChatSession(
        client=None,  # type: ignore[arg-type]
        model="m", history=[{"role": "user", "content": "older"}],
        session_id="20250101T000000Z",
    )
    newer = chat_mod.ChatSession(
        client=None,  # type: ignore[arg-type]
        model="m", history=[{"role": "user", "content": "newer"}],
        session_id="20250601T000000Z",
    )
    older.save()
    newer.save()
    latest = chat_mod.latest_session()
    assert latest is not None and latest["id"] == "20250601T000000Z"


def test_restore_preserves_history_and_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PENNYWISE_CHATS_DIR", str(tmp_path))
    sess = chat_mod.ChatSession(
        client=None,  # type: ignore[arg-type]
        model="m",
        history=[{"role": "user", "content": "hi"}],
        session_id="20250101T000000Z",
    )
    sess.save()
    data = chat_mod.load_session_file("20250101T000000Z")
    assert data is not None
    restored = chat_mod.ChatSession.restore(
        data, client=None, model="m",  # type: ignore[arg-type]
    )
    assert restored.session_id == "20250101T000000Z"
    assert restored.history[0]["content"] == "hi"
