"""Role-based access control for the LegalAkshi API.

Roles: consumer < officer < admin (hierarchy; admin implies officer).

Identity sources, in order:
1. Clerk session JWT (only when CLERK_JWKS_URL is configured). Verified
   signature + expiry via PyJWT against Clerk's JWKS. Role resolution is
   server-side only, in order: (a) an explicit role claim in the token
   (``public_metadata.role`` → ``metadata.role`` → ``role`` — present only
   when a Clerk JWT template includes it); (b) the server-controlled user-ID
   maps OFFICER_USER_IDS / ADMIN_USER_IDS (Clerk ``sub`` values from backend
   env — works with default Clerk tokens); (c) ``consumer``. Dev headers
   are IGNORED in this mode.
2. Local development (Clerk not configured): explicit ``DEV_AUTH_ROLE`` env,
   else the ``X-LegalAkshi-Role`` / ``X-LegalAkshi-User`` request headers.
   This is a documented local-dev convenience, never a production path —
   set CLERK_JWKS_URL in any shared deployment.
3. Otherwise the caller is an anonymous consumer (public endpoints only).

``require_roles(...)`` is enforced server-side on every protected route.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request

log = logging.getLogger("legalakshi.auth")

CONSUMER, OFFICER, ADMIN = "consumer", "officer", "admin"
ROLES = (CONSUMER, OFFICER, ADMIN)
_RANK = {CONSUMER: 0, OFFICER: 1, ADMIN: 2}

DEV_ROLE_HEADER = "X-LegalAkshi-Role"
DEV_USER_HEADER = "X-LegalAkshi-User"


@dataclass
class Principal:
    user_id: str
    role: str = CONSUMER
    source: str = "anonymous"  # clerk | dev | anonymous
    email: str = ""
    name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def can(self, *roles: str) -> bool:
        if self.role not in _RANK:
            return False
        return any(self.role == r or _RANK[self.role] > _RANK.get(r, 99)
                   for r in roles)


def _claimed_role(claims: dict[str, Any]) -> Optional[str]:
    """Explicit role inside the verified token, if a JWT template provides it."""
    for container in (claims.get("public_metadata") or {},
                      claims.get("metadata") or {}):
        if isinstance(container, dict) and container.get("role") in ROLES:
            return container["role"]
    if claims.get("role") in ROLES:
        return claims["role"]
    return None


def _role_from_claims(claims: dict[str, Any]) -> str:
    return _claimed_role(claims) or CONSUMER


def _server_ids(raw: str) -> set[str]:
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


def _role_from_server_map(sub: str, cfg: Any) -> Optional[str]:
    """Server-controlled role assignment by verified Clerk user ID.

    Works with DEFAULT Clerk session tokens (which carry ``sub`` but no
    role metadata). IDs come from backend env only — never from the client.
    """
    if sub and sub in _server_ids(getattr(cfg, "ADMIN_USER_IDS", "")):
        return ADMIN
    if sub and sub in _server_ids(getattr(cfg, "OFFICER_USER_IDS", "")):
        return OFFICER
    return None


@lru_cache(maxsize=1)
def _jwks_client(url: str):
    from jwt import PyJWKClient

    return PyJWKClient(url)


def _principal_from_clerk(request: Request, jwks_url: str,
                          audience: str) -> Optional[Principal]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    try:
        import jwt

        key = _jwks_client(jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"],
                            audience=audience or None,
                            options={"verify_aud": bool(audience)})
    except Exception as exc:
        log.warning("clerk token verification failed: %s", exc)
        return None
    from app.core.config import get_settings

    cfg = get_settings()
    sub = str(claims.get("sub", ""))
    role = (_claimed_role(claims)
            or _role_from_server_map(sub, cfg)
            or CONSUMER)
    return Principal(
        user_id=sub,
        role=role,
        source="clerk",
        email=str(claims.get("email", "")),
        name=str(claims.get("name", "")),
        raw={k: claims[k] for k in ("iss", "exp", "sid") if k in claims},
    )


def get_principal(request: Request) -> Principal:
    """Resolve the caller. Never raises; protected routes enforce."""
    from app.core.config import get_settings

    cfg = get_settings()
    if cfg.CLERK_JWKS_URL:
        principal = _principal_from_clerk(request, cfg.CLERK_JWKS_URL,
                                          cfg.CLERK_AUDIENCE)
        if principal is not None:
            return principal
        return Principal(user_id="", role=CONSUMER, source="anonymous")
    # Local development path (no Clerk configured).
    if cfg.DEV_AUTH_ROLE in ROLES:
        return Principal(
            user_id=request.headers.get(DEV_USER_HEADER, "dev-user"),
            role=cfg.DEV_AUTH_ROLE, source="dev")
    header_role = (request.headers.get(DEV_ROLE_HEADER, "") or "").lower()
    if header_role in ROLES:
        return Principal(
            user_id=request.headers.get(DEV_USER_HEADER, "dev-user"),
            role=header_role, source="dev")
    return Principal(user_id="", role=CONSUMER, source="anonymous")


def require_roles(*roles: str):
    """FastAPI dependency: 401 when unauthenticated, 403 when forbidden."""
    def _check(request: Request) -> Principal:
        principal = get_principal(request)
        if principal.source == "anonymous":
            raise HTTPException(status_code=401,
                                detail="authentication required")
        if not principal.can(*roles):
            raise HTTPException(status_code=403,
                                detail=f"requires role: {'|'.join(roles)}")
        return principal

    return _check


# Re-export for routes: Depends(require_roles("officer"))
officer_only = require_roles(OFFICER)
admin_only = require_roles(ADMIN)
