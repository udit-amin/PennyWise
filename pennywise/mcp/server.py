from __future__ import annotations

from fastmcp import FastMCP

from pennywise.agents.risk_analyzer import risk_analyzer_node
from pennywise.agents.risk_commentator import risk_commentator_node
from pennywise.connectors.screener import ScreenerScraper
from pennywise.graph.workflow import run_pennywise
from pennywise.snapshot import Snapshot
from pennywise.tagging import build_snapshot

mcp = FastMCP("pennywise")


@mcp.tool()
def portfolio_snapshot(refresh: bool = False) -> dict:
    """Return the tagged portfolio (holdings + sector/industry/market-cap).

    Reads ~/.pennywise/snapshot.json when fresh (<2h); otherwise rebuilds it
    from Groww + Screener and saves a new snapshot.
    """
    snap = None if refresh else Snapshot.load_if_fresh()
    if snap is None:
        snap = build_snapshot()
        snap.save()
    return {"fetched_at": snap.fetched_at, "holdings": snap.holdings, "positions": snap.positions}


@mcp.tool()
def portfolio_risk(refresh: bool = False) -> dict:
    """Analyse the tagged snapshot: HHI, sector/mcap weights, gaps, and LLM
    narrative commentary. Pure analysis — no external HTTP unless refresh."""
    snap = None if refresh else Snapshot.load_if_fresh()
    if snap is None:
        snap = build_snapshot()
        snap.save()
    state = {
        "holdings": list(snap.holdings),
        "fundamentals": {
            h["symbol"]: {
                "broad_sector": h.get("broad_sector"),
                "sector": h.get("sector"),
                "industry": h.get("industry_raw") or h.get("industry"),
                "market_cap_cr": h.get("market_cap_cr"),
            }
            for h in snap.holdings if h.get("symbol")
        },
    }
    enriched = risk_analyzer_node(state)
    state.update(enriched)
    return {
        "fetched_at": snap.fetched_at,
        "risk": enriched["risk_metrics"],
        "gaps": enriched["gaps"],
        "commentary": risk_commentator_node(state).get("risk_commentary"),
    }


@mcp.tool()
def fundamentals(ticker: str) -> dict:
    """Fetch fundamentals from Screener.in for a single ticker."""
    with ScreenerScraper() as s:
        return s.fetch(ticker).__dict__


@mcp.tool()
def recommend(focus: str = "all") -> dict:
    """Run the full PennyWise LangGraph workflow and return recommendations.

    focus: one of "all", "gaps", "rebalance", "new".
    """
    return run_pennywise(focus=focus)


if __name__ == "__main__":
    mcp.run()
