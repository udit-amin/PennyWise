"""Live market-data tool endpoints."""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Path

from pennywise.api.auth import current_user
from pennywise.api.models import FundamentalsResponse, NewsResponse, TechnicalsResponse
from pennywise.chat import tool_fetch_fundamentals, tool_fetch_news, tool_fetch_technicals

router = APIRouter(prefix="/api/tools", tags=["tools"])

_SYMBOL_RE = re.compile(r"^[A-Z0-9&-]{1,20}$")


def _validate_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not _SYMBOL_RE.match(sym):
        raise HTTPException(status_code=400, detail=f"Invalid symbol: {symbol!r}")
    return sym


@router.get("/technicals/{symbol}", response_model=TechnicalsResponse)
async def get_technicals(
    symbol: str = Path(...),
    user: dict = Depends(current_user),
) -> TechnicalsResponse:
    """Live technical indicators from yfinance for any NSE ticker."""
    sym = _validate_symbol(symbol)
    result = await asyncio.to_thread(tool_fetch_technicals, sym)
    return TechnicalsResponse(**result)


@router.get("/fundamentals/{symbol}", response_model=FundamentalsResponse)
async def get_fundamentals(
    symbol: str = Path(...),
    user: dict = Depends(current_user),
) -> FundamentalsResponse:
    """Live fundamentals from Screener.in for any NSE ticker."""
    sym = _validate_symbol(symbol)
    result = await asyncio.to_thread(tool_fetch_fundamentals, sym)
    return FundamentalsResponse(**result)


@router.get("/news/{symbol}", response_model=NewsResponse)
async def get_news(
    symbol: str = Path(...),
    user: dict = Depends(current_user),
) -> NewsResponse:
    """Recent Moneycontrol headlines mentioning the ticker."""
    sym = _validate_symbol(symbol)
    result = await asyncio.to_thread(tool_fetch_news, sym)
    return NewsResponse(**result)
