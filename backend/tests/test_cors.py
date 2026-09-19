"""CORS preflight tests — the local Vite frontend must get a valid 200
preflight (never the 400 "Disallowed CORS origin" seen in production).

CORSMiddleware answers OPTIONS before authentication/RBAC: preflight needs
no credentials, while real requests still pass through to the normal
auth layer (401 for anonymous callers on protected routes).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    # Dev machine LAN IP serving Vite (see Network panel Origin).
    "http://172.20.128.1:5174",
]


@pytest.fixture()
def client():
    return make_client(make_repo())


@pytest.mark.parametrize("origin", FRONTEND_ORIGINS)
@pytest.mark.parametrize("path", [
    "/api/v1/health",
    "/api/v1/complaints",
    "/api/v1/inspections",
    "/api/v1/officer/queue",
    "/api/v1/rules",
])
def test_preflight_allows_frontend_origins(client: TestClient, origin: str, path: str):
    r = client.options(
        path,
        headers={"Origin": origin,
                 "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert r.status_code == 200, (path, origin, r.text)
    assert r.headers.get("access-control-allow-origin") == origin
    allow_methods = r.headers.get("access-control-allow-methods", "")
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        assert method in allow_methods, allow_methods


def test_preflight_rejects_unknown_origin(client: TestClient):
    r = client.options(
        "/api/v1/rules",
        headers={"Origin": "https://evil.example.com",
                 "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_get_rules_passes_cors_to_route(client: TestClient):
    """Public route: CORS headers present and the route itself answers."""
    r = client.get("/api/v1/rules", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_protected_route_still_requires_auth_after_cors(client: TestClient):
    """CORS success must not bypass auth: anonymous verify hits the 401 layer."""
    r = client.post(
        "/api/v1/violations/00000000-0000-0000-0000-000000000000/verify",
        json={"decision": "CONFIRMED", "inspector_id": "x"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
