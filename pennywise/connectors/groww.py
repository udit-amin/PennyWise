from __future__ import annotations

import hashlib
import os
import time
from typing import Any

import httpx

GROWW_BASE = "https://api.groww.in/v1"
TOKEN_PATH = "/token/api/access"


def _checksum(secret: str, timestamp: str) -> str:
    return hashlib.sha256(f"{secret}{timestamp}".encode()).hexdigest()


def exchange_for_access_token(api_key: str, api_secret: str, *, timeout: float = 10.0) -> str:
    """Trade an API key + secret for a daily access token.

    Groww requires:
      Authorization: Bearer <API_KEY>
      body: {"key_type": "approval", "checksum": sha256(secret+timestamp), "timestamp": <epoch_s>}
    """
    timestamp = str(int(time.time()))
    body = {
        "key_type": "approval",
        "checksum": _checksum(api_secret, timestamp),
        "timestamp": timestamp,
    }
    r = httpx.post(
        f"{GROWW_BASE}{TOKEN_PATH}",
        json=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-VERSION": "1.0",
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    payload = data.get("payload", data)
    token = payload.get("token") or payload.get("access_token")
    if not token:
        raise RuntimeError(f"No access token in Groww response: {data!r}")
    return token


class GrowwConnector:
    """Wrapper over the Groww Trade API.

    Two ways to authenticate:
      1. Pass `token=` directly (a pre-minted daily access token), OR
      2. Pass `api_key=` + `api_secret=` (or set GROWW_API_KEY + GROWW_API_SECRET);
         the constructor exchanges them for a daily access token.

    Falls back to env vars: GROWW_API_TOKEN, GROWW_API_KEY, GROWW_API_SECRET.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout: float = 10.0,
    ):
        token = token or os.environ.get("GROWW_API_TOKEN")
        if not token:
            api_key = api_key or os.environ.get("GROWW_API_KEY")
            api_secret = api_secret or os.environ.get("GROWW_API_SECRET")
            if api_key and api_secret:
                token = exchange_for_access_token(api_key, api_secret, timeout=timeout)
            else:
                raise RuntimeError(
                    "No Groww credentials. Set GROWW_API_TOKEN, or both "
                    "GROWW_API_KEY and GROWW_API_SECRET."
                )
        self.token = token
        self._client = httpx.Client(
            base_url=GROWW_BASE,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "X-API-VERSION": "1.0",
            },
            timeout=timeout,
        )

    def __enter__(self) -> "GrowwConnector":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, **params: Any) -> Any:
        r = self._client.get(path, params=params or None)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict) and "payload" in body:
            return body["payload"]
        return body

    def holdings(self) -> list[dict]:
        """Return holdings with normalised field names.

        Groww returns `trading_symbol` and `average_price`; downstream code
        in PennyWise uses `symbol` and `avg_price`. We expose both so the
        raw fields remain accessible.
        """
        data = self._get("/holdings/user")
        raw = data.get("holdings", []) if isinstance(data, dict) else data
        for h in raw:
            h["symbol"] = h.get("trading_symbol") or h.get("symbol")
            h["avg_price"] = h.get("average_price", h.get("avg_price"))
        return raw

    def positions(self) -> list[dict]:
        data = self._get("/positions/user")
        return data.get("positions", []) if isinstance(data, dict) else data

    def ltp(
        self,
        symbols: list[str],
        *,
        exchange: str = "NSE",
        segment: str = "CASH",
    ) -> dict[str, float]:
        """Fetch last-traded price.

        `symbols` may be bare trading symbols (e.g. "RELIANCE") which are then
        prefixed with `exchange`, or already-qualified `EXCHANGE_SYMBOL`
        strings (e.g. "NSE_RELIANCE") which are passed through.
        """
        qualified = [s if "_" in s else f"{exchange}_{s}" for s in symbols]
        data = self._get(
            "/live-data/ltp",
            segment=segment,
            exchange_symbols=",".join(qualified),
        )
        out: dict[str, float] = {}
        for k, v in data.items():
            bare = k.split("_", 1)[1] if "_" in k else k
            out[bare] = float(v)
        return out

    def holdings_with_ltp(self, *, batch: int = 50) -> list[dict]:
        """Holdings enriched with a live `ltp` field per row.

        Groww's LTP endpoint accepts up to 50 symbols per call; we batch
        accordingly. Symbols whose LTP lookup fails silently keep `ltp=None`.
        """
        rows = self.holdings()
        symbols = [h["symbol"] for h in rows if h.get("symbol")]
        prices: dict[str, float] = {}
        for i in range(0, len(symbols), batch):
            try:
                prices.update(self.ltp(symbols[i : i + batch]))
            except Exception:
                continue
        for h in rows:
            h["ltp"] = prices.get(h.get("symbol"))
        return rows

    def close(self) -> None:
        self._client.close()
