from __future__ import annotations

from langgraph.graph import END, StateGraph

from pennywise.agents.candidate_picker import candidate_picker_node
from pennywise.agents.fundamentals import fundamentals_node
from pennywise.agents.news import news_node
from pennywise.agents.portfolio_manager import portfolio_manager_node
from pennywise.agents.risk_analyzer import risk_analyzer_node
from pennywise.agents.risk_commentator import risk_commentator_node
from pennywise.agents.strategy_critic import critic_node, finalizer_node
from pennywise.agents.strategy_synthesizer import synthesizer_node
from pennywise.agents.technicals import technicals_node
from pennywise.graph.state import PortfolioState

MAX_REVISIONS = 1


def _after_critic(state: PortfolioState) -> str:
    critique = state.get("critique") or {}
    if critique.get("verdict") == "revise" and state.get("revision_count", 0) <= MAX_REVISIONS:
        return "synthesizer"
    return "finalizer"


def build_graph():
    g = StateGraph(PortfolioState)
    g.add_node("portfolio_manager", portfolio_manager_node)
    g.add_node("fundamentals_first", fundamentals_node)   # for risk/gaps pre-candidates
    g.add_node("risk_first", risk_analyzer_node)
    g.add_node("candidate_picker", candidate_picker_node)
    g.add_node("fundamentals_full", fundamentals_node)    # adds candidate tickers
    g.add_node("technicals", technicals_node)
    g.add_node("news", news_node)
    g.add_node("risk_analyzer", risk_analyzer_node)
    g.add_node("risk_commentator", risk_commentator_node)
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("critic", critic_node)
    g.add_node("finalizer", finalizer_node)

    # ── sequential setup: holdings → initial fundamentals → risk → candidates
    g.set_entry_point("portfolio_manager")
    g.add_edge("portfolio_manager", "fundamentals_first")
    g.add_edge("fundamentals_first", "risk_first")
    g.add_edge("risk_first", "candidate_picker")

    # ── parallel fan-out: data-fetch nodes run concurrently ──
    # fundamentals_full, technicals, and news all read the same state
    # (holdings + candidate_tickers) and write to independent keys
    # (fundamentals, technicals, news). LangGraph waits for all three
    # to complete before running risk_analyzer.
    g.add_edge("candidate_picker", "fundamentals_full")
    g.add_edge("candidate_picker", "technicals")
    g.add_edge("candidate_picker", "news")

    # ── fan-in: risk_analyzer fires after all three data nodes finish
    g.add_edge("fundamentals_full", "risk_analyzer")
    g.add_edge("technicals", "risk_analyzer")
    g.add_edge("news", "risk_analyzer")

    # ── sequential tail: risk → commentary → synthesis → critique
    g.add_edge("risk_analyzer", "risk_commentator")
    g.add_edge("risk_commentator", "synthesizer")
    g.add_edge("synthesizer", "critic")
    g.add_conditional_edges("critic", _after_critic, {"synthesizer": "synthesizer", "finalizer": "finalizer"})
    g.add_edge("finalizer", END)
    return g.compile()


def run_pennywise(
    focus: str = "all",
    *,
    initial_holdings: list[dict] | None = None,
    initial_positions: list[dict] | None = None,
) -> dict:
    """Run the full workflow.

    ``initial_holdings``/``initial_positions`` let the API seed a per-user
    portfolio so the entry node never touches local/shared credentials; when
    omitted (CLI path) the portfolio_manager node fetches from Groww itself.
    """
    graph = build_graph()
    initial: PortfolioState = {"focus": focus, "revision_count": 0}
    if initial_holdings is not None:
        initial["holdings"] = list(initial_holdings)
        initial["positions"] = list(initial_positions or [])
    final = graph.invoke(initial)
    return {
        "focus": focus,
        "risk_metrics": final.get("risk_metrics"),
        "gaps": final.get("gaps"),
        "risk_commentary": final.get("risk_commentary"),
        "summary": final.get("final_summary", ""),
        "recommendations": final.get("final_recommendations", []),
        "critique": final.get("critique"),
    }
