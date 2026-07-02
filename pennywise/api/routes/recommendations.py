"""Background recommendation workflow endpoint."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from pennywise.api import db
from pennywise.api.auth import current_user
from pennywise.api.background import submit_job
from pennywise.api.groww_creds import GrowwNotLinked, has_portfolio_source, snapshot_provider
from pennywise.api.models import JobStatus, RecommendRequest
from pennywise.api.ratelimit import allow_recommendation
from pennywise.graph.workflow import run_pennywise

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.post("", response_model=JobStatus)
async def start_recommendations(
    request: Request,
    body: RecommendRequest,
    user: dict = Depends(current_user),
) -> JobStatus:
    """Kick off the full PennyWise workflow (12-node LangGraph pipeline).

    This takes 30-100 seconds, so it runs as a background job.
    Poll ``GET /api/recommendations/{job_id}`` for status.
    """
    # Shared per-user cap (each run is a multi-LLM-call workflow).
    if not await asyncio.to_thread(allow_recommendation, user["user_id"]):
        raise HTTPException(
            status_code=429,
            detail="Recommendation limit reached — try again in an hour.",
        )

    # Fail fast with a 409 instead of a background job that's doomed.
    if not await asyncio.to_thread(has_portfolio_source, user):
        raise GrowwNotLinked()

    user_id = user["user_id"]
    job_id = await asyncio.to_thread(
        db.create_job, user_id, "recommendations", {"focus": body.focus}
    )
    get_snapshot = snapshot_provider(user)

    def _run() -> dict:
        snap = get_snapshot()  # resolved in the job thread — may hit Groww/Screener
        return run_pennywise(
            focus=body.focus,
            initial_holdings=snap.holdings,
            initial_positions=snap.positions,
        )

    submit_job(user_id, job_id, _run)

    return JobStatus(job_id=job_id, status="pending")


@router.get("/{job_id}", response_model=JobStatus)
async def get_recommendation_status(
    job_id: str,
    user: dict = Depends(current_user),
) -> JobStatus:
    """Poll the status of a recommendation job."""
    job = await asyncio.to_thread(db.get_job, user["user_id"], job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        result=job.get("result"),
        error=job.get("error"),
    )
