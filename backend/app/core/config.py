"""Application configuration (pydantic-settings). Never commit real credentials."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # No default: production must provide DATABASE_URL (backend/.env or env).
    # Tests bypass this via the in-memory repository (see app.factory).
    DATABASE_URL: str = ""
    # Local Vite dev origins. No wildcard: allow_credentials=True forbids
    # allow_origins=["*"]. Override per deployment via CORS_ORIGINS.
    # NOTE: 172.20.128.1 is this dev machine's LAN IP serving Vite; LAN IPs
    # vary per machine/network — extend CORS_ORIGINS if yours differs.
    CORS_ORIGINS: str = ("http://localhost:5173,http://localhost:5174,"
                         "http://127.0.0.1:5173,http://127.0.0.1:5174,"
                         "http://172.20.128.1:5174")
    ENGINE_VERSION: str = "1.0.0"
    SCORING_POLICY_CODE: str = "DEFAULT-2026"
    LOW_CONFIDENCE_THRESHOLD: float = 0.6
    # Auth: set CLERK_JWKS_URL (e.g. https://<clerk-domain>/.well-known/jwks.json)
    # to enforce Clerk JWT verification. Without it the API uses the documented
    # local-dev identity (DEV_AUTH_ROLE or X-LegalAkshi-Role headers).
    CLERK_JWKS_URL: str = ""
    CLERK_AUDIENCE: str = ""
    DEV_AUTH_ROLE: str = ""
    # Server-controlled role assignment by verified Clerk user ID (sub).
    # Comma-separated Clerk user IDs, e.g. OFFICER_USER_IDS=user_abc,user_def.
    # Works with DEFAULT Clerk session tokens (no JWT template changes needed).
    # Backend env only — clients can never grant themselves roles.
    OFFICER_USER_IDS: str = ""
    ADMIN_USER_IDS: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
