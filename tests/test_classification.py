from pennywise.analytics.classification import asset_class


def test_gold_and_silver_symbols():
    assert asset_class("GROWWGOLD") == "gold_silver"
    assert asset_class("GROWWSLVR") == "gold_silver"
    assert asset_class("GOLDBEES") == "gold_silver"
    assert asset_class("SILVERBEES") == "gold_silver"


def test_index_etfs():
    assert asset_class("HDFCNIFTY") == "etf"
    assert asset_class("NIFTYBEES") == "etf"
    assert asset_class("LIQUIDBEES") == "etf"
    assert asset_class("JUNIORBEES") == "etf"


def test_regular_stocks():
    assert asset_class("RELIANCE") == "stock"
    assert asset_class("HDFCBANK") == "stock"
    assert asset_class("TCS") == "stock"


def test_sector_fallback_when_symbol_inconclusive():
    # Hypothetical broad-market ETF without an obvious symbol token
    assert asset_class("MYFUNDXYZ", sector="ETF / Index") == "etf"


def test_missing_symbol():
    assert asset_class(None) == "unknown"
    assert asset_class("") == "unknown"
