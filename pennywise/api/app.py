"""FastAPI application factory for PennyWise.

Run locally:
    uvicorn pennywise.api.app:create_app --factory --reload

Or via Docker:
    docker-compose up
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from pennywise.api import auth as auth_module
from pennywise.api import db
from pennywise.api import groww_creds
from pennywise.api.logging_config import configure_logging
from pennywise.api.ratelimit import limiter
from pennywise.api.routes import auth, chat, portfolio, recommendations, tools

logger = logging.getLogger("pennywise.api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    configure_logging()
    # Refuse to boot with insecure auth/crypto config in staging/prod.
    auth_module.validate_auth_config()
    groww_creds.validate_crypto_config()
    # Create DynamoDB tables only against dynamodb-local. In deployed
    # environments tables are provisioned out of band (Terraform /
    # `python -m pennywise.api.db --create`), never on web boot.
    if os.getenv("DYNAMODB_ENDPOINT"):
        db.create_tables_if_not_exist()
    # Fail jobs orphaned by the previous task/process. Best-effort — boot
    # must never hinge on it (idempotent + safe across workers).
    import asyncio

    from pennywise.api import background

    try:
        reconciled = await asyncio.to_thread(background.reconcile_stale_jobs)
        if reconciled:
            logger.info("reconciled %d orphaned background job(s)", reconciled)
    except Exception as exc:
        logger.warning("stale-job reconciliation failed: %s", exc)
    yield


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="PennyWise API",
        version="0.1.0",
        description="Agentic stock recommendation engine for Indian retail investors.",
        lifespan=_lifespan,
    )

    # ── Rate limiting ────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── No portfolio source → 409 with an actionable message ────────
    @app.exception_handler(groww_creds.GrowwNotLinked)
    async def _not_linked(request: Request, exc: groww_creds.GrowwNotLinked):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    "No portfolio available. Connect your Groww account "
                    "(POST /api/auth/groww-credentials) or upload a holdings "
                    "statement (POST /api/portfolio/upload) first."
                )
            },
        )

    # ── CORS ─────────────────────────────────────────────────────────
    allowed_origins = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    ).split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request id ───────────────────────────────────────────────────
    @app.middleware("http")
    async def _request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    # ── Routes ───────────────────────────────────────────────────────
    app.include_router(auth.router)
    app.include_router(portfolio.router)
    app.include_router(tools.router)
    app.include_router(chat.router)
    app.include_router(recommendations.router)

    # ── Login page ───────────────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page():
        return HTMLResponse(auth._LOGIN_HTML)

    # ── Health checks ────────────────────────────────────────────────
    @app.get("/health", tags=["infra"])
    async def health():
        """Liveness — the process is up."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["infra"])
    async def health_ready():
        """Readiness — the process can reach DynamoDB. Used by the ALB
        target group so we don't route to a task that can't serve."""
        import asyncio

        try:
            await asyncio.to_thread(db.ping)
        except Exception as exc:  # pragma: no cover - exercised via integration
            logger.warning("readiness check failed: %s", exc)
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "detail": "datastore unreachable"},
            )
        return {"status": "ready"}

    return app
