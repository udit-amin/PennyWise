from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from pennywise.utils.ttl_cache import TTLCache


@dataclass
class Fundamentals:
    ticker: str
    pe: float | None
    pb: float | None
    debt_to_equity: float | None
    roe: float | None
    market_cap_cr: float | None
    industry: str | None        # most-specific GICS-style industry label (used for canonicalisation)
    broad_sector: str | None    # GICS top-level sector (e.g. "Energy", "Financials")
    sector: str | None          # mid-level sector (e.g. "Oil, Gas & Consumable Fuels")


# Process-level cache. Screener fundamentals barely change intraday and the
# main LangGraph workflow calls fundamentals_node twice (once before, once
# after candidate-picking) — without this we'd double every external request.
# Bounded + 1h TTL so a long-running server doesn't serve stale ratios forever.
_CACHE = TTLCache(maxsize=1024, ttl_s=3600)


class ScreenerScraper:
    """Fetches fundamentals from screener.in/company/<TICKER>/.

    Screener applies aggressive per-IP rate limits (HTTP 429 after roughly
    15-20 consecutive requests). We mitigate with:
      - polite inter-request throttling (`min_interval_s`)
      - one retry on 429 honouring the `Retry-After` header
      - a process-level cache so duplicate fetches are free
    """

    BASE = "https://www.screener.in/company"
    DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        min_interval_s: float = 0.8,
        max_retries: int = 2,
    ):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": self.DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
        )
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
        self._last_request_t = 0.0

    def __enter__(self) -> "ScreenerScraper":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def _to_float(text: str | None) -> float | None:
        if not text:
            return None
        m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
        return float(m.group()) if m else None

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_request_t
        if gap < self._min_interval_s:
            time.sleep(self._min_interval_s - gap)
        self._last_request_t = time.monotonic()

    def _get(self, url: str) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            self._throttle()
            r = self._client.get(url)
            if r.status_code == 429 and attempt < self._max_retries:
                wait = float(r.headers.get("Retry-After", "5"))
                time.sleep(min(wait, 30.0))
                continue
            return r
        return r  # last response (won't reach here unless retries hit and still 429)

    def fetch(self, ticker: str) -> Fundamentals:
        cached = _CACHE.get(ticker)
        if cached is not None:
            return cached
        r = self._get(f"{self.BASE}/{ticker}/")
        r.raise_for_status()
        f = self._parse(ticker, r.text)
        _CACHE[ticker] = f
        return f

    @classmethod
    def _parse(cls, ticker: str, html: str) -> Fundamentals:
        soup = BeautifulSoup(html, "lxml")
        ratios: dict[str, str] = {}
        for li in soup.select("#top-ratios li"):
            name = li.find("span", class_="name")
            value = li.find("span", class_="number")
            if name and value:
                ratios[name.get_text(strip=True)] = value.get_text(strip=True)

        def _by_title(t: str) -> str | None:
            node = soup.select_one(f'a[title="{t}"]')
            return node.get_text(strip=True) if node else None

        broad_sector = _by_title("Broad Sector")
        sector = _by_title("Sector")
        broad_industry = _by_title("Broad Industry")
        industry = _by_title("Industry") or broad_industry or sector or broad_sector

        return Fundamentals(
            ticker=ticker,
            pe=cls._to_float(ratios.get("Stock P/E")),
            pb=cls._to_float(ratios.get("Price to book value")),
            debt_to_equity=cls._to_float(ratios.get("Debt to equity")),
            roe=cls._to_float(ratios.get("ROE")),
            market_cap_cr=cls._to_float(ratios.get("Market Cap")),
            industry=industry,
            broad_sector=broad_sector,
            sector=sector,
        )

    def close(self) -> None:
        self._client.close()


def clear_cache() -> None:
    """Drop the process-level fundamentals cache. Mainly useful in tests."""
    _CACHE.clear()
