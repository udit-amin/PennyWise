from __future__ import annotations

import json

from pennywise.agents._llm import structured_call
from pennywise.graph.state import PortfolioState

SYSTEM = """You are PennyWise's Strategy Critic. Your job is to find holes in
the Synthesizer's recommendations before they reach the user.

Check, for each recommendation:
- Are all three signal types (fundamental, technical, news) used or explicitly
  noted as missing? If a recommendation relies on only one signal, flag it.
- Does a BUY_NEW pick actually fill a gap listed in `gaps`?
- Is confidence claimed without supporting evidence?
- Does any recommendation contradict the portfolio's stated risk metrics?

Set verdict='revise' if score < 0.7 OR any single high-impact pick has issues.
"""

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "issues": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
    },
    "required": ["score", "issues", "verdict"],
}


def critic_node(state: PortfolioState) -> PortfolioState:
    payload = json.dumps({
        "recommendations": state.get("draft_recommendations", []),
        "risk": state.get("risk_metrics", {}),
        "gaps": state.get("gaps", {}),
        "fundamentals": state.get("fundamentals", {}),
        "technicals": state.get("technicals", {}),
        "news": state.get("news", {}),
    }, default=str)
    critique = structured_call(
        system=SYSTEM,
        user_payload=payload,
        tool_name="emit_critique",
        tool_description="Return the structured critique of the recommendations.",
        input_schema=TOOL_SCHEMA,
        max_tokens=2000,
        reasoning=True,
    )
    return {"critique": critique, "revision_count": state.get("revision_count", 0) + 1}


def finalizer_node(state: PortfolioState) -> PortfolioState:
    return {
        "final_recommendations": state.get("draft_recommendations", []),
        "final_summary": state.get("draft_summary", ""),
    }
