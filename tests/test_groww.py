import httpx
import pytest

from pennywise.connectors.groww import GrowwConnector


@pytest.fixture
def fake_groww(monkeypatch):
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/holdings/user":
            return httpx.Response(200, json={"status": "OK", "payload": {"holdings": [
                {"trading_symbol": "INFY", "quantity": 10, "average_price": 1400.0}
            ]}})
        if request.url.path == "/v1/positions/user":
            return httpx.Response(200, json={"status": "OK", "payload": {"positions": []}})
        if request.url.path == "/v1/live-data/ltp":
            assert request.url.params.get("segment") == "CASH"
            assert request.url.params.get("exchange_symbols") == "NSE_INFY"
            return httpx.Response(200, json={"status": "OK", "payload": {"NSE_INFY": "1500.50"}})
        return httpx.Response(404)

    monkeypatch.setenv("GROWW_API_TOKEN", "test-token")
    conn = GrowwConnector()
    conn._client = httpx.Client(
        base_url="https://api.groww.in/v1",
        headers={"Authorization": "Bearer test-token", "X-API-VERSION": "1.0"},
        transport=httpx.MockTransport(transport),
    )
    yield conn
    conn.close()


def test_holdings_normalises_field_names(fake_groww):
    h = fake_groww.holdings()
    assert len(h) == 1
    assert h[0]["symbol"] == "INFY"
    assert h[0]["avg_price"] == 1400.0
    assert h[0]["trading_symbol"] == "INFY"
    assert h[0]["average_price"] == 1400.0


def test_positions_empty(fake_groww):
    assert fake_groww.positions() == []


def test_ltp_coerces_floats(fake_groww):
    assert fake_groww.ltp(["INFY"]) == {"INFY": 1500.50}


def test_holdings_with_ltp_attaches_price(fake_groww):
    rows = fake_groww.holdings_with_ltp()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "INFY"
    assert rows[0]["ltp"] == 1500.50


def test_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("GROWW_API_TOKEN", raising=False)
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_API_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="No Groww credentials"):
        GrowwConnector()


def test_checksum_is_sha256_of_secret_plus_timestamp():
    from pennywise.connectors.groww import _checksum
    import hashlib
    assert _checksum("s3cr3t", "1719830400") == hashlib.sha256(b"s3cr3t1719830400").hexdigest()
