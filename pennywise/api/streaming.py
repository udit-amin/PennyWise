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
import time
from typing import Any

from anthropic import AsyncAnthropic
from fastapi import WebSocket

from pennywise.chat import SYSTEM, TOOL_IMPLS, TOOL_SPECS
from pennywise.config import load


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
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    history.append({"role": "user", "content": user_text})

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

        # Get the final message
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

        # Execute tools in parallel
        tool_results = []
        tasks = []
        for tu in tool_uses:
            await ws.send_json({
                "type": "tool_call",
                "name": tu.name,
                "input": dict(tu.input),
            })
            tasks.append((tu, asyncio.get_event_loop().run_in_executor(
                None, _execute_tool, impls, tu.name, dict(tu.input)
            )))

        for tu, task in tasks:
            start = time.monotonic()
            result = await task
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
