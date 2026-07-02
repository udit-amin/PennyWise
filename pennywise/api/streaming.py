"""WebSocket streaming adapter for the chat interface.

Adapts the existing chat tool infrastructure (TOOL_SPECS, TOOL_IMPLS,
SYSTEM prompt) to work over a WebSocket connection with streaming
responses.

The protocol:
  Client → Server: {"type": "message", "text": "...", "session_id": "..." | null}
  Server → Client: {"type": "tool_call", "name": "...", "input": {...}}
  Server → Client: {"type": "tool_result", "name": "...", "duration_ms": int}
  Server → Client: {"type": "text_delta", "delta": "..."}
  Server → Client: {"type": "text_done", "session_id": "..."}
  Server → Client: {"type": "error", "detail": "..."}
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from functools import lru_cache
from typing import Any

import httpx
from anthropic import AsyncAnthropic
from fastapi import WebSocket

from pennywise.chat import SYSTEM, TOOL_IMPLS, TOOL_SPECS
from pennywise.config import load

# Wall-clock cap on one full chat turn (all model iterations + tools).
CHAT_TURN_TIMEOUT_S = int(os.getenv("PENNYWISE_CHAT_TURN_TIMEOUT_S", "300"))

# Per-tool execution timeouts; a hung scraper degrades the tool result
# instead of hanging the WebSocket.
_DEFAULT_TOOL_TIMEOUT_S = 30
_TOOL_TIMEOUTS_S = {
    "list_recommendations": 240,  # full LangGraph workflow
    "fetch_technicals": 45,       # yfinance can be slow on first hit
    "get_holdings": 120,          # may build a fresh snapshot (Groww + Screener)
    "get_risk_metrics": 120,
    "analyze_ticker": 120,
}


@lru_cache(maxsize=4)
def _client(api_key: str) -> AsyncAnthropic:
    """Shared async client — connection reuse + retries + timeout, instead of
    a new client per chat turn."""
    return AsyncAnthropic(
        api_key=api_key,
        max_retries=2,
        timeout=httpx.Timeout(120.0, connect=5.0),
    )


async def stream_chat_turn(
    ws: WebSocket,
    history: list[dict],
    user_text: str,
    session_id: str,
    *,
    tool_impls: dict | None = None,
    max_iterations: int = 6,
) -> list[dict]:
    """Run one chat turn with streaming.

    Mutates and returns ``history`` with the new user + assistant turns appended.
    Sends streaming events over ``ws`` as the model produces output.
    ``tool_impls`` carries the per-user tool table (see chat.make_tool_impls);
    defaults to the CLI table for backwards compatibility.
    """
    impls = tool_impls if tool_impls is not None else TOOL_IMPLS
    settings = load()
    client = _client(settings.anthropic_api_key)

    history.append({"role": "user", "content": user_text})

    async with asyncio.timeout(CHAT_TURN_TIMEOUT_S):
        return await _run_turn(ws, client, settings, impls, history, session_id, max_iterations)


async def _run_turn(
    ws: WebSocket,
    client: AsyncAnthropic,
    settings,
    impls: dict,
    history: list[dict],
    session_id: str,
    max_iterations: int,
) -> list[dict]:
    for _ in range(max_iterations):
        kwargs: dict[str, Any] = {
            "model": settings.llm_model,
            "max_tokens": 4096,
            "system": SYSTEM,
            "tools": TOOL_SPECS,
            "messages": history,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "low"},
        }

        # Use streaming API for text deltas
        collected_content: list[dict] = []
        text_buffer = ""

        async with client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_start":
                        block = event.content_block
                        if hasattr(block, "type"):
                            if block.type == "tool_use":
                                await ws.send_json({
                                    "type": "tool_call",
                                    "name": block.name,
                                    "input": {},
                                })
                    elif event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, "text"):
                            text_buffer += delta.text
                            await ws.send_json({
                                "type": "text_delta",
                                "delta": delta.text,
                            })

            # Must run inside the stream context — the response is closed
            # (and get_final_message unreliable) once the block exits.
            msg = await stream.get_final_message()
        # Serialize the assistant content for history
        assistant_content = []
        for block in msg.content:
            if hasattr(block, "model_dump"):
                assistant_content.append(block.model_dump(exclude_none=True))
            elif isinstance(block, dict):
                assistant_content.append(block)

        history.append({"role": "assistant", "content": assistant_content})

        # Check for tool uses
        tool_uses = [b for b in msg.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            # Done — no more tools, send completion signal
            await ws.send_json({"type": "text_done", "session_id": session_id})
            return history

        # Execute tools in parallel, each behind its own timeout so a hung
        # scraper degrades that tool's result instead of stalling the socket.
        tool_results = []
        tasks = []
        for tu in tool_uses:
            await ws.send_json({
                "type": "tool_call",
                "name": tu.name,
                "input": dict(tu.input),
            })
            timeout_s = _TOOL_TIMEOUTS_S.get(tu.name, _DEFAULT_TOOL_TIMEOUT_S)
            coro = asyncio.wait_for(
                asyncio.to_thread(_execute_tool, impls, tu.name, dict(tu.input)),
                timeout=timeout_s,
            )
            tasks.append((tu, asyncio.ensure_future(coro)))

        for tu, task in tasks:
            start = time.monotonic()
            try:
                result = await task
            except (asyncio.TimeoutError, TimeoutError):
                result = {"error": f"tool timed out after {_TOOL_TIMEOUTS_S.get(tu.name, _DEFAULT_TOOL_TIMEOUT_S)}s"}
            duration_ms = int((time.monotonic() - start) * 1000)
            await ws.send_json({
                "type": "tool_result",
                "name": tu.name,
                "duration_ms": duration_ms,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, default=str),
            })

        history.append({"role": "user", "content": tool_results})

    await ws.send_json({"type": "text_done", "session_id": session_id})
    return history


def _execute_tool(impls: dict, name: str, kwargs: dict) -> dict:
    """Execute a tool implementation synchronously (runs in thread pool)."""
    impl = impls.get(name)
    try:
        return impl(kwargs) if impl else {"error": f"unknown tool: {name}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
