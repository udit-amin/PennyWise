"""FastAPI application factory for PennyWise.

Run locally:
    uvicorn pennywise.api.app:create_app --factory --reload

Or via Docker:
    docker-compose up
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from pennywise.api import db
from pennywise.api.routes import auth, chat, portfolio, recommendations, tools


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    # Create DynamoDB tables when running against dynamodb-local
    if os.getenv("DYNAMODB_ENDPOINT"):
        db.create_tables_if_not_exist()
    yield


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="PennyWise API",
        version="0.1.0",
        description="Agentic stock recommendation engine for Indian retail investors.",
        lifespan=_lifespan,
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

    # ── Health check ─────────────────────────────────────────────────
    @app.get("/health", tags=["infra"])
    async def health():
        return {"status": "ok"}

    return app
