from __future__ import annotations

from pennywise.analytics.risk import analyze_portfolio, gaps
from pennywise.analytics.sectors import canonicalize_sector
from pennywise.config import load
from pennywise.graph.state import PortfolioState


def _enrich_with_fundamentals(holdings: list[dict], fundamentals: dict[str, dict]) -> list[dict]:
    """Stamp each holding with `sector` (canonicalized) and `market_cap_cr`."""
    for h in holdings:
        sym = h.get("symbol")
        f = fundamentals.get(sym, {}) if sym else {}
        # Prefer the GICS top-level "Broad Sector" — it's the most stable label
        # for canonicalisation. Fall back through the hierarchy if missing.
        industry_for_canon = f.get("broad_sector") or f.get("sector") or f.get("industry")
        h["industry_raw"] = f.get("industry")
        h["broad_sector"] = f.get("broad_sector")
        sector = canonicalize_sector(industry_for_canon)
        # Screener has no pages for ETFs / gold-coin products. Tag from symbol
        # so they sit in their own bucket instead of polluting 'unknown'.
        if sector == "unknown" and sym:
            sector = canonicalize_sector(sym)
        h["sector"] = sector
        h["market_cap_cr"] = f.get("market_cap_cr")
    return holdings


def risk_analyzer_node(state: PortfolioState) -> PortfolioState:
    settings = load()
    holdings = _enrich_with_fundamentals(
        list(state.get("holdings", [])),
        state.get("fundamentals", {}),
    )
    risk = analyze_portfolio(
        holdings,
        hhi_flag=settings.hhi_flag,
        top_name_flag=settings.top_name_flag,
        large_cap_floor_cr=settings.large_cap_floor_cr,
        mid_cap_floor_cr=settings.mid_cap_floor_cr,
    )
    return {"risk_metrics": risk, "gaps": gaps(risk), "holdings": holdings}
