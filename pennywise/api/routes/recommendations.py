"""Background recommendation workflow endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from pennywise.api import db
from pennywise.api.auth import current_user
from pennywise.api.background import submit_job
from pennywise.api.models import JobStatus, RecommendRequest
from pennywise.api.ratelimit import RECOMMENDATIONS_LIMIT, limiter
from pennywise.graph.workflow import run_pennywise

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


@router.post("", response_model=JobStatus)
@limiter.limit(RECOMMENDATIONS_LIMIT)
async def start_recommendations(
    request: Request,
    body: RecommendRequest,
    user: dict = Depends(current_user),
) -> JobStatus:
    """Kick off the full PennyWise workflow (12-node LangGraph pipeline).

    This takes 30-100 seconds, so it runs as a background job.
    Poll ``GET /api/recommendations/{job_id}`` for status.
    """
    user_id = user["user_id"]
    job_id = db.create_job(user_id, "recommendations", {"focus": body.focus})

    def _run() -> dict:
        return run_pennywise(focus=body.focus)

    submit_job(user_id, job_id, _run)

    return JobStatus(job_id=job_id, status="pending")


@router.get("/{job_id}", response_model=JobStatus)
async def get_recommendation_status(
    job_id: str,
    user: dict = Depends(current_user),
) -> JobStatus:
    """Poll the status of a recommendation job."""
    job = db.get_job(user["user_id"], job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        result=job.get("result"),
        error=job.get("error"),
    )
