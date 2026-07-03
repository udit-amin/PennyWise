"""Holdings-statement parsing for ``POST /api/portfolio/upload``.

Accepts the CSV/XLSX holdings exports brokers let users download (Groww
"Holdings statement", Zerodha Console, etc.) and normalises them into the
holding-row shape the rest of the codebase consumes. Column names vary by
broker, so headers are matched tolerantly; rows that can't be imported are
reported back with a reason instead of silently dropped.

Parsing is in-memory only — uploaded bytes never touch disk.
"""
from __future__ import annotations

import io
import re

import pandas as pd

MAX_FILE_BYTES = 1_000_000
MAX_HOLDINGS = 200

# Header candidates, normalised (lowercase, alphanumerics only).
_SYMBOL_COLS = {
    "symbol", "ticker", "stocksymbol", "tradingsymbol", "nsesymbol",
    "bsesymbol", "instrument", "scrip", "scripname",
}
_NAME_COLS = {"stockname", "name", "companyname", "company"}
_QTY_COLS = {"quantity", "qty", "quantityavailable", "totalquantity", "shares", "units"}
_AVG_COLS = {
    "avgbuyprice", "averagebuyingprice", "averagebuyprice", "avgcost",
    "avgprice", "averageprice", "buyaverageprice", "avgbuyingprice",
    "buyavg", "averagecost",
}
_LTP_COLS = {
    "ltp", "closingprice", "closeprice", "currentprice", "lasttradedprice",
    "cmp", "marketprice", "previousclosingprice", "currentmarketprice",
}

# NSE/BSE tickers: uppercase alphanumerics plus the odd & or - (M&M, BAJAJ-AUTO).
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9&\-]{0,19}$")


class StatementError(ValueError):
    """The file could not be parsed into holdings; message is user-facing."""


def _norm_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _read_frame(filename: str, content: bytes) -> pd.DataFrame:
    name = (filename or "").lower()
    buf = io.BytesIO(content)
    try:
        if name.endswith((".xlsx", ".xls")):
            return pd.read_excel(buf, header=None)
        return pd.read_csv(buf, header=None, skip_blank_lines=True)
    except Exception:
        raise StatementError(
            "Could not read the file. Upload the holdings statement as "
            "exported by your broker (.csv or .xlsx)."
        )


def _locate_header(frame: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """Find the header row (broker exports often have preamble rows) and map
    field → column index. Requires a quantity column plus a symbol or name."""
    for row_idx in range(min(len(frame), 15)):
        row = [_norm_header(v) for v in frame.iloc[row_idx].tolist()]
        cols: dict[str, int] = {}
        for col_idx, header in enumerate(row):
            if header in _SYMBOL_COLS and "symbol" not in cols:
                cols["symbol"] = col_idx
            elif header in _NAME_COLS and "name" not in cols:
                cols["name"] = col_idx
            elif header in _QTY_COLS and "quantity" not in cols:
                cols["quantity"] = col_idx
            elif header in _AVG_COLS and "avg_price" not in cols:
                cols["avg_price"] = col_idx
            elif header in _LTP_COLS and "ltp" not in cols:
                cols["ltp"] = col_idx
        if "quantity" in cols and ("symbol" in cols or "name" in cols):
            return row_idx, cols
    raise StatementError(
        "Could not find holdings columns. The file needs a quantity column "
        "and a symbol (or stock name) column — e.g. Groww's holdings "
        "statement or Zerodha Console export."
    )


def _to_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().replace(",", "").replace("₹", "")
    if not s or s.lower() in ("nan", "none", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_statement(filename: str, content: bytes) -> tuple[list[dict], list[dict]]:
    """Parse an uploaded statement into ``(holdings, ignored)``.

    ``holdings`` rows carry symbol / quantity / avg_price / ltp — the shape
    ``tagging.tag_holdings`` expects. ``ignored`` rows carry the original row
    number and a human-readable reason.
    """
    if len(content) > MAX_FILE_BYTES:
        raise StatementError("File too large (max 1 MB).")

    frame = _read_frame(filename, content)
    header_idx, cols = _locate_header(frame)

    holdings: list[dict] = []
    ignored: list[dict] = []
    for offset, (_, raw_row) in enumerate(frame.iloc[header_idx + 1 :].iterrows(), 1):
        row_number = header_idx + 1 + offset  # 1-based, as seen in the file
        values = raw_row.tolist()

        def _cell(field: str) -> object:
            idx = cols.get(field)
            return values[idx] if idx is not None and idx < len(values) else None

        raw_symbol = _cell("symbol") if "symbol" in cols else _cell("name")
        symbol = str(raw_symbol or "").strip().upper()
        if not symbol or symbol.lower() == "nan":
            continue  # blank/total rows — skip silently

        if not _TICKER_RE.match(symbol):
            ignored.append({
                "row": row_number,
                "value": symbol[:60],
                "reason": (
                    "Not a ticker symbol (looks like a company name). "
                    "Include a symbol column, e.g. RELIANCE not Reliance Industries."
                ),
            })
            continue

        quantity = _to_float(_cell("quantity"))
        if not quantity or quantity <= 0:
            ignored.append({"row": row_number, "value": symbol, "reason": "Missing or non-positive quantity."})
            continue

        holdings.append({
            "symbol": symbol,
            "quantity": quantity,
            "avg_price": _to_float(_cell("avg_price")) or 0.0,
            "ltp": _to_float(_cell("ltp")),
        })

    if not holdings:
        raise StatementError(
            "No importable holdings found. "
            + (ignored[0]["reason"] if ignored else "The file appears to be empty.")
        )
    if len(holdings) > MAX_HOLDINGS:
        raise StatementError(f"Too many holdings ({len(holdings)}; max {MAX_HOLDINGS}).")

    return holdings, ignored
