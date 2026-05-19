from __future__ import annotations

from typing import TypedDict


class PortfolioState(TypedDict, total=False):
    focus: str  # "gaps" | "rebalance" | "new" | "all"

    holdings: list[dict]
    positions: list[dict]

    fundamentals: dict[str, dict]  # ticker -> Fundamentals.__dict__
    technicals: dict[str, dict]    # ticker -> Technicals.__dict__
    news: dict[str, list[dict]]    # ticker -> list of news items

    risk_metrics: dict
    gaps: dict
    risk_commentary: dict

    candidate_tickers: list[str]   # new-ticker universe filtered to fill gaps

    draft_recommendations: list[dict]
    draft_summary: str
    critique: dict                  # {"score": float, "issues": [str], "verdict": "accept"|"revise"}
    revision_count: int

    final_recommendations: list[dict]
    final_summary: str
