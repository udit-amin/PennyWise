from __future__ import annotations

from dataclasses import asdict

from pennywise.connectors.yfinance_client import YFinanceClient
from pennywise.graph.state import PortfolioState


def technicals_node(state: PortfolioState) -> PortfolioState:
    """Pull technical indicators from yfinance for every held + candidate ticker."""
    held = [h["symbol"] for h in state.get("holdings", []) if h.get("symbol")]
    candidates = state.get("candidate_tickers", [])
    tickers = sorted(set(held + list(candidates)))

    yfc = YFinanceClient()
    out: dict[str, dict] = {}
    for t in tickers:
        try:
            out[t] = asdict(yfc.fetch(t))
        except Exception as e:
            out[t] = {"ticker": t, "error": str(e)}
    return {"technicals": out}
