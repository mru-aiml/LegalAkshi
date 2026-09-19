"""RBAC tests — server-side enforcement on every protected endpoint.

Dev-identity mode (no CLERK_JWKS_URL): role from X-LegalAkshi-Role header.
Clerk mode: verified JWT claims (unit-tested with stubbed verification).
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from conftest import make_client, make_repo

OFFICER = {"X-LegalAkshi-Role": "officer", "X-LegalAkshi-User": "INSP-9"}
ADMIN = {"X-LegalAkshi-Role": "admin", "X-LegalAkshi-User": "ADMIN-1"}
CONSUMER = {"X-LegalAkshi-Role": "consumer", "X-LegalAkshi-User": "C-9"}


@pytest.fixture()
def client():
    return make_client(make_repo())


def test_anonymous_blocked_on_officer_and_admin(client: TestClient):
    assert client.get("/api/v1/officer/stats").status_code == 401
    assert client.get("/api/v1/officer/queue").status_code == 401
    assert client.get("/api/v1/admin/rules/sync/preview").status_code == 401


def test_consumer_forbidden_on_officer_and_admin(client: TestClient):
    assert client.get("/api/v1/officer/stats", headers=CONSUMER).status_code == 403
    assert client.get("/api/v1/admin/rules/sync/preview",
                      headers=CONSUMER).status_code == 403


def test_officer_allowed_officer_denied_admin(client: TestClient):
    assert client.get("/api/v1/officer/stats", headers=OFFICER).status_code == 200
    assert client.get("/api/v1/officer/queue", headers=OFFICER).status_code == 200
    assert client.get("/api/v1/admin/rules/sync/preview",
                      headers=OFFICER).status_code == 403


def test_admin_can_access_everything(client: TestClient):
    assert client.get("/api/v1/officer/stats", headers=ADMIN).status_code == 200
    assert client.get("/api/v1/admin/rules/sync/preview",
                      headers=ADMIN).status_code == 200


def test_unknown_role_header_treated_as_anonymous(client: TestClient):
    r = client.get("/api/v1/officer/stats",
                   headers={"X-LegalAkshi-Role": "superuser"})
    assert r.status_code == 401


def test_real_jwt_reaches_officer_endpoint(monkeypatch):
    """Regression: a real RS256 Clerk-style JWT in Authorization must
    authenticate the officer console end-to-end (stats/queue/profile)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    import jwt as pyjwt

    from app.core import auth as auth_mod
    from app.core.config import get_settings

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    token = pyjwt.encode({"sub": "off-1", "public_metadata": {"role": "officer"}},
                         priv_pem, algorithm="RS256")

    class _Key:
        key = pub_pem

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(auth_mod, "_jwks_client", lambda url: _Client())
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.test/.well-known/jwks.json")
    get_settings.cache_clear()
    try:
        authed = make_client(make_repo())
        bearer = {"Authorization": f"Bearer {token}"}
        assert authed.get("/api/v1/officer/stats", headers=bearer).status_code == 200
        assert authed.get("/api/v1/officer/queue", headers=bearer).status_code == 200
        assert authed.get("/api/v1/officer/profile", headers=bearer).status_code == 200
        # dev headers are ignored once Clerk verification is configured
        assert authed.get("/api/v1/officer/stats", headers=OFFICER).status_code == 401
    finally:
        get_settings.cache_clear()
        if "CLERK_JWKS_URL" in os.environ:
            del os.environ["CLERK_JWKS_URL"]


def test_public_endpoints_need_no_role(client: TestClient):
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/rules").status_code == 200


# ------------------------------------------------- clerk verification ---
def _rsa_keypair():
    rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key


def test_clerk_role_claim_extraction(monkeypatch):
    from app.core import auth as auth_mod

    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.test/.well-known/jwks.json")
    from app.core.config import get_settings
    get_settings.cache_clear()

    class _Key:
        key = "k"

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(auth_mod, "_jwks_client", lambda url: _Client())
    import jwt as pyjwt

    monkeypatch.setattr(pyjwt, "decode",
                        lambda *a, **k: {"sub": "u1",
                                         "public_metadata": {"role": "officer"}})

    class Req:
        headers = {"authorization": "Bearer abc"}

    principal = auth_mod._principal_from_clerk(Req(), "https://x", "")  # type: ignore[arg-type]
    assert principal is not None and principal.role == "officer"
    assert principal.source == "clerk"
    get_settings.cache_clear()
    if "CLERK_JWKS_URL" in os.environ:
        del os.environ["CLERK_JWKS_URL"]


def test_clerk_bad_token_means_anonymous(monkeypatch):
    from app.core import auth as auth_mod

    class _Client:
        def get_signing_key_from_jwt(self, token):
            raise Exception("no key")

    monkeypatch.setattr(auth_mod, "_jwks_client", lambda url: _Client())

    class Req:
        headers = {"authorization": "Bearer bogus"}

    assert auth_mod._principal_from_clerk(Req(), "https://x", "") is None  # type: ignore[arg-type]


def test_clerk_rs256_round_trip(monkeypatch):
    pytest.importorskip("cryptography")
    import jwt as pyjwt

    from app.core import auth as auth_mod

    key = _rsa_keypair()
    pub = key.public_key()
    from cryptography.hazmat.primitives import serialization

    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub_pem = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    token = pyjwt.encode({"sub": "u2", "role": "admin"}, priv_pem,
                         algorithm="RS256")

    class _Key:
        key = pub_pem

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(auth_mod, "_jwks_client", lambda url: _Client())

    class Req:
        headers = {"authorization": f"Bearer {token}"}

    principal = auth_mod._principal_from_clerk(Req(), "https://x", "")  # type: ignore[arg-type]
    assert principal is not None and principal.role == "admin"


def test_dev_headers_ignored_when_clerk_configured(monkeypatch):
    from app.core import auth as auth_mod
    from app.core.config import get_settings

    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.test/jwks.json")
    get_settings.cache_clear()

    class Req:
        headers = {"X-LegalAkshi-Role": "admin", "X-LegalAkshi-User": "mallory"}

    try:
        principal = auth_mod.get_principal(Req())  # type: ignore[arg-type]
        assert principal.source == "anonymous"  # no bearer token -> anonymous
        assert principal.role == "consumer"
    finally:
        get_settings.cache_clear()
        if "CLERK_JWKS_URL" in os.environ:
            del os.environ["CLERK_JWKS_URL"]


# --------------------------------- server role map + endpoint matrix ---
def _mint(sub: str, extra: dict | None = None) -> tuple[str, str]:
    """Real RS256 token + matching public PEM for stubbed JWKS."""
    pytest.importorskip("cryptography")
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    claims = {"sub": sub, **(extra or {})}
    return pyjwt.encode(claims, priv, algorithm="RS256"), pub


def _clerk_mode(monkeypatch, jwks_pub: str, **env_extra):
    """Stub JWKS verification + enable Clerk mode for one test."""
    from app.core import auth as auth_mod
    from app.core.config import get_settings

    class _Key:
        key = jwks_pub

    class _Client:
        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr(auth_mod, "_jwks_client", lambda url: _Client())
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.test/jwks.json")
    for k, v in env_extra.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_server_map_officer_endpoints(monkeypatch):
    """Cases 5-7: sub in OFFICER_USER_IDS -> stats/profile/queue allowed."""
    token, pub = _mint("user_officer_1")
    _clerk_mode(monkeypatch, pub, OFFICER_USER_IDS="user_officer_1")
    authed = make_client(make_repo())
    try:
        assert authed.get("/api/v1/officer/stats",
                          headers=_bearer(token)).status_code == 200
        assert authed.get("/api/v1/officer/profile",
                          headers=_bearer(token)).status_code == 200
        assert authed.get("/api/v1/officer/queue",
                          headers=_bearer(token)).status_code == 200
    finally:
        from app.core.config import get_settings
        get_settings.cache_clear()


def test_server_map_admin_policy(monkeypatch):
    """Case 8: sub in ADMIN_USER_IDS -> officer AND admin endpoints allowed."""
    token, pub = _mint("user_admin_9")
    _clerk_mode(monkeypatch, pub, ADMIN_USER_IDS="user_admin_9")
    authed = make_client(make_repo())
    try:
        assert authed.get("/api/v1/officer/stats",
                          headers=_bearer(token)).status_code == 200
        assert authed.get("/api/v1/admin/rules/sync/preview",
                          headers=_bearer(token)).status_code == 200
    finally:
        from app.core.config import get_settings
        get_settings.cache_clear()


def test_claim_beats_server_map(monkeypatch):
    """Explicit verified role claim takes precedence over the env map."""
    token, pub = _mint("user_officer_1", {"role": "admin"})
    _clerk_mode(monkeypatch, pub, OFFICER_USER_IDS="user_officer_1")
    authed = make_client(make_repo())
    try:
        assert authed.get("/api/v1/admin/rules/sync/preview",
                          headers=_bearer(token)).status_code == 200
    finally:
        from app.core.config import get_settings
        get_settings.cache_clear()


def test_missing_and_invalid_jwt_endpoints(monkeypatch):
    """Cases 1-2: no token -> 401; garbage token -> 401 (not 403/500)."""
    token, pub = _mint("user_x")
    _clerk_mode(monkeypatch, pub)
    authed = make_client(make_repo())
    try:
        assert authed.get("/api/v1/officer/stats").status_code == 401
        assert authed.post(
            "/api/v1/violations/00000000-0000-0000-0000-000000000000/verify",
            json={"decision": "CONFIRMED", "inspector_id": "x"},
            headers={"Authorization": "Bearer garbage.token.here"}).status_code == 401
    finally:
        from app.core.config import get_settings
        get_settings.cache_clear()


def test_consumer_jwt_consumer_allowed_officer_denied(monkeypatch):
    """Cases 3-4: plain verified consumer reaches consumer endpoints, not officer."""
    token, pub = _mint("user_consumer_3")
    _clerk_mode(monkeypatch, pub)
    authed = make_client(make_repo())
    try:
        assert authed.post("/api/v1/complaints",
                           json={"product_name": "P"},
                           headers=_bearer(token)).status_code == 201
        assert authed.get("/api/v1/inspections",
                          headers=_bearer(token)).status_code == 200
        assert authed.get("/api/v1/officer/stats",
                          headers=_bearer(token)).status_code == 403
    finally:
        from app.core.config import get_settings
        get_settings.cache_clear()


def test_dev_header_cannot_escalate_under_clerk(monkeypatch):
    """Case 9 (endpoint level): role headers are ignored once Clerk is on."""
    token, pub = _mint("user_consumer_3")
    _clerk_mode(monkeypatch, pub)
    authed = make_client(make_repo())
    try:
        headers = {"Authorization": f"Bearer {token}",
                   "X-LegalAkshi-Role": "admin", "X-LegalAkshi-User": "mallory"}
        assert authed.get("/api/v1/admin/rules/sync/preview",
                          headers=headers).status_code == 403
    finally:
        from app.core.config import get_settings
        get_settings.cache_clear()
