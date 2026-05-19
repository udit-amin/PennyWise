from __future__ import annotations

import json

from pennywise.agents._llm import structured_call
from pennywise.graph.state import PortfolioState

SYSTEM = """You are PennyWise's Strategy Synthesizer for a retail Indian
investor on Groww (NSE/BSE). You convert deterministic portfolio + market
signals into concrete actions.

CONTRACT (non-negotiable — the Critic will reject violations):

1. Every currently-held ticker MUST appear in the output, with one of:
   HOLD, ADD, TRIM, SELL. Never omit a holding silently.

2. For each gap sector / market-cap bucket reported in ``gaps``, propose
   at least one BUY_NEW recommendation drawn from ``candidate_tickers`` (if
   any candidate exists for that gap). Pick the candidate with the strongest
   fundamentals + technicals.

3. Rationale rules:
   - Cite at least one fundamental signal (PE / PB / ROE / D-E / market cap).
   - Cite at least one technical signal (RSI / SMA position / MACD).
   - Cite news only if a relevant headline exists; explicitly say
     "no recent news" otherwise — do NOT skip the recommendation.
   - When signals conflict, prefer HOLD over SELL/BUY and say which
     signals conflicted.

4. TRIM is the right action for any name whose weight exceeds the
   ``top_name_flag`` from risk_metrics OR whose technicals are clearly
   bearish (RSI < 35, price below SMA200, MACD negative) even if
   fundamentals are intact.

5. Confidence calibration:
   - 0.8+: all three signal types agree.
   - 0.5-0.8: two signals agree, one is missing or weak.
   - <0.5: should usually be HOLD, not a directional call.

Never return an empty list — if you are about to, re-read the holdings
and emit a HOLD per name with the rationale "insufficient signal
divergence to act".
"""

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["BUY_NEW", "ADD", "HOLD", "TRIM", "SELL"],
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "Cites fundamental + technical signals (+ news "
                            "if any). 2-4 sentences."
                        ),
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "target_weight_pct": {
                        "type": "number",
                        "description": "Suggested target portfolio weight (0-100). Optional.",
                    },
                },
                "required": ["ticker", "action", "rationale", "confidence"],
            },
        },
        "summary": {
            "type": "string",
            "description": "One-paragraph plain-English summary the user reads first.",
        },
    },
    "required": ["recommendations", "summary"],
}


def _compact_payload(state: PortfolioState) -> str:
    """Trim the state down to what the LLM actually needs. Keeps the prompt
    cheap and prevents irrelevant fields from drowning the signal."""
    holdings = [
        {
            "symbol": h.get("symbol"),
            "sector": h.get("sector"),
            "market_cap_cr": h.get("market_cap_cr"),
            "value": (h.get("quantity") or 0) * (h.get("ltp") or 0),
            "avg_price": h.get("avg_price"),
            "ltp": h.get("ltp"),
        }
        for h in state.get("holdings", [])
    ]
    total = sum(h["value"] for h in holdings) or 1.0
    for h in holdings:
        h["weight_pct"] = round(100 * h["value"] / total, 2)
        h.pop("value")

    return json.dumps({
        "focus": state.get("focus", "all"),
        "holdings": holdings,
        "risk_metrics": state.get("risk_metrics", {}),
        "gaps": state.get("gaps", {}),
        "risk_commentary": state.get("risk_commentary", {}),
        "fundamentals": state.get("fundamentals", {}),
        "technicals": state.get("technicals", {}),
        "news_headlines_by_ticker": {
            t: [n.get("title") for n in items][:3]
            for t, items in (state.get("news") or {}).items()
        },
        "candidate_tickers": state.get("candidate_tickers", []),
        "prior_critique": state.get("critique", {}),
    }, default=str)


def synthesizer_node(state: PortfolioState) -> PortfolioState:
    result = structured_call(
        system=SYSTEM,
        user_payload=_compact_payload(state),
        tool_name="emit_recommendations",
        tool_description="Return the structured Buy/Hold/Sell recommendations.",
        input_schema=TOOL_SCHEMA,
        max_tokens=6000,
        reasoning=True,
    )
    return {
        "draft_recommendations": result.get("recommendations", []),
        "draft_summary": result.get("summary", ""),
    }
