"""Tests for pennywise.utils.ttl_cache.TTLCache."""
from __future__ import annotations

import threading
import time

import pytest

from pennywise.utils.ttl_cache import TTLCache


def test_get_set_and_dict_style_access():
    cache = TTLCache(maxsize=4, ttl_s=60)
    cache.set("a", 1)
    cache["b"] = 2
    assert cache.get("a") == 1
    assert cache["b"] == 2
    assert "a" in cache
    assert "zz" not in cache
    assert len(cache) == 2


def test_missing_key_raises_keyerror():
    cache = TTLCache(maxsize=4, ttl_s=60)
    with pytest.raises(KeyError):
        cache["missing"]
    assert cache.get("missing") is None
    assert cache.get("missing", "fallback") == "fallback"


def test_entries_expire():
    cache = TTLCache(maxsize=4, ttl_s=0.01)
    cache["a"] = 1
    time.sleep(0.03)
    assert cache.get("a") is None
    assert "a" not in cache
    assert len(cache) == 0


def test_eviction_drops_oldest_inserted():
    cache = TTLCache(maxsize=2, ttl_s=60)
    cache["a"] = 1
    cache["b"] = 2
    cache["c"] = 3  # evicts "a"
    assert "a" not in cache
    assert cache["b"] == 2
    assert cache["c"] == 3


def test_overwrite_does_not_evict_others():
    cache = TTLCache(maxsize=2, ttl_s=60)
    cache["a"] = 1
    cache["b"] = 2
    cache["a"] = 10  # overwrite, still 2 entries
    assert cache["a"] == 10
    assert cache["b"] == 2


def test_clear():
    cache = TTLCache(maxsize=4, ttl_s=60)
    cache["a"] = 1
    cache.clear()
    assert "a" not in cache
    assert len(cache) == 0


def test_thread_safety_smoke():
    cache = TTLCache(maxsize=100, ttl_s=60)

    def worker(n: int) -> None:
        for i in range(500):
            cache[f"k{n}-{i % 50}"] = i
            cache.get(f"k{n}-{i % 50}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(cache) <= 100
