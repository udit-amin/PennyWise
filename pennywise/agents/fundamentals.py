from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict

from pennywise.connectors.screener import ScreenerScraper
from pennywise.graph.state import PortfolioState

# Screener rate-limits after ~15-20 consecutive requests from one IP.
# The ScreenerScraper already throttles at 0.8s per request, but running
# 3 concurrent scrapers tripled throughput while staying under the 429
# threshold in testing.
_MAX_SCREENER_WORKERS = 3


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


def _fetch_one(ticker: str) -> dict:
    """Fetch a single ticker in its own ScreenerScraper (own throttle timer)."""
    with ScreenerScraper() as scr:
        return asdict(scr.fetch(ticker))


def fundamentals_node(state: PortfolioState) -> PortfolioState:
    """Pull Screener fundamentals only for tickers that aren't already tagged.

    The snapshot step tags every held ticker upfront, so in steady state this
    node only fires for *new* candidate tickers proposed by candidate_picker.

    When there are multiple tickers to fetch, we use a thread pool (capped at
    3 workers) to scrape concurrently — each thread gets its own
    ScreenerScraper with its own per-connection throttle, keeping us under
    Screener's rate limit while tripling throughput.
    """
    out = _seed_from_snapshot(state)
    todo = _tickers_needing_research(state, out)
    if not todo:
        return {"fundamentals": out}

    if len(todo) <= 2:
        # Not worth the thread-pool overhead for 1-2 tickers.
        with ScreenerScraper() as scr:
            for t in todo:
                try:
                    out[t] = asdict(scr.fetch(t))
                except Exception as e:
                    out[t] = {"ticker": t, "error": str(e)}
    else:
        with ThreadPoolExecutor(max_workers=_MAX_SCREENER_WORKERS) as pool:
            futs = {pool.submit(_fetch_one, t): t for t in todo}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    out[t] = fut.result()
                except Exception as e:
                    out[t] = {"ticker": t, "error": str(e)}

    return {"fundamentals": out}
