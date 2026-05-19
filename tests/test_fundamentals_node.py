from pennywise.agents.fundamentals import fundamentals_node


def test_fundamentals_node_skips_already_tagged_holdings(monkeypatch):
    """When snapshot has already tagged every held ticker, no Screener
    fetches should happen. We verify by replacing ScreenerScraper with a
    sentinel that raises on construction."""
    import pennywise.agents.fundamentals as fmod

    class ExplodingScraper:
        def __init__(self, *a, **kw):
            raise AssertionError("ScreenerScraper must not be constructed when all holdings are pre-tagged")

    monkeypatch.setattr(fmod, "ScreenerScraper", ExplodingScraper)

    state = {
        "holdings": [
            {"symbol": "INFY", "broad_sector": "Information Technology", "industry": "IT", "market_cap_cr": 700_000},
            {"symbol": "RELIANCE", "broad_sector": "Energy", "industry": "Refineries", "market_cap_cr": 1_800_000},
        ],
        "candidate_tickers": [],
    }
    out = fundamentals_node(state)
    assert set(out["fundamentals"]) == {"INFY", "RELIANCE"}
    assert out["fundamentals"]["INFY"]["broad_sector"] == "Information Technology"


def test_fundamentals_node_only_fetches_new_candidates(monkeypatch):
    """Held tickers are pre-tagged; candidates are not. Scraper should only
    be hit for the candidate symbols."""
    import pennywise.agents.fundamentals as fmod

    fetched: list[str] = []

    class FakeFundamentals:
        def __init__(self, ticker):
            self.ticker = ticker
            self.broad_sector = "Healthcare"
            self.sector = None
            self.industry = "Pharma"
            self.market_cap_cr = 50_000
            self.pe = self.pb = self.debt_to_equity = self.roe = None

    class FakeScraper:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def fetch(self, t):
            fetched.append(t)
            return FakeFundamentals(t)

    monkeypatch.setattr(fmod, "ScreenerScraper", FakeScraper)

    state = {
        "holdings": [
            {"symbol": "INFY", "broad_sector": "Information Technology", "industry": "IT", "market_cap_cr": 700_000},
        ],
        "candidate_tickers": ["CIPLA", "SUNPHARMA"],
    }
    out = fundamentals_node(state)
    assert sorted(fetched) == ["CIPLA", "SUNPHARMA"]
    assert set(out["fundamentals"]) == {"INFY", "CIPLA", "SUNPHARMA"}
