"""Map Screener.in industry strings to canonical sector buckets.

Screener returns industry labels like "Banks", "IT - Software",
"Cigarettes/Tobacco", "Pharmaceuticals", etc. The gap-detection and
risk-concentration logic need a smaller, stable vocabulary so weights add
up across related industries.
"""
from __future__ import annotations

CANONICAL = {
    "Financial Services",
    "Information Technology",
    "Consumer Goods",
    "Healthcare",
    "Energy",
    "Industrials",
    "Materials",
    "Communication Services",
    "Utilities",
    "Real Estate",
    "ETF / Index",
    "unknown",
}

# Substring → canonical bucket. First match wins; checked case-insensitively.
# GICS top-level labels are checked first because Screener now exposes them
# directly as "Broad Sector"; they must match before the granular substrings
# (otherwise e.g. "Information Technology" would fall through to nothing).
_RULES: list[tuple[str, str]] = [
    ("financial services", "Financial Services"),
    ("financials", "Financial Services"),
    ("information technology", "Information Technology"),
    ("health care", "Healthcare"),
    ("consumer staples", "Consumer Goods"),
    ("consumer discretionary", "Consumer Goods"),
    ("communication services", "Communication Services"),
    ("real estate", "Real Estate"),
    ("utilities", "Utilities"),
    ("materials", "Materials"),
    ("industrials", "Industrials"),
    ("energy", "Energy"),
    ("bank", "Financial Services"),
    ("finance", "Financial Services"),
    ("nbfc", "Financial Services"),
    ("insurance", "Financial Services"),
    ("holding", "Financial Services"),
    ("broker", "Financial Services"),
    ("amc", "Financial Services"),
    ("housing fin", "Financial Services"),
    ("it ", "Information Technology"),
    ("it -", "Information Technology"),
    ("computers - software", "Information Technology"),
    ("software", "Information Technology"),
    ("technology", "Information Technology"),
    ("internet", "Communication Services"),
    ("telecom", "Communication Services"),
    ("media", "Communication Services"),
    ("entertainment", "Communication Services"),
    ("fmcg", "Consumer Goods"),
    ("tobacco", "Consumer Goods"),
    ("cigarette", "Consumer Goods"),
    ("personal care", "Consumer Goods"),
    ("household", "Consumer Goods"),
    ("food", "Consumer Goods"),
    ("beverage", "Consumer Goods"),
    ("footwear", "Consumer Goods"),
    ("retail", "Consumer Goods"),
    ("textile", "Consumer Goods"),
    ("apparel", "Consumer Goods"),
    ("auto", "Consumer Goods"),
    ("two wheeler", "Consumer Goods"),
    ("consumer dur", "Consumer Goods"),
    ("hotel", "Consumer Goods"),
    ("leisure", "Consumer Goods"),
    ("pharma", "Healthcare"),
    ("drug", "Healthcare"),
    ("hospital", "Healthcare"),
    ("healthcare", "Healthcare"),
    ("diagnostic", "Healthcare"),
    ("biotech", "Healthcare"),
    ("refineries", "Energy"),
    ("refinery", "Energy"),
    ("oil", "Energy"),
    ("gas", "Energy"),
    ("petroleum", "Energy"),
    ("coal", "Energy"),
    ("power", "Energy"),
    ("renewable", "Energy"),
    ("cement", "Materials"),
    ("steel", "Materials"),
    ("metal", "Materials"),
    ("mining", "Materials"),
    ("chemical", "Materials"),
    ("fertilizer", "Materials"),
    ("paper", "Materials"),
    ("plastic", "Materials"),
    ("rubber", "Materials"),
    ("capital goods", "Industrials"),
    ("engineering", "Industrials"),
    ("construction", "Industrials"),
    ("infrastructure", "Industrials"),
    ("defence", "Industrials"),
    ("aviation", "Industrials"),
    ("logistic", "Industrials"),
    ("shipping", "Industrials"),
    ("railway", "Industrials"),
    ("port", "Industrials"),
    ("electrical", "Industrials"),
    ("electronics", "Industrials"),
    ("water", "Utilities"),
    ("realty", "Real Estate"),
    ("real estate", "Real Estate"),
    ("etf", "ETF / Index"),
    ("nifty", "ETF / Index"),
    ("sensex", "ETF / Index"),
    ("gold", "ETF / Index"),
    ("silver", "ETF / Index"),
]


def canonicalize_sector(industry: str | None) -> str:
    if not industry:
        return "unknown"
    needle = industry.lower()
    for token, bucket in _RULES:
        if token in needle:
            return bucket
    return "unknown"
