from __future__ import annotations

from pennywise.connectors.moneycontrol import MoneycontrolNews
from pennywise.graph.state import PortfolioState


def news_node(state: PortfolioState) -> PortfolioState:
    """Filter the Moneycontrol business feed for headlines mentioning each ticker."""
    held = [h["symbol"] for h in state.get("holdings", []) if h.get("symbol")]
    candidates = state.get("candidate_tickers", [])
    tickers = sorted(set(held + list(candidates)))

    out: dict[str, list[dict]] = {t: [] for t in tickers}
    with MoneycontrolNews() as mc:
        try:
            feed = mc.fetch()
            for t in tickers:
                hits = mc.filter_for(feed, [t])
                out[t] = [
                    {
                        "title": n.title,
                        "link": n.link,
                        "published": n.published.isoformat() if n.published else None,
                    }
                    for n in hits[:5]
                ]
        except Exception:
            pass
    return {"news": out}
