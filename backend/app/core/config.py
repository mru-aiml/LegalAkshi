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
    # Stage 2 — Package Intelligence vision provider (multimodal AI for
    # extraction/reconciliation ONLY; never for compliance decisions).
    # LEGALAKSHI_VISION_PROVIDER: "" (disabled) | "mock" | "gemini".
    # LEGALAKSHI_VISION_ENABLED=false by default: OCR-only flow continues.
    LEGALAKSHI_VISION_PROVIDER: str = ""
    LEGALAKSHI_VISION_MODEL: str = ""
    # Plain alias for the model name. Resolution order: GEMINI_MODEL,
    # then LEGALAKSHI_VISION_MODEL, then the provider default. No other
    # model is ever silently substituted (spec §1).
    GEMINI_MODEL: str = ""
    # OpenRouter (OpenAI-compatible) vision provider. Key and model are
    # server-side only — never sent to, or readable by, the frontend.
    # Model resolution: OPENROUTER_MODEL, else the free default below.
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = ""
    OPENROUTER_MODEL: str = ""
    # Groq (OpenAI-compatible) vision provider — the active demo path.
    # Key/model/base are server-side only. Model resolution:
    # GROQ_MODEL, else the Groq default below.
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = ""
    GROQ_MODEL: str = ""
    LEGALAKSHI_VISION_API_KEY: str = ""
    # Accepted fallback for the Gemini key (same secret, plain name).
    # Resolution order: LEGALAKSHI_VISION_API_KEY, then GEMINI_API_KEY.
    # Server-side only — never sent to, or readable by, the frontend.
    GEMINI_API_KEY: str = ""
    LEGALAKSHI_VISION_ENABLED: bool = False
    # OCR inspection pipeline (extraction only, never compliance).
    # OCR_MAX_IMAGES caps TOTAL package images per inspection
    # (front + back + extras; the listing screenshot is separate).
    # OCR_TIMEOUT_SECONDS is the end-to-end extraction budget the
    # frontend honors for up to OCR_MAX_IMAGES photos (sequential
    # staged pipeline: one fast pass per image, then targeted crops).
    OCR_MAX_IMAGES: int = 8
    OCR_TIMEOUT_SECONDS: float = 420.0
    # Vision second pass: consolidated single-call extraction first
    # (one call, all wanted fields, one representative original photo),
    # grouped region-crop calls only for fields still uncovered.
    # False restores the legacy grouped-only plan.
    LEGALAKSHI_VISION_CONSOLIDATED: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
