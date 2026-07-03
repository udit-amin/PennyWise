"""WebSocket chat: first-message auth, rate limiting, session persistence."""
from __future__ import annotations

import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from pennywise.api.auth import create_jwt


def _auth_frame(user) -> str:
    return json.dumps({"type": "auth", "token": create_jwt(user["user_id"], user["email"])})


def _wait_for_saved_session(fake_db, user_id, timeout_s=8.0):
    """The route saves the session via asyncio.to_thread *after* the mocked
    turn already sent text_done to the client, so the client-side `with`
    block can exit before the write lands server-side. Poll instead of
    asserting immediately after the socket closes.

    8s (not 2s) because the oversized-history test does real CPU work here
    (serialize + truncate ~1MB of history) inside the background thread —
    comfortably fast on a quiet machine, but a loaded/shared CI runner needs
    more headroom. The loop still returns as soon as the write lands, so
    this only affects worst-case latency, not the common-case runtime."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        saved = [s for (uid, _), s in fake_db.sessions.items() if uid == user_id]
        if saved:
            return saved
        time.sleep(0.01)
    pytest.fail("session was never persisted")


def test_ws_auth_happy_path(app_client, fake_db, test_user):
    with app_client.websocket_connect("/api/chat/ws") as ws:
        ws.send_text(_auth_frame(test_user))
        assert ws.receive_json() == {"type": "auth_ok"}


def test_ws_rejects_bad_token(app_client, fake_db):
    with pytest.raises(WebSocketDisconnect) as exc:
        with app_client.websocket_connect("/api/chat/ws") as ws:
            ws.send_text(json.dumps({"type": "auth", "token": "garbage"}))
            ws.receive_json()
    assert exc.value.code == 4001


def test_ws_rejects_non_auth_first_frame(app_client, fake_db, test_user):
    with pytest.raises(WebSocketDisconnect) as exc:
        with app_client.websocket_connect("/api/chat/ws") as ws:
            ws.send_text(json.dumps({"type": "message", "text": "hi"}))
            ws.receive_json()
    assert exc.value.code == 4001


def test_ws_rejects_unknown_user(app_client, fake_db):
    ghost = {"user_id": "no-such-user", "email": "ghost@example.com"}
    with pytest.raises(WebSocketDisconnect) as exc:
        with app_client.websocket_connect("/api/chat/ws") as ws:
            ws.send_text(_auth_frame(ghost))
            ws.receive_json()
    assert exc.value.code == 4001


def test_ws_token_not_accepted_in_query_param(app_client, fake_db, test_user):
    """The old ?token= auth is gone — a query param alone must not authenticate."""
    token = create_jwt(test_user["user_id"], test_user["email"])
    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect(f"/api/chat/ws?token={token}") as ws:
            ws.send_text(json.dumps({"type": "message", "text": "hi"}))
            ws.receive_json()


def test_ws_rate_limited_turn_returns_error_frame(app_client, fake_db, test_user, monkeypatch):
    monkeypatch.setattr(
        "pennywise.api.routes.chat.allow_chat_turn", lambda user_id: False
    )
    with app_client.websocket_connect("/api/chat/ws") as ws:
        ws.send_text(_auth_frame(test_user))
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_text(json.dumps({"type": "message", "text": "hello"}))
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "Rate limit" in frame["detail"]


def test_ws_turn_persists_session(app_client, fake_db, test_user, monkeypatch):
    async def _fake_turn(ws, history, user_text, session_id, *, tool_impls=None, **kw):
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": [{"type": "text", "text": "hi!"}]})
        await ws.send_json({"type": "text_done", "session_id": session_id})
        return history

    monkeypatch.setattr("pennywise.api.routes.chat.stream_chat_turn", _fake_turn)

    with app_client.websocket_connect("/api/chat/ws") as ws:
        ws.send_text(_auth_frame(test_user))
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_text(json.dumps({"type": "message", "text": "hello there"}))
        done = ws.receive_json()
        assert done["type"] == "text_done"

    saved = _wait_for_saved_session(fake_db, test_user["user_id"])
    assert len(saved) == 1
    assert saved[0]["last_user_message"] == "hello there"
    assert saved[0]["history"][0]["content"] == "hello there"


def test_ws_oversized_history_truncated_on_save(app_client, fake_db, test_user, monkeypatch):
    big_text = "x" * 50_000

    async def _fake_turn(ws, history, user_text, session_id, *, tool_impls=None, **kw):
        for i in range(20):  # ~1MB of history, well over the 300KB budget
            history.append({"role": "user", "content": f"{i} {big_text}"})
            history.append({"role": "assistant", "content": [{"type": "text", "text": big_text}]})
        await ws.send_json({"type": "text_done", "session_id": session_id})
        return history

    monkeypatch.setattr("pennywise.api.routes.chat.stream_chat_turn", _fake_turn)

    with app_client.websocket_connect("/api/chat/ws") as ws:
        ws.send_text(_auth_frame(test_user))
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_text(json.dumps({"type": "message", "text": "go"}))
        ws.receive_json()

    (saved,) = _wait_for_saved_session(fake_db, test_user["user_id"])
    stored_bytes = len(json.dumps(saved["history"]).encode())
    assert stored_bytes <= 300_000
    # Truncation cut at a user-text boundary: history still starts with a user turn.
    assert saved["history"][0]["role"] == "user"
    assert isinstance(saved["history"][0]["content"], str)
