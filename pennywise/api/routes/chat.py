"""WebSocket chat endpoint + session management."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from pennywise.api import db
from pennywise.api.auth import current_user, decode_jwt
from pennywise.api.models import ChatSessionSummary
from pennywise.api.streaming import stream_chat_turn

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── REST: session management ─────────────────────────────────────────


@router.get("/sessions", response_model=list[ChatSessionSummary])
async def list_sessions(
    user: dict = Depends(current_user),
) -> list[ChatSessionSummary]:
    """List the user's recent chat sessions."""
    items = db.list_sessions(user["user_id"])
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
    db.delete_session(user["user_id"], session_id)
    return {"status": "deleted"}


# ── WebSocket: streaming chat ────────────────────────────────────────


@router.websocket("/ws")
async def chat_ws(ws: WebSocket):
    """Streaming chat over WebSocket.

    Auth: pass JWT as ``?token=<jwt>`` query param (WebSocket doesn't
    support Authorization header in browsers).

    Protocol (see ``pennywise.api.streaming`` docstring for full details):
      Client -> {"type": "message", "text": "...", "session_id": "..." | null}
      Server -> {"type": "tool_call" | "tool_result" | "text_delta" | "text_done" | "error", ...}
    """
    # ── authenticate via query param ──
    token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4001, reason="Missing token query param")
        return

    try:
        payload = decode_jwt(token)
    except Exception:
        await ws.close(code=4001, reason="Invalid or expired token")
        return

    user = db.get_user(payload["sub"])
    if not user:
        await ws.close(code=4001, reason="User not found")
        return

    user_id = user["user_id"]
    await ws.accept()

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

            session_id = msg.get("session_id")

            # ── resolve / create session ──
            if session_id and session_id in sessions:
                history = sessions[session_id]
            elif session_id:
                # Try loading from DynamoDB
                saved = db.load_session(user_id, session_id)
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
                history = await stream_chat_turn(ws, history, text, session_id)
                sessions[session_id] = history

                # Persist to DynamoDB
                db.save_session(user_id, session_id, {
                    "history": history,
                    "started_at": session_id,
                    "last_user_message": text,
                })
            except Exception as exc:
                await ws.send_json({
                    "type": "error",
                    "detail": f"{type(exc).__name__}: {exc}",
                })

    except WebSocketDisconnect:
        pass  # client closed — nothing to clean up
