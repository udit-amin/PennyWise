from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx


@dataclass
class NewsItem:
    title: str
    link: str
    published: datetime | None
    summary: str | None


# Moneycontrol exposes section-level RSS feeds. We default to "business" which
# carries the broadest market and corporate-action coverage; callers can
# override per-ticker by passing a different feed URL.
MC_BUSINESS_RSS = "https://www.moneycontrol.com/rss/business.xml"
MC_MARKETS_RSS = "https://www.moneycontrol.com/rss/marketreports.xml"


class MoneycontrolNews:
    """Pulls headlines from Moneycontrol RSS feeds.

    No public per-ticker feed exists; we fetch the section feeds and filter
    headlines whose title or summary mentions the ticker / company name.
    """

    def __init__(self, *, timeout: float = 8.0):
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "PennyWise/0.1 (+local-research)"},
            follow_redirects=True,
        )

    def __enter__(self) -> "MoneycontrolNews":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(self, feed_url: str = MC_BUSINESS_RSS) -> list[NewsItem]:
        r = self._client.get(feed_url)
        r.raise_for_status()
        return self._parse(r.text)

    def filter_for(self, items: list[NewsItem], needles: list[str]) -> list[NewsItem]:
        lc_needles = [n.lower() for n in needles if n]
        out: list[NewsItem] = []
        for it in items:
            hay = f"{it.title} {it.summary or ''}".lower()
            if any(n in hay for n in lc_needles):
                out.append(it)
        return out

    @staticmethod
    def _parse(xml: str) -> list[NewsItem]:
        root = ET.fromstring(xml)
        items: list[NewsItem] = []
        for node in root.iter("item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            summary = (node.findtext("description") or "").strip() or None
            pub_raw = node.findtext("pubDate")
            published: datetime | None
            try:
                published = parsedate_to_datetime(pub_raw) if pub_raw else None
            except (TypeError, ValueError):
                published = None
            items.append(NewsItem(title=title, link=link, published=published, summary=summary))
        return items

    def close(self) -> None:
        self._client.close()
