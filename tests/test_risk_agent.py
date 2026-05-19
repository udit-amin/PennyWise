from pennywise.agents.risk_analyzer import risk_analyzer_node


def test_risk_analyzer_node_canonicalises_broad_sector():
    """Prefers Screener's GICS Broad Sector field for canonicalisation."""
    state = {
        "holdings": [
            {"symbol": "HDFCBANK", "quantity": 10, "avg_price": 1500, "ltp": 1600},
            {"symbol": "INFY", "quantity": 10, "avg_price": 1400, "ltp": 1500},
        ],
        "fundamentals": {
            "HDFCBANK": {"broad_sector": "Financials", "industry": "Private Sector Bank", "market_cap_cr": 1_200_000},
            "INFY": {"broad_sector": "Information Technology", "industry": "IT - Software", "market_cap_cr": 700_000},
        },
    }
    out = risk_analyzer_node(state)
    sectors = out["risk_metrics"]["sector_weights"]
    assert "Financial Services" in sectors
    assert "Information Technology" in sectors
    assert "unknown" not in sectors
    h0 = next(h for h in out["holdings"] if h["symbol"] == "HDFCBANK")
    assert h0["sector"] == "Financial Services"
    assert h0["broad_sector"] == "Financials"
    assert h0["industry_raw"] == "Private Sector Bank"
    assert h0["market_cap_cr"] == 1_200_000


def test_etf_and_gold_split_into_asset_allocation_not_sector_pie():
    """Non-stock instruments live in asset_allocation only; they must not pollute
    the sector / mcap pies (which describe the *stock* portion of the book)."""
    state = {
        "holdings": [
            {"symbol": "RELIANCE", "quantity": 1, "avg_price": 1000, "ltp": 1500},
            {"symbol": "GROWWGOLD", "quantity": 100, "avg_price": 50, "ltp": 60},
            {"symbol": "HDFCNIFTY", "quantity": 80, "avg_price": 250, "ltp": 270},
        ],
        "fundamentals": {
            "RELIANCE": {"broad_sector": "Energy", "industry": "Refineries", "market_cap_cr": 1_800_000},
            "GROWWGOLD": {"ticker": "GROWWGOLD", "error": "404"},
            "HDFCNIFTY": {"ticker": "HDFCNIFTY", "error": "404"},
        },
    }
    out = risk_analyzer_node(state)
    risk = out["risk_metrics"]
    # Asset allocation has all three classes
    assert set(risk["asset_allocation"]) == {"stock", "gold_silver", "etf"}
    # Sector / mcap pies contain ONLY stock data
    assert list(risk["sector_weights"]) == ["Energy"]
    assert list(risk["market_cap_weights"]) == ["large_cap"]


def test_risk_analyzer_falls_back_when_broad_sector_missing():
    """If broad_sector is absent, fall through to sector then industry."""
    state = {
        "holdings": [{"symbol": "ACME", "quantity": 10, "avg_price": 100, "ltp": 110}],
        "fundamentals": {"ACME": {"industry": "Pharmaceuticals"}},
    }
    out = risk_analyzer_node(state)
    assert out["risk_metrics"]["sector_weights"] == {"Healthcare": 1.0}


def test_risk_analyzer_node_handles_missing_fundamentals():
    """A truly unknown symbol (no Screener data, no symbol match) → 'unknown'."""
    state = {
        "holdings": [{"symbol": "XYZABC", "quantity": 100, "avg_price": 50, "ltp": 60}],
        "fundamentals": {"XYZABC": {"ticker": "XYZABC", "error": "404"}},
    }
    out = risk_analyzer_node(state)
    assert out["risk_metrics"]["sector_weights"] == {"unknown": 1.0}
