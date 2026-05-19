from datetime import datetime

from pennywise.connectors.moneycontrol import MoneycontrolNews, NewsItem

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
  <item>
    <title>Infosys posts Q4 beat</title>
    <link>https://example.com/infy</link>
    <description>Infosys reported revenue ahead of estimates.</description>
    <pubDate>Mon, 13 May 2026 09:00:00 +0530</pubDate>
  </item>
  <item>
    <title>Reliance to raise capex</title>
    <link>https://example.com/ril</link>
    <description>RIL plans expansion in renewables.</description>
    <pubDate>Mon, 13 May 2026 08:00:00 +0530</pubDate>
  </item>
</channel></rss>
"""


def test_parses_rss_items():
    items = MoneycontrolNews._parse(SAMPLE_RSS)
    assert len(items) == 2
    assert items[0].title.startswith("Infosys")
    assert isinstance(items[0].published, datetime)


def test_filter_for_needle():
    items = [
        NewsItem("Infosys posts Q4 beat", "u1", None, "summary"),
        NewsItem("Reliance to raise capex", "u2", None, None),
    ]
    hits = MoneycontrolNews().filter_for(items, ["reliance"])
    assert len(hits) == 1 and hits[0].link == "u2"
