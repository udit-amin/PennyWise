from __future__ import annotations

from dataclasses import asdict

from pennywise.connectors.yfinance_client import YFinanceClient
from pennywise.graph.state import PortfolioState


def technicals_node(state: PortfolioState) -> PortfolioState:
    """Pull technical indicators from yfinance for every held + candidate ticker.

    Uses ``fetch_batch`` to download all tickers in a single HTTP request
    (~3-5s) instead of looping one-by-one (~2-4s × N tickers).
    """
    held = [h["symbol"] for h in state.get("holdings", []) if h.get("symbol")]
    candidates = state.get("candidate_tickers", [])
    tickers = sorted(set(held + list(candidates)))

    yfc = YFinanceClient()
    batch = yfc.fetch_batch(tickers)
    return {"technicals": {t: asdict(tech) for t, tech in batch.items()}}
