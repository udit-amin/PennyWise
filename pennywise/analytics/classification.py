"""Asset-class classification for portfolio holdings.

Groww's portfolio page splits the universe by asset class first (Stocks vs
Gold/Silver vs ETFs) and only computes market-cap weights *within stocks*.
We do the same so non-equity instruments don't pollute the mcap pie.
"""
from __future__ import annotations


def asset_class(symbol: str | None, sector: str | None = None) -> str:
    """Return one of: 'gold_silver', 'etf', 'stock', 'unknown'.

    Order of precedence: symbol pattern → canonicalized sector → default stock.
    Symbol patterns are checked first because Screener has no pages for these
    instruments, so the canonical sector for them is already 'ETF / Index'.
    """
    if symbol:
        sym = symbol.upper()
        if any(tok in sym for tok in ("GOLD", "SLVR", "SILVER")):
            return "gold_silver"
        if any(tok in sym for tok in ("NIFTY", "ETF", "BEES", "SENSEX", "LIQUID")):
            return "etf"
    if sector == "ETF / Index":
        return "etf"
    if not symbol:
        return "unknown"
    return "stock"
