from pennywise.analytics.risk import analyze_portfolio, gaps


def _h(symbol, qty, ltp, sector="Information Technology", mcap=100_000, asset_cls=None):
    h = {"symbol": symbol, "quantity": qty, "avg_price": ltp * 0.9, "ltp": ltp,
         "sector": sector, "market_cap_cr": mcap}
    if asset_cls:
        h["asset_class"] = asset_cls
    return h


def test_empty_portfolio_safe():
    r = analyze_portfolio([])
    assert r["total_value"] == 0.0
    assert r["concentration_flag"] is False
    assert r["sector_weights"] == {}
    assert r["asset_allocation"] == {}


def test_single_stock_holding_is_concentrated():
    r = analyze_portfolio([_h("INFY", 100, 1500)])
    assert r["hhi_sector"] == 1.0
    assert r["concentration_flag"] is True
    assert r["top_holding"]["symbol"] == "INFY"
    assert r["asset_allocation"] == {"stock": 1.0}


def test_diversified_portfolio_below_flag():
    holdings = [
        _h("INFY", 10, 1500, "Information Technology"),
        _h("HDFCBANK", 10, 1500, "Financial Services"),
        _h("HINDUNILVR", 10, 1500, "Consumer Goods"),
        _h("SUNPHARMA", 10, 1500, "Healthcare"),
        _h("RELIANCE", 10, 1500, "Energy"),
        _h("LT", 10, 1500, "Industrials"),
    ]
    r = analyze_portfolio(holdings)
    assert r["hhi_sector"] < 0.25
    assert r["concentration_flag"] is False
    assert sum(r["sector_weights"].values()) == 1.0


def test_sebi_market_cap_thresholds():
    """Defaults: large ≥ 80,000 Cr, mid 28,000–80,000 Cr, small < 28,000 Cr."""
    holdings = [
        _h("BIG", 1, 1000, mcap=90_000),     # large
        _h("MIDA", 1, 1000, mcap=50_000),    # mid
        _h("MIDB", 1, 1000, mcap=29_000),    # mid (just above floor)
        _h("SML", 1, 1000, mcap=15_000),     # small
    ]
    r = analyze_portfolio(holdings)
    assert r["market_cap_weights"]["large_cap"] == 0.25
    assert r["market_cap_weights"]["mid_cap"] == 0.50
    assert r["market_cap_weights"]["small_cap"] == 0.25


def test_market_cap_thresholds_are_configurable():
    """Bumping the large-cap floor pushes a previously-large company into mid."""
    holdings = [_h("X", 1, 1000, mcap=85_000)]
    default = analyze_portfolio(holdings)
    assert default["market_cap_weights"] == {"large_cap": 1.0}
    bumped = analyze_portfolio(holdings, large_cap_floor_cr=100_000, mid_cap_floor_cr=28_000)
    assert bumped["market_cap_weights"] == {"mid_cap": 1.0}


def test_gold_and_etf_excluded_from_sector_and_mcap_weights():
    """Asset allocation includes everything; sector / mcap include stocks only."""
    holdings = [
        _h("RELIANCE", 10, 1500, "Energy", mcap=1_800_000),
        _h("GROWWGOLD", 1000, 50),    # gold
        _h("HDFCNIFTY", 100, 250),    # ETF
    ]
    r = analyze_portfolio(holdings)
    aa = r["asset_allocation"]
    assert set(aa) == {"stock", "gold_silver", "etf"}
    # Sector/mcap weights are *of stocks*, so RELIANCE alone is 100% of each
    assert r["sector_weights"] == {"Energy": 1.0}
    assert r["market_cap_weights"] == {"large_cap": 1.0}
    # stock_value < total_value
    assert r["stock_value"] < r["total_value"]


def test_gaps_identifies_missing_sectors():
    r = analyze_portfolio([_h("INFY", 100, 1500, "Information Technology", mcap=700_000)])
    g = gaps(r)
    assert "Financial Services" in g["sectors"]
    assert "Information Technology" not in g["sectors"]


def test_unrealised_pnl_pct():
    h = {"symbol": "X", "quantity": 10, "avg_price": 100, "ltp": 110,
         "sector": "Industrials", "market_cap_cr": 100_000}
    r = analyze_portfolio([h])
    assert abs(r["unrealised_pnl_pct"] - 0.10) < 1e-9
