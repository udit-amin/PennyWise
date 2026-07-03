from __future__ import annotations

from collections import defaultdict
from typing import TypedDict

from pennywise.analytics.classification import asset_class


class Holding(TypedDict, total=False):
    symbol: str
    quantity: float
    avg_price: float
    ltp: float
    sector: str
    market_cap_cr: float | None
    asset_class: str  # "stock" | "gold_silver" | "etf" | "unknown"


def _value(h: Holding) -> float:
    # ltp can be an explicit None (failed LTP lookup, or an uploaded
    # statement without a price column) — treat as zero value.
    return float(h.get("quantity") or 0) * float(h.get("ltp") or 0)


def _bucket_mcap(cr: float | None, *, large_floor: float, mid_floor: float) -> str:
    """SEBI/AMFI-aligned bucket. Floors are configurable because AMFI republishes
    the top-100 and top-250 cutoffs twice a year."""
    if cr is None:
        return "unknown"
    if cr >= large_floor:
        return "large_cap"
    if cr >= mid_floor:
        return "mid_cap"
    return "small_cap"


def _ensure_asset_class(h: Holding) -> str:
    cls = h.get("asset_class")
    if not cls:
        cls = asset_class(h.get("symbol"), h.get("sector"))
        h["asset_class"] = cls
    return cls


def analyze_portfolio(
    holdings: list[Holding],
    *,
    hhi_flag: float = 0.25,
    top_name_flag: float = 0.20,
    large_cap_floor_cr: float = 80_000,
    mid_cap_floor_cr: float = 28_000,
) -> dict:
    """Risk metrics for a tagged portfolio.

    Two layers:
      * Asset allocation is computed over the FULL portfolio (stocks +
        gold/silver + ETFs).
      * Sector weights, market-cap weights, HHI, and concentration flags are
        computed over STOCKS ONLY — matching Groww's portfolio page and the
        way SEBI's mcap categories are defined.
    """
    if not holdings:
        return {
            "total_value": 0.0,
            "stock_value": 0.0,
            "asset_allocation": {},
            "sector_weights": {},
            "market_cap_weights": {},
            "hhi_sector": 0.0,
            "concentration_flag": False,
            "top_holding": None,
            "unrealised_pnl_pct": 0.0,
        }

    total = sum(_value(h) for h in holdings) or 1.0
    asset_w: dict[str, float] = defaultdict(float)
    sector_w: dict[str, float] = defaultdict(float)
    mcap_w: dict[str, float] = defaultdict(float)
    name_w: dict[str, float] = {}
    cost = 0.0
    stock_value = 0.0

    for h in holdings:
        v = _value(h)
        cls = _ensure_asset_class(h)
        asset_w[cls] += v / total
        cost += float(h.get("quantity", 0)) * float(h.get("avg_price", 0))
        if cls == "stock":
            stock_value += v

    if stock_value > 0:
        for h in holdings:
            if _ensure_asset_class(h) != "stock":
                continue
            v = _value(h)
            w = v / stock_value
            sector_w[h.get("sector") or "unknown"] += w
            mcap_w[_bucket_mcap(h.get("market_cap_cr"),
                                large_floor=large_cap_floor_cr,
                                mid_floor=mid_cap_floor_cr)] += w
            name_w[h["symbol"]] = name_w.get(h["symbol"], 0.0) + w

    hhi = sum(w * w for w in sector_w.values())
    top = None
    if name_w:
        top_symbol, top_weight = max(name_w.items(), key=lambda kv: kv[1])
        top = {"symbol": top_symbol, "weight": top_weight}
    unrealised = (total - cost) / cost if cost > 0 else 0.0
    concentrated = hhi > hhi_flag or (top is not None and top["weight"] > top_name_flag)

    return {
        "total_value": total,
        "stock_value": stock_value,
        "asset_allocation": dict(asset_w),
        "sector_weights": dict(sector_w),
        "market_cap_weights": dict(mcap_w),
        "hhi_sector": hhi,
        "concentration_flag": concentrated,
        "top_holding": top,
        "unrealised_pnl_pct": unrealised,
    }


def gaps(risk: dict, target_sectors: list[str] | None = None) -> dict:
    """Identify sectors / mcap buckets that look under-allocated."""
    target_sectors = target_sectors or [
        "Financial Services",
        "Information Technology",
        "Consumer Goods",
        "Healthcare",
        "Energy",
        "Industrials",
    ]
    sector_w = risk.get("sector_weights", {})
    mcap_w = risk.get("market_cap_weights", {})

    sector_gaps = [s for s in target_sectors if sector_w.get(s, 0.0) < 0.05]
    mcap_gaps = [b for b in ("large_cap", "mid_cap", "small_cap") if mcap_w.get(b, 0.0) < 0.10]

    return {"sectors": sector_gaps, "market_cap_buckets": mcap_gaps}
