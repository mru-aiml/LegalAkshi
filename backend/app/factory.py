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
from app.routes import admin, complaints, consumer, inspections, notifications, ocr, officer, reports, rules, violations

log = logging.getLogger("legalakshi")


def create_app(repo: Repo) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LegalAkshi Compliance API", version="4.0.0",
                  description="REST/JSON contract between React frontend and FastAPI backend.")
    app.state.repo = repo
    # CORSMiddleware is the outermost layer: preflight OPTIONS is answered
    # here (200 + Access-Control-Allow-*) and never reaches authentication,
    # RBAC, or routes. Explicit origin list only — never "*" together with
    # allow_credentials=True.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Accept",
            "Origin",
            "X-LegalAkshi-Role",
            "X-LegalAkshi-User",
        ],
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
    app.include_router(complaints.router, prefix="/api/v1")
    app.include_router(ocr.router, prefix="/api/v1")
    app.include_router(notifications.router, prefix="/api/v1")
    app.include_router(officer.router, prefix="/api/v1")
    app.include_router(consumer.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")

    @app.middleware("http")
    async def _timing(request: Request, call_next):
        """Lightweight timing: duration header + debug log for key reads.

        Development-safe by design: only method, path, status and
        milliseconds are recorded — never credentials, tokens, personal
        data, or OCR text. Clients (and the frontend timeout logic) can
        read ``X-Request-Duration-Ms``; operators get per-endpoint
        timings at DEBUG level (uvicorn --log-level debug).
        """
        import time as _time

        start = _time.perf_counter()
        response = await call_next(request)
        ms = round((_time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-Duration-Ms"] = str(ms)
        path = request.url.path
        if request.method == "GET" and (
                path.startswith("/api/v1/consumer/")
                or path in ("/api/v1/officer/stats", "/api/v1/officer/queue",
                            "/api/v1/inspections", "/api/v1/complaints",
                            "/api/v1/rules")):
            log.debug("timing %s %s -> %s in %sms", request.method, path,
                      response.status_code, ms)
        return response

    return app
