from pennywise.agents.candidate_picker import candidate_picker_node


def test_skips_held_symbols_and_respects_gap_filters():
    state = {
        "focus": "all",
        "holdings": [{"symbol": "INFY"}, {"symbol": "TCS"}],
        "gaps": {"sectors": ["Financial Services"], "market_cap_buckets": []},
    }
    out = candidate_picker_node(state)
    picks = out["candidate_tickers"]
    assert "INFY" not in picks and "TCS" not in picks
    assert all(s in {"HDFCBANK", "ICICIBANK", "SBIN", "BAJFINANCE"} for s in picks)
    assert 0 < len(picks) <= 8


def test_rebalance_focus_returns_no_candidates():
    state = {"focus": "rebalance", "holdings": [], "gaps": {"sectors": [], "market_cap_buckets": []}}
    assert candidate_picker_node(state)["candidate_tickers"] == []
