"""LLM client hardening: singleton reuse, retries, and timeouts wired in."""
from __future__ import annotations

import pennywise.agents._llm as _llm
import pennywise.api.streaming as streaming


def _fresh(monkeypatch):
    _llm._client.cache_clear()
    streaming._client.cache_clear()


def test_sync_client_is_singleton_with_retries(monkeypatch):
    _fresh(monkeypatch)
    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(_llm, "Anthropic", FakeAnthropic)
    a = _llm._client("key-1")
    b = _llm._client("key-1")
    assert a is b  # same client reused across calls/threads
    assert captured["max_retries"] == 3
    assert captured["timeout"] is not None
    _llm._client.cache_clear()


def test_sync_client_retries_env_tunable(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setenv("PENNYWISE_LLM_MAX_RETRIES", "7")
    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(_llm, "Anthropic", FakeAnthropic)
    _llm._client("key-2")
    assert captured["max_retries"] == 7
    _llm._client.cache_clear()


def test_async_client_is_singleton(monkeypatch):
    _fresh(monkeypatch)
    captured = {}

    class FakeAsync:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(streaming, "AsyncAnthropic", FakeAsync)
    a = streaming._client("key-3")
    assert streaming._client("key-3") is a
    assert captured["max_retries"] == 2
    streaming._client.cache_clear()


def test_tool_timeouts_configured():
    assert streaming._TOOL_TIMEOUTS_S["list_recommendations"] >= 120
    assert streaming._DEFAULT_TOOL_TIMEOUT_S <= 60
    assert streaming.CHAT_TURN_TIMEOUT_S >= streaming._TOOL_TIMEOUTS_S["list_recommendations"]
