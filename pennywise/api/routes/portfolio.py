"""Portfolio endpoints: holdings, risk metrics, statement upload."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from pennywise.api import db
from pennywise.api.auth import current_user
from pennywise.api.groww_creds import snapshot_provider
from pennywise.api.models import HoldingsResponse, RiskMetricsResponse, UploadResponse
from pennywise.api.statement import MAX_FILE_BYTES, StatementError, parse_statement
from pennywise.chat import tool_get_holdings, tool_get_risk_metrics

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/holdings", response_model=HoldingsResponse)
async def get_holdings(user: dict = Depends(current_user)) -> HoldingsResponse:
    """Return the user's current holdings with sector, value, and P&L."""
    result = await asyncio.to_thread(tool_get_holdings, snapshot_provider(user))
    return HoldingsResponse(**result)


@router.get("/risk", response_model=RiskMetricsResponse)
async def get_risk(user: dict = Depends(current_user)) -> RiskMetricsResponse:
    """Return concentration / risk metrics for the portfolio."""
    result = await asyncio.to_thread(tool_get_risk_metrics, snapshot_provider(user))
    return RiskMetricsResponse(**result)


@router.post("/upload", response_model=UploadResponse)
async def upload_statement(
    file: UploadFile = File(...),
    user: dict = Depends(current_user),
) -> UploadResponse:
    """Import a holdings statement (CSV/XLSX broker export).

    The low-friction alternative to connecting the Groww API: rows are
    parsed, tagged with sector/market-cap data, and stored as the user's
    portfolio snapshot. Re-upload any time to refresh.
    """
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 1 MB).")

    def _process() -> tuple[int, str, list[dict]]:
        from pennywise.snapshot import stamp_now
        from pennywise.tagging import tag_holdings

        holdings, ignored = parse_statement(file.filename or "", content)
        tag_holdings(holdings)
        fetched_at = stamp_now()
        db.save_snapshot(user["user_id"], {
            "fetched_at": fetched_at,
            "holdings": holdings,
            "positions": [],
            "source": "upload",
        })
        return len(holdings), fetched_at, ignored

    try:
        count, as_of, ignored = await asyncio.to_thread(_process)
    except StatementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return UploadResponse(count=count, as_of=as_of, ignored=ignored)
