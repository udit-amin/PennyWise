"""On-disk snapshot of a tagged portfolio.

`pennywise snapshot` fetches holdings + LTP + fundamentals once and writes
the tagged result here. `pennywise risk` and `pennywise recommend` read it
back instead of re-hitting Groww / Screener, which keeps the rate limits
happy and makes analysis steps near-instant.

File: ``~/.pennywise/snapshot.json`` (override with ``$PENNYWISE_SNAPSHOT``).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def snapshot_path() -> Path:
    override = os.environ.get("PENNYWISE_SNAPSHOT")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pennywise" / "snapshot.json"


@dataclass
class Snapshot:
    fetched_at: str  # ISO-8601 UTC
    holdings: list[dict]  # each row: symbol, quantity, avg_price, ltp, broad_sector, sector, industry, market_cap_cr
    positions: list[dict] = field(default_factory=list)

    def age_seconds(self) -> float:
        try:
            t = datetime.fromisoformat(self.fetched_at.replace("Z", "+00:00"))
        except ValueError:
            return float("inf")
        return (datetime.now(timezone.utc) - t).total_seconds()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def save(self, path: Path | None = None) -> Path:
        target = path or snapshot_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json())
        return target

    @classmethod
    def from_dict(cls, data: dict) -> "Snapshot":
        return cls(
            fetched_at=data["fetched_at"],
            holdings=list(data.get("holdings", [])),
            positions=list(data.get("positions", [])),
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "Snapshot":
        target = path or snapshot_path()
        return cls.from_dict(json.loads(target.read_text()))

    @classmethod
    def load_if_fresh(cls, *, max_age_s: float = 7200, path: Path | None = None) -> "Snapshot | None":
        """Return the on-disk snapshot iff it exists and is younger than ``max_age_s``."""
        target = path or snapshot_path()
        if not target.exists():
            return None
        try:
            snap = cls.load(target)
        except (OSError, json.JSONDecodeError, KeyError):
            return None
        return snap if snap.age_seconds() <= max_age_s else None


def stamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
