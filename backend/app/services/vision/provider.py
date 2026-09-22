"""Vision provider selection + configuration (no hardcoded secrets).

Resolution order: explicit provider argument > LEGALAKSHI_VISION_PROVIDER
env. LEGALAKSHI_VISION_ENABLED=false (default) or missing credentials ->
None (OCR-only flow continues). Secrets come from env only and are never
logged, stored, or exposed to the frontend.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.vision.base import VisionError  # noqa: F401 (public)

log = logging.getLogger("legalakshi.vision")

# Stage 2C §1 configuration states (rendered, never probed over the
# network by the status endpoint).
VISION_DISABLED = "DISABLED"
VISION_NOT_CONFIGURED = "NOT_CONFIGURED"
VISION_INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
VISION_UNREACHABLE = "UNREACHABLE"
VISION_AVAILABLE = "AVAILABLE"


def _resolve_model(settings: Any) -> str:
    """Effective Gemini model (spec §1): GEMINI_MODEL, else
    LEGALAKSHI_VISION_MODEL, else the provider default. Empty means
    'provider default applies' — never a silent different model."""
    try:
        explicit = str(getattr(settings, "GEMINI_MODEL", "") or "").strip()
        if explicit:
            return explicit
        return str(getattr(settings, "LEGALAKSHI_VISION_MODEL", "")
                   or "").strip()
    except Exception:
        return ""


def _resolve_openrouter_key(settings: Any) -> str:
    """Server-side OpenRouter key (never exposed to callers)."""
    try:
        return str(getattr(settings, "OPENROUTER_API_KEY", "")
                   or "").strip()
    except Exception:
        return ""


def _resolve_groq_key(settings: Any) -> str:
    """Server-side Groq key (never exposed to callers)."""
    try:
        return str(getattr(settings, "GROQ_API_KEY", "") or "").strip()
    except Exception:
        return ""


def _resolve_groq_model(settings: Any) -> str:
    """Effective Groq model: GROQ_MODEL, else the Groq default."""
    try:
        from app.services.vision.groq_provider import DEFAULT_MODEL

        explicit = str(getattr(settings, "GROQ_MODEL", "") or "").strip()
        return explicit or DEFAULT_MODEL
    except Exception:
        return ""


def _resolve_groq_base(settings: Any) -> str:
    try:
        from app.services.vision.groq_provider import DEFAULT_BASE_URL

        explicit = str(getattr(settings, "GROQ_BASE_URL", "")
                       or "").strip()
        return explicit or DEFAULT_BASE_URL
    except Exception:
        return ""


def _resolve_openrouter_model(settings: Any) -> str:
    """Effective OpenRouter model: OPENROUTER_MODEL, else the free
    default. Empty means 'free default applies'."""
    try:
        from app.services.vision.openrouter_provider import DEFAULT_MODEL

        explicit = str(getattr(settings, "OPENROUTER_MODEL", "")
                       or "").strip()
        return explicit or DEFAULT_MODEL
    except Exception:
        return ""


def _resolve_openrouter_base(settings: Any) -> str:
    try:
        from app.services.vision.openrouter_provider import (
            DEFAULT_BASE_URL,
        )

        explicit = str(getattr(settings, "OPENROUTER_BASE_URL", "")
                       or "").strip()
        return explicit or DEFAULT_BASE_URL
    except Exception:
        return ""


def _resolve_api_key(settings: Any) -> str:
    """Server-side key resolution (never exposed to callers).

    Order: LEGALAKSHI_VISION_API_KEY, then the plain GEMINI_API_KEY
    alias. Both name the same secret; presence booleans (never the
    key itself) are what diagnostics report.
    """
    try:
        key = str(getattr(settings, "LEGALAKSHI_VISION_API_KEY", "")
                  or "").strip()
        if key:
            return key
        return str(getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def get_vision_config() -> dict[str, Any]:
    from app.core.config import get_settings

    try:
        settings = get_settings()
        provider = str(getattr(settings, "LEGALAKSHI_VISION_PROVIDER", "")
                       or "").strip().lower()
        enabled = bool(getattr(settings, "LEGALAKSHI_VISION_ENABLED", False))
        if provider == "openrouter":
            model = _resolve_openrouter_model(settings)
            has_key = bool(_resolve_openrouter_key(settings))
        elif provider == "groq":
            model = _resolve_groq_model(settings)
            has_key = bool(_resolve_groq_key(settings))
        else:
            model = _resolve_model(settings)
            has_key = bool(_resolve_api_key(settings))
    except Exception:
        return {"provider": "", "model": "", "enabled": False,
                "configured": False, "api_key_present": False}
    return {"provider": provider, "model": model, "enabled": enabled,
            "configured": bool(enabled and provider),
            "api_key_present": has_key}


def describe_vision_status() -> dict[str, Any]:
    """Configuration diagnosis for GET vision-status (Stage 2C §1).

    No network traffic: ``reachable`` means fully configured (key +
    model present where required), not a live probe — the officer-only
    health endpoint performs the single live check. Never includes key
    material (boolean presence only).
    """
    cfg = get_vision_config()
    provider = cfg.get("provider") or ""
    model = cfg.get("model") or ""
    enabled = bool(cfg.get("enabled"))
    key_present = bool(cfg.get("api_key_present"))
    if not enabled:
        return {**_public(cfg, key_present),
                "status": VISION_DISABLED,
                "reachable": False,
                "reason": "vision disabled "
                "(LEGALAKSHI_VISION_ENABLED is not true); "
                "OCR-only extraction is being used"}
    if not provider:
        return {**_public(cfg, key_present),
                "status": VISION_NOT_CONFIGURED,
                "reachable": False,
                "reason": "no vision provider selected "
                "(LEGALAKSHI_VISION_PROVIDER is empty)"}
    if provider not in ("mock", "gemini", "openrouter", "groq"):
        return {**_public(cfg, key_present),
                "status": VISION_INVALID_CONFIGURATION,
                "reachable": False,
                "reason": f"unknown vision provider {provider!r}; "
                "expected 'mock', 'gemini', 'openrouter' or 'groq'"}
    if provider == "gemini" and not key_present:
        return {**_public(cfg, key_present),
                "status": VISION_NOT_CONFIGURED,
                "reachable": False,
                "reason": "gemini selected but no API key configured "
                "(LEGALAKSHI_VISION_API_KEY is empty)"}
    if provider == "gemini" and not model:
        return {**_public(cfg, key_present),
                "status": VISION_NOT_CONFIGURED,
                "reachable": False,
                "reason": "gemini selected but no model configured "
                "(LEGALAKSHI_VISION_MODEL is empty)"}
    if provider == "openrouter" and not key_present:
        return {**_public(cfg, key_present),
                "status": VISION_NOT_CONFIGURED,
                "reachable": False,
                "reason": "openrouter selected but no API key configured "
                "(OPENROUTER_API_KEY is empty)"}
    if provider == "groq" and not key_present:
        return {**_public(cfg, key_present),
                "status": VISION_NOT_CONFIGURED,
                "reachable": False,
                "reason": "groq selected but no API key configured "
                "(GROQ_API_KEY is empty)"}
    if provider == "openrouter" and not str(model).endswith(":free"):
        return {**_public(cfg, key_present),
                "status": VISION_INVALID_CONFIGURATION,
                "reachable": False,
                "reason": "openrouter must use a free model "
                f"(got {model!r}); OCR-only flow"}
    return {**_public(cfg, key_present),
            "status": VISION_AVAILABLE,
            "reachable": True,
            "reason": f"provider {provider} configured"
            + (f" with model {model}" if model else "")}


def _public(cfg: dict[str, Any], key_present: bool) -> dict[str, Any]:
    return {"enabled": bool(cfg.get("enabled")),
            "provider": cfg.get("provider") or None,
            "model": cfg.get("model") or None,
            "configured": bool(cfg.get("configured")),
            "api_key_present": key_present}


def get_vision_provider(explicit: str | None = None) -> Any | None:
    """Return a configured provider or None (OCR-only fallback).

    Never raises: misconfiguration degrades to None with a safe log line
    (no secrets). Callers treat None as "vision unavailable".
    """
    from app.core.config import get_settings

    try:
        settings = get_settings()
        name = (explicit or getattr(
            settings, "LEGALAKSHI_VISION_PROVIDER", "") or "").strip().lower()
        enabled = bool(getattr(settings, "LEGALAKSHI_VISION_ENABLED", False))
    except Exception:
        return None
    if not enabled or not name:
        return None
    if name == "mock":
        from app.services.vision.mock_provider import MockVisionProvider

        return MockVisionProvider()
    if name == "gemini":
        from app.services.vision.gemini_provider import GeminiVisionProvider

        provider = GeminiVisionProvider(
            api_key=_resolve_api_key(settings),
            model=_resolve_model(settings))
        if not provider.available():
            log.warning("vision provider gemini requested but not "
                        "configured (missing key/model); OCR-only flow")
            return None
        return provider
    if name == "openrouter":
        from app.services.vision.openrouter_provider import (
            OpenRouterVisionProvider,
        )

        provider = OpenRouterVisionProvider(
            api_key=_resolve_openrouter_key(settings),
            model=_resolve_openrouter_model(settings),
            base_url=_resolve_openrouter_base(settings))
        # Free-tier safety (§10): the prototype must use ONLY the free
        # Qwen model. A non-free configuration degrades to OCR-only
        # instead of silently spending on a paid variant.
        if not provider.model.endswith(":free"):
            log.warning("vision provider openrouter configured with "
                        "non-free model %r; refusing (OCR-only flow)",
                        provider.model)
            return None
        if not provider.available():
            log.warning("vision provider openrouter requested but not "
                        "configured (missing key); OCR-only flow")
            return None
        return provider
    if name == "groq":
        from app.services.vision.groq_provider import GroqVisionProvider

        provider = GroqVisionProvider(
            api_key=_resolve_groq_key(settings),
            model=_resolve_groq_model(settings),
            base_url=_resolve_groq_base(settings))
        if not provider.available():
            log.warning("vision provider groq requested but not "
                        "configured (missing key); OCR-only flow")
            return None
        return provider
    log.warning("unknown vision provider %r; OCR-only flow", name)
    return None
