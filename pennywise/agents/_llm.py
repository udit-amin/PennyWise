"""Shared helper for structured LLM calls.

We don't ask Claude to "reply with JSON" and then string-parse the response —
that breaks the moment a quote, brace, or max_tokens cut-off lands the wrong
way. Instead every LLM node defines a single tool schema and forces Claude
to call it via `tool_choice`. The SDK gives us a parsed dict back, guaranteed
well-formed. Truncation becomes "missing fields", not "JSONDecodeError".

When ``reasoning=True`` we enable Claude's extended-thinking mode. The model
gets a private scratchpad to plan before emitting the tool call, which
noticeably improves recommendation quality on synthesis/critic nodes (the
two places where the model has to weigh many signals at once). Thinking
tokens are billed but invisible to downstream code — we only consume the
final ``tool_use`` block.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import httpx
from anthropic import Anthropic

from pennywise.config import load


@lru_cache(maxsize=4)
def _client(api_key: str) -> Anthropic:
    """Shared Anthropic client (thread-safe): reuses HTTP connections across
    the parallel graph nodes / job threads instead of one client per call,
    and gives every call retries + a hard timeout so a transient Anthropic
    blip doesn't fail a 30-100s workflow or hang it forever."""
    return Anthropic(
        api_key=api_key,
        max_retries=int(os.getenv("PENNYWISE_LLM_MAX_RETRIES", "3")),
        timeout=httpx.Timeout(
            float(os.getenv("PENNYWISE_LLM_TIMEOUT_S", "120")), connect=5.0
        ),
    )


def structured_call(
    *,
    system: str,
    user_payload: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    max_tokens: int = 2000,
    reasoning: bool = False,
    effort: str | None = None,
) -> dict[str, Any]:
    """Run a single Claude call constrained to emit one tool invocation.

    Args:
        reasoning: when True, enable Claude's adaptive extended-thinking
            mode. The model decides how much to think before emitting the
            tool call. Recommended for synthesis / critic nodes that weigh
            many signals at once.
        effort: how hard to reason — "low" | "medium" | "high". Defaults to
            ``PENNYWISE_REASONING_EFFORT`` env var, else "medium". Only
            consumed when ``reasoning=True``.

    Returns the tool's ``input`` dict directly. Raises if no tool call landed.
    """
    settings = load()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    client = _client(settings.anthropic_api_key)

    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "system": system,
        "tools": [{
            "name": tool_name,
            "description": tool_description,
            "input_schema": input_schema,
        }],
        "messages": [{"role": "user", "content": user_payload}],
    }
    if reasoning:
        # Newer Claude models (opus-4.x, sonnet-4.x) use *adaptive* thinking:
        # the API decides how much scratchpad to allocate based on the
        # ``output_config.effort`` knob. Adaptive thinking is compatible with
        # tool_choice, unlike the old budget-tokens mode.
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": effort or settings.reasoning_effort}
        kwargs["tool_choice"] = {"type": "tool", "name": tool_name}
    else:
        kwargs["tool_choice"] = {"type": "tool", "name": tool_name}

    msg = client.messages.create(**kwargs)
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input)
    raise RuntimeError(
        f"Claude returned no tool_use block for {tool_name!r}. "
        f"stop_reason={msg.stop_reason!r}, "
        f"content_types={[getattr(b, 'type', None) for b in msg.content]}"
    )
