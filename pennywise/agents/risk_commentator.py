from __future__ import annotations

import json

from pennywise.agents._llm import structured_call
from pennywise.config import load
from pennywise.graph.state import PortfolioState

SYSTEM = """You are PennyWise's Risk Commentator for a retail Indian investor.

You receive deterministic concentration math (HHI, sector / market-cap
weights, top-name weight, gap list). Your job is to produce a SHORT,
actionable narrative — not a summary of the numbers.

Rules:
- Be specific. "Concentrated in Energy at 38%" beats "consider diversifying".
- If HHI < 0.15 and no single name > 15%, say so plainly under headline.
- Treat 'unknown' sector weight as a data-quality issue, not a real risk,
  unless it is >25% of the portfolio.
- ETF / Index exposure is not a concern — note it neutrally if present.
"""

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {
            "type": "string",
            "description": "One sentence naming the single biggest risk (or stating that the portfolio is well-diversified).",
        },
        "concerns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string", "description": "Short label, 3-6 words."},
                    "detail": {"type": "string", "description": "One or two sentences explaining the concern."},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["issue", "detail", "severity"],
            },
        },
        "missing_exposure": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Sectors or market-cap buckets the portfolio is under-allocated to.",
        },
        "suggested_actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short imperative actions, e.g. 'Trim RECLTD to 10%'.",
        },
    },
    "required": ["headline", "concerns", "missing_exposure", "suggested_actions"],
}


def _payload(state: PortfolioState) -> str:
    risk = state.get("risk_metrics", {}) or {}
    holdings = state.get("holdings", []) or []
    top_by_value = sorted(
        ({"symbol": h.get("symbol"), "sector": h.get("sector"),
          "value": (h.get("quantity") or 0) * (h.get("ltp") or 0)}
         for h in holdings),
        key=lambda r: r["value"],
        reverse=True,
    )[:10]
    return json.dumps({
        "risk_metrics": risk,
        "gaps": state.get("gaps", {}),
        "top_holdings_by_value": top_by_value,
        "holding_count": len(holdings),
    }, default=str)


def risk_commentator_node(state: PortfolioState) -> PortfolioState:
    settings = load()
    if not settings.anthropic_api_key:
        return {"risk_commentary": {
            "headline": "LLM commentary skipped (ANTHROPIC_API_KEY not set).",
            "concerns": [], "missing_exposure": state.get("gaps", {}).get("sectors", []),
            "suggested_actions": [],
        }}
    commentary = structured_call(
        system=SYSTEM,
        user_payload=_payload(state),
        tool_name="emit_risk_commentary",
        tool_description="Return the structured risk commentary for this portfolio.",
        input_schema=TOOL_SCHEMA,
        max_tokens=1500,
    )
    return {"risk_commentary": commentary}
