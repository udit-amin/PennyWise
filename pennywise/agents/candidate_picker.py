from __future__ import annotations

import csv
from importlib import resources

from pennywise.graph.state import PortfolioState

UNIVERSE_RESOURCE = ("pennywise.data", "universe.csv")


def _load_universe() -> list[dict]:
    try:
        with resources.files(UNIVERSE_RESOURCE[0]).joinpath(UNIVERSE_RESOURCE[1]).open("r") as fh:
            return list(csv.DictReader(fh))
    except (FileNotFoundError, ModuleNotFoundError):
        return []


def candidate_picker_node(state: PortfolioState) -> PortfolioState:
    """Deterministically pick up to 8 candidate tickers that fill identified gaps.

    Selection is structural (sector + mcap match). The Synthesizer LLM ranks
    them in the next node using fundamentals/technicals/news.
    """
    if state.get("focus") == "rebalance":
        return {"candidate_tickers": []}

    universe = _load_universe()
    held = {h.get("symbol") for h in state.get("holdings", [])}
    gap = state.get("gaps", {})
    target_sectors = set(gap.get("sectors", []))
    target_buckets = set(gap.get("market_cap_buckets", []))

    picks: list[str] = []
    for row in universe:
        if row["symbol"] in held:
            continue
        if target_sectors and row.get("sector") not in target_sectors:
            continue
        if target_buckets and row.get("market_cap_bucket") not in target_buckets:
            continue
        picks.append(row["symbol"])
        if len(picks) >= 8:
            break

    return {"candidate_tickers": picks}
