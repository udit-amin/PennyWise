"""Portfolio endpoints: holdings, risk metrics, snapshot."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from pennywise.api.auth import current_user
from pennywise.api.models import HoldingsResponse, RiskMetricsResponse
from pennywise.chat import tool_get_holdings, tool_get_risk_metrics

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/holdings", response_model=HoldingsResponse)
async def get_holdings(user: dict = Depends(current_user)) -> HoldingsResponse:
    """Return the user's current holdings with sector, value, and P&L."""
    result = await asyncio.to_thread(tool_get_holdings)
    return HoldingsResponse(**result)


@router.get("/risk", response_model=RiskMetricsResponse)
async def get_risk(user: dict = Depends(current_user)) -> RiskMetricsResponse:
    """Return concentration / risk metrics for the portfolio."""
    result = await asyncio.to_thread(tool_get_risk_metrics)
    return RiskMetricsResponse(**result)
