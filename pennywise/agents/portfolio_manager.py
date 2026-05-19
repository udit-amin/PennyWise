from __future__ import annotations

from pennywise.connectors.groww import GrowwConnector
from pennywise.graph.state import PortfolioState
from pennywise.snapshot import Snapshot
from pennywise.tagging import build_snapshot

# Workflow nodes prefer the on-disk snapshot when fresh — keeps Screener
# rate-limit pressure to one shot per session and makes every run after the
# first ``pennywise snapshot`` near-instant.
SNAPSHOT_MAX_AGE_S = 2 * 60 * 60  # 2 hours


def portfolio_manager_node(state: PortfolioState) -> PortfolioState:
    snap = Snapshot.load_if_fresh(max_age_s=SNAPSHOT_MAX_AGE_S)
    if snap is None:
        snap = build_snapshot()
        snap.save()
    return {"holdings": list(snap.holdings), "positions": list(snap.positions)}


def portfolio_manager_node_live(state: PortfolioState) -> PortfolioState:
    """Force a live re-fetch even if a snapshot exists. Currently unused;
    handy if you ever want a 'no-cache' workflow entry point."""
    with GrowwConnector() as g:
        holdings = g.holdings_with_ltp()
        positions = g.positions()
    return {"holdings": holdings, "positions": positions}
