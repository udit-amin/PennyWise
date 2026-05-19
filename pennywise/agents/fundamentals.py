from __future__ import annotations

from dataclasses import asdict

from pennywise.connectors.screener import ScreenerScraper
from pennywise.graph.state import PortfolioState


def _seed_from_snapshot(state: PortfolioState) -> dict[str, dict]:
    """Pre-populate fundamentals from any tags already attached by snapshot."""
    seed: dict[str, dict] = dict(state.get("fundamentals") or {})
    for h in state.get("holdings", []):
        sym = h.get("symbol")
        if not sym or sym in seed:
            continue
        if h.get("broad_sector") or h.get("industry") or h.get("market_cap_cr") is not None:
            seed[sym] = {
                "ticker": sym,
                "broad_sector": h.get("broad_sector"),
                "sector": h.get("sector"),
                "industry": h.get("industry_raw") or h.get("industry"),
                "market_cap_cr": h.get("market_cap_cr"),
            }
    return seed


def _tickers_needing_research(state: PortfolioState, already: dict[str, dict]) -> list[str]:
    held = [h["symbol"] for h in state.get("holdings", []) if h.get("symbol")]
    candidates = state.get("candidate_tickers", [])
    wanted = sorted(set(held + list(candidates)))
    return [t for t in wanted if t not in already]


def fundamentals_node(state: PortfolioState) -> PortfolioState:
    """Pull Screener fundamentals only for tickers that aren't already tagged.

    The snapshot step tags every held ticker upfront, so in steady state this
    node only fires for *new* candidate tickers proposed by candidate_picker.
    """
    out = _seed_from_snapshot(state)
    todo = _tickers_needing_research(state, out)
    if todo:
        with ScreenerScraper() as scr:
            for t in todo:
                try:
                    out[t] = asdict(scr.fetch(t))
                except Exception as e:
                    out[t] = {"ticker": t, "error": str(e)}
    return {"fundamentals": out}
