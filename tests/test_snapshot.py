import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pennywise.snapshot import Snapshot, snapshot_path, stamp_now


def _sample(ts: str | None = None) -> Snapshot:
    return Snapshot(
        fetched_at=ts or stamp_now(),
        holdings=[{"symbol": "INFY", "quantity": 10, "avg_price": 1400, "ltp": 1500,
                   "broad_sector": "Information Technology", "industry": "IT - Software"}],
        positions=[],
    )


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PENNYWISE_SNAPSHOT", str(tmp_path / "snap.json"))
    s = _sample()
    s.save()
    loaded = Snapshot.load()
    assert loaded.holdings == s.holdings
    assert loaded.fetched_at == s.fetched_at


def test_load_if_fresh_returns_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("PENNYWISE_SNAPSHOT", str(tmp_path / "snap.json"))
    _sample().save()
    assert Snapshot.load_if_fresh(max_age_s=3600) is not None


def test_load_if_fresh_rejects_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("PENNYWISE_SNAPSHOT", str(tmp_path / "snap.json"))
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _sample(stale_ts).save()
    assert Snapshot.load_if_fresh(max_age_s=3600) is None


def test_load_if_fresh_handles_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PENNYWISE_SNAPSHOT", str(tmp_path / "nope.json"))
    assert Snapshot.load_if_fresh() is None


def test_load_if_fresh_handles_corrupt_file(tmp_path, monkeypatch):
    p = tmp_path / "snap.json"
    p.write_text("not json {")
    monkeypatch.setenv("PENNYWISE_SNAPSHOT", str(p))
    assert Snapshot.load_if_fresh() is None


def test_snapshot_path_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PENNYWISE_SNAPSHOT", str(tmp_path / "x.json"))
    assert snapshot_path() == tmp_path / "x.json"
