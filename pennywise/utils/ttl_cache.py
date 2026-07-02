"""Thread-safe in-process cache with per-entry TTL and a size bound.

Used for the market-data caches (technicals / fundamentals / screener) that
previously were plain module-level dicts — shared across users by design
(the data is ticker-keyed, not user-specific) but unbounded and immortal in
a long-running server. Supports dict-style access so it drops in for a dict.
"""
from __future__ import annotations

import threading
import time
from typing import Any

_MISSING = object()


class TTLCache:
    """Mapping with expiry ``ttl_s`` seconds after insertion and eviction of
    the oldest-inserted entry once ``maxsize`` is reached (dicts preserve
    insertion order)."""

    def __init__(self, maxsize: int = 512, ttl_s: float = 900.0) -> None:
        self.maxsize = maxsize
        self.ttl_s = ttl_s
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return default
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._data[key]
                return default
            return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data.pop(key, None)
            while len(self._data) >= self.maxsize:
                self._data.pop(next(iter(self._data)))
            self._data[key] = (time.monotonic() + self.ttl_s, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __getitem__(self, key: Any) -> Any:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        self.set(key, value)

    def __contains__(self, key: Any) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def __len__(self) -> int:
        with self._lock:
            now = time.monotonic()
            return sum(1 for expires_at, _ in self._data.values() if expires_at > now)
