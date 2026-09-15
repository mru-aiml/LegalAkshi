"""FastAPI application factory — repository injected, never global.

Production (app.main) injects PostgresRepo (DATABASE_URL required).
Unit tests inject MemoryRepo (test-only, seeded from master v4).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.repositories.base import Repo
from app.routes import inspections, reports, rules, violations

log = logging.getLogger("legalakshi")


def create_app(repo: Repo) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LegalAkshi Compliance API", version="4.0.0",
                  description="REST/JSON contract between React frontend and FastAPI backend.")
    app.state.repo = repo
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["health"])
    def healthz():
        return {"status": "ok"}

    @app.get("/api/v1/health", tags=["health"])
    def health_v1(request: Request):
        return {"status": "ok", "backend": request.app.state.repo.kind,
                "engine_version": settings.ENGINE_VERSION}

    app.include_router(inspections.router, prefix="/api/v1")
    app.include_router(rules.router, prefix="/api/v1")
    app.include_router(violations.router, prefix="/api/v1")
    app.include_router(reports.router, prefix="/api/v1")
    return app
