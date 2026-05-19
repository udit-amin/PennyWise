from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class Technicals:
    ticker: str
    last_close: float | None
    sma_50: float | None
    sma_200: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    vol_30d_ann: float | None  # annualised stdev of daily returns


def _yf_symbol(ticker: str) -> str:
    """Append .NS for NSE symbols unless caller already specified an exchange."""
    if "." in ticker:
        return ticker
    return f"{ticker}.NS"


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _last(series: pd.Series) -> float | None:
    if series.empty:
        return None
    val = series.dropna().iloc[-1] if series.notna().any() else None
    return float(val) if val is not None else None


class YFinanceClient:
    """Thin wrapper around yfinance for the technicals we need."""

    def fetch(self, ticker: str, period: str = "1y") -> Technicals:
        sym = _yf_symbol(ticker)
        df = yf.download(sym, period=period, progress=False, auto_adjust=True)
        if df.empty:
            return Technicals(ticker, None, None, None, None, None, None, None)

        close = df["Close"].squeeze()
        sma_50 = close.rolling(50).mean()
        sma_200 = close.rolling(200).mean()
        rsi = _rsi(close)
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd = ema_12 - ema_26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        daily_ret = close.pct_change().tail(30)
        vol = float(daily_ret.std() * np.sqrt(252)) if not daily_ret.empty else None

        return Technicals(
            ticker=ticker,
            last_close=_last(close),
            sma_50=_last(sma_50),
            sma_200=_last(sma_200),
            rsi_14=_last(rsi),
            macd=_last(macd),
            macd_signal=_last(macd_signal),
            vol_30d_ann=vol,
        )
