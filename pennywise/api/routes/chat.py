"""WebSocket chat endpoint + session management."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from pennywise.api import db
from pennywise.api.auth import current_user, decode_jwt
from pennywise.api.groww_creds import snapshot_provider
from pennywise.api.models import ChatSessionSummary
from pennywise.api.ratelimit import allow_chat_turn
from pennywise.api.streaming import stream_chat_turn
from pennywise.chat import make_tool_impls

logger = logging.getLogger("pennywise.api.chat")

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── REST: session management ─────────────────────────────────────────


@router.get("/sessions", response_model=list[ChatSessionSummary])
async def list_sessions(
    user: dict = Depends(current_user),
) -> list[ChatSessionSummary]:
    """List the user's recent chat sessions."""
    items = await asyncio.to_thread(db.list_sessions, user["user_id"])
    return [
        ChatSessionSummary(
            id=it["session_id"],
            turns=0,  # we don't store turn count separately
            started_at=it.get("started_at"),
            last_user_message=it.get("last_user_message"),
        )
        for it in items
    ]


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: dict = Depends(current_user),
) -> dict:
    """Delete a chat session."""
    await asyncio.to_thread(db.delete_session, user["user_id"], session_id)
    return {"status": "deleted"}


# ── WebSocket: streaming chat ────────────────────────────────────────


AUTH_DEADLINE_S = 10


@router.websocket("/ws")
async def chat_ws(ws: WebSocket):
    """Streaming chat over WebSocket.

    Auth: connect, then send ``{"type": "auth", "token": "<jwt>"}`` as the
    FIRST frame (within 10s). The server replies ``{"type": "auth_ok"}`` or
    closes with code 4001. Tokens never ride the URL — query strings land in
    ALB access logs.

    Protocol after auth (see ``pennywise.api.streaming`` for full details):
      Client -> {"type": "message", "text": "...", "session_id": "..." | null}
      Server -> {"type": "tool_call" | "tool_result" | "text_delta" | "text_done" | "error", ...}
    """
    await ws.accept()

    # ── first-message auth ──
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=AUTH_DEADLINE_S))
    except WebSocketDisconnect:
        return
    except (asyncio.TimeoutError, TimeoutError):
        await ws.close(code=4001, reason="Auth timeout")
        return
    except json.JSONDecodeError:
        await ws.close(code=4001, reason="Expected an auth message")
        return

    token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
    if not token:
        await ws.close(code=4001, reason='Expected {"type": "auth", "token": "<jwt>"}')
        return

    try:
        payload = decode_jwt(token)
    except Exception:
        await ws.close(code=4001, reason="Invalid or expired token")
        return

    user = await asyncio.to_thread(db.get_user, payload["sub"])
    if not user:
        await ws.close(code=4001, reason="User not found")
        return

    user_id = user["user_id"]
    await ws.send_json({"type": "auth_ok"})

    # Per-user tool table: portfolio tools read THIS user's snapshot (and
    # degrade to a groww_not_linked result when they have no portfolio).
    tool_impls = make_tool_impls(snapshot_provider(user))

    # Per-connection session state: {session_id: history_list}
    sessions: dict[str, list[dict]] = {}

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "detail": "Invalid JSON"})
                continue

            if msg.get("type") != "message":
                await ws.send_json({"type": "error", "detail": f"Unknown message type: {msg.get('type')}"})
                continue

            text = (msg.get("text") or "").strip()
            if not text:
                await ws.send_json({"type": "error", "detail": "Empty message"})
                continue

            if not await asyncio.to_thread(allow_chat_turn, user_id):
                await ws.send_json({
                    "type": "error",
                    "detail": "Rate limit exceeded — too many chat turns. Try again later.",
                })
                continue

            session_id = msg.get("session_id")

            # ── resolve / create session ──
            if session_id and session_id in sessions:
                history = sessions[session_id]
            elif session_id:
                # Try loading from DynamoDB
                saved = await asyncio.to_thread(db.load_session, user_id, session_id)
                if saved:
                    history = saved.get("history", [])
                else:
                    history = []
                sessions[session_id] = history
            else:
                # New session
                session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                history = []
                sessions[session_id] = history

            # ── run the chat turn ──
            try:
                history = await stream_chat_turn(
                    ws, history, text, session_id, tool_impls=tool_impls
                )
                sessions[session_id] = history

                # Persist to DynamoDB
                await asyncio.to_thread(db.save_session, user_id, session_id, {
                    "history": history,
                    "started_at": session_id,
                    "last_user_message": text,
                })
            except Exception as exc:
                logger.exception(
                    "chat turn failed: %s", exc,
                    extra={"user_id": user_id, "session_id": session_id},
                )
                await ws.send_json({
                    "type": "error",
                    "detail": "Internal error while processing this turn.",
                })

    except WebSocketDisconnect:
        pass  # client closed — nothing to clean up
