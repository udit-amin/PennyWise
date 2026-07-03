import pytest

from pennywise.connectors.screener import ScreenerScraper, clear_cache


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()

SAMPLE_HTML = """
<html><body>
<ul id="top-ratios">
  <li class="flex flex-space-between" data-source="default">
    <span class="name">Market Cap</span>
    <span class="nowrap value">₹ <span class="number">12,345</span> Cr.</span>
  </li>
  <li><span class="name">Stock P/E</span><span class="value"><span class="number">28.4</span></span></li>
  <li><span class="name">Price to book value</span><span class="value"><span class="number">3.10</span></span></li>
  <li><span class="name">Debt to equity</span><span class="value"><span class="number">0.45</span></span></li>
  <li><span class="name">ROE</span><span class="value"><span class="number">18.2</span> %</span></li>
</ul>
<a href="/market/IN03/" title="Broad Sector">Energy</a>
<a href="/market/IN03/IN0301/" title="Sector">Oil, Gas &amp; Consumable Fuels</a>
<a href="/market/IN03/IN0301/IN030103/" title="Broad Industry">Petroleum Products</a>
<a href="/market/IN03/IN0301/IN030103/IN030103001/" title="Industry">Refineries &amp; Marketing</a>
</body></html>
"""


def test_screener_parses_top_ratios():
    f = ScreenerScraper._parse("ACME", SAMPLE_HTML)
    assert f.ticker == "ACME"
    assert f.market_cap_cr == 12345.0
    assert f.pe == 28.4
    assert f.pb == 3.10
    assert f.debt_to_equity == 0.45
    assert f.roe == 18.2


def test_screener_extracts_full_sector_hierarchy():
    f = ScreenerScraper._parse("ACME", SAMPLE_HTML)
    assert f.broad_sector == "Energy"
    assert f.sector == "Oil, Gas & Consumable Fuels"
    assert f.industry == "Refineries & Marketing"


def test_screener_handles_missing_ratios():
    f = ScreenerScraper._parse("X", "<html><body></body></html>")
    assert f.pe is None and f.roe is None
    assert f.industry is None and f.broad_sector is None and f.sector is None
