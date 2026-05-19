"""End-to-end tagged-holdings builder used by the snapshot command.

Combines GrowwConnector (holdings + LTP) and ScreenerScraper (sector /
industry / market cap), normalising every row into the dict shape the rest
of the codebase consumes:

    {
      "symbol", "quantity", "avg_price", "ltp",
      "broad_sector", "sector", "industry", "industry_raw",
      "market_cap_cr",
    }
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from pennywise.analytics.sectors import canonicalize_sector
from pennywise.connectors.groww import GrowwConnector
from pennywise.connectors.screener import ScreenerScraper
from pennywise.snapshot import Snapshot, stamp_now


def _tag_one(holding: dict, screener: ScreenerScraper) -> dict:
    sym = holding.get("symbol")
    if not sym:
        return holding
    try:
        f = asdict(screener.fetch(sym))
    except Exception as e:
        f = {"error": str(e)}
    industry_for_canon = f.get("broad_sector") or f.get("sector") or f.get("industry")
    holding["broad_sector"] = f.get("broad_sector")
    holding["sector"] = canonicalize_sector(industry_for_canon) if industry_for_canon else canonicalize_sector(sym)
    holding["industry_raw"] = f.get("industry")
    holding["industry"] = f.get("industry")
    holding["market_cap_cr"] = f.get("market_cap_cr")
    holding["fundamentals_error"] = f.get("error")
    return holding


def build_snapshot(*, progress: callable | None = None) -> Snapshot:
    """Fetch live holdings, attach LTP, tag with sector / industry / mcap.

    ``progress`` is called with (idx, total, symbol) after each Screener fetch
    so the CLI can render a progress bar without coupling this module to rich.
    """
    with GrowwConnector() as g:
        holdings = g.holdings_with_ltp()
        positions = g.positions()

    with ScreenerScraper() as scr:
        total = len(holdings)
        for i, h in enumerate(holdings, 1):
            _tag_one(h, scr)
            if progress:
                progress(i, total, h.get("symbol"))

    return Snapshot(fetched_at=stamp_now(), holdings=holdings, positions=positions)
