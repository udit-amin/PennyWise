"""Holdings-statement upload: parsing, validation, and the API endpoint."""
from __future__ import annotations

import io

import pytest

from pennywise.api.statement import StatementError, parse_statement


def _csv(text: str) -> bytes:
    return text.strip().encode()


GROWW_LIKE = _csv("""
Symbol,Quantity,Average buying price,Closing price
RELIANCE,10,2400.50,2900
TCS,5,"3,600",4100.25
M&M,12,1500,
BAJAJ-AUTO,2,9000,9500
""")


# ── parser ────────────────────────────────────────────────────────────


def test_parse_basic_csv():
    holdings, ignored = parse_statement("holdings.csv", GROWW_LIKE)
    assert [h["symbol"] for h in holdings] == ["RELIANCE", "TCS", "M&M", "BAJAJ-AUTO"]
    assert holdings[0] == {"symbol": "RELIANCE", "quantity": 10.0, "avg_price": 2400.5, "ltp": 2900.0}
    assert holdings[1]["avg_price"] == 3600.0  # thousands separator handled
    assert holdings[2]["ltp"] is None
    assert ignored == []


def test_parse_skips_preamble_rows():
    content = _csv("""
Holdings statement,,,
Generated on 2026-07-01,,,
,,,
Symbol,Qty,Avg cost,LTP
INFY,10,1400,1600
""")
    holdings, ignored = parse_statement("export.csv", content)
    assert holdings == [{"symbol": "INFY", "quantity": 10.0, "avg_price": 1400.0, "ltp": 1600.0}]


def test_company_names_reported_not_silently_dropped():
    content = _csv("""
Stock Name,Quantity,Average buying price
Reliance Industries,10,2400
TCS,5,3600
""")
    holdings, ignored = parse_statement("groww.csv", content)
    assert [h["symbol"] for h in holdings] == ["TCS"]
    assert len(ignored) == 1
    assert ignored[0]["row"] == 2
    assert "symbol" in ignored[0]["reason"].lower()


def test_non_positive_quantity_ignored():
    content = _csv("""
Symbol,Quantity,Avg price
RELIANCE,0,2400
TCS,5,3600
""")
    holdings, ignored = parse_statement("x.csv", content)
    assert [h["symbol"] for h in holdings] == ["TCS"]
    assert ignored[0]["reason"] == "Missing or non-positive quantity."


def test_no_recognizable_columns_rejected():
    with pytest.raises(StatementError, match="quantity column"):
        parse_statement("x.csv", _csv("a,b,c\n1,2,3"))


def test_all_rows_unusable_rejected():
    content = _csv("""
Stock Name,Quantity
Reliance Industries,10
""")
    with pytest.raises(StatementError, match="No importable holdings"):
        parse_statement("x.csv", content)


def test_too_many_holdings_rejected():
    rows = "\n".join(f"S{i},1,10" for i in range(201))
    with pytest.raises(StatementError, match="Too many holdings"):
        parse_statement("x.csv", _csv(f"Symbol,Qty,Avg price\n{rows}"))


def test_garbage_bytes_rejected():
    with pytest.raises(StatementError, match="Could not read"):
        parse_statement("x.xlsx", b"\x00\x01\x02 not a spreadsheet")


def test_xlsx_round_trip():
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Symbol", "Quantity", "Avg buy price", "LTP"])
    ws.append(["HDFCBANK", 8, 1500, 1700])
    buf = io.BytesIO()
    wb.save(buf)

    holdings, ignored = parse_statement("holdings.xlsx", buf.getvalue())
    assert holdings == [{"symbol": "HDFCBANK", "quantity": 8.0, "avg_price": 1500.0, "ltp": 1700.0}]


# ── endpoint ──────────────────────────────────────────────────────────


@pytest.fixture
def _no_tagging(monkeypatch):
    """Skip the Screener enrichment (network) — tag rows with a marker instead."""

    def _fake_tag(holdings, *, progress=None):
        for h in holdings:
            h["sector"] = "Tagged"
        return holdings

    monkeypatch.setattr("pennywise.tagging.tag_holdings", _fake_tag)


def test_upload_endpoint_happy_path(app_client, fake_db, test_user, auth_headers, _no_tagging):
    resp = app_client.post(
        "/api/portfolio/upload",
        files={"file": ("holdings.csv", GROWW_LIKE, "text/csv")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 4
    assert body["source"] == "upload"
    assert body["ignored"] == []
    assert body["as_of"]

    stored = fake_db.load_snapshot(test_user["user_id"])
    assert stored["source"] == "upload"
    assert stored["holdings"][0]["sector"] == "Tagged"

    # The uploaded portfolio now serves /holdings — no Groww link needed.
    resp = app_client.get("/api/portfolio/holdings", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["count"] == 4


def test_upload_bad_file_400(app_client, auth_headers, _no_tagging):
    resp = app_client.post(
        "/api/portfolio/upload",
        files={"file": ("x.csv", b"a,b,c\n1,2,3", "text/csv")},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "quantity column" in resp.json()["detail"]


def test_upload_oversized_413(app_client, auth_headers):
    big = b"Symbol,Qty,Avg price\n" + b"RELIANCE,1,10\n" * 100_000
    resp = app_client.post(
        "/api/portfolio/upload",
        files={"file": ("big.csv", big, "text/csv")},
        headers=auth_headers,
    )
    assert resp.status_code == 413


def test_upload_requires_auth(app_client):
    resp = app_client.post(
        "/api/portfolio/upload",
        files={"file": ("x.csv", GROWW_LIKE, "text/csv")},
    )
    assert resp.status_code in (401, 403)
