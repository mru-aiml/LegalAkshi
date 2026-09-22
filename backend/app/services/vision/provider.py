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
        model = _resolve_model(settings)
        enabled = bool(getattr(settings, "LEGALAKSHI_VISION_ENABLED", False))
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
    if provider not in ("mock", "gemini"):
        return {**_public(cfg, key_present),
                "status": VISION_INVALID_CONFIGURATION,
                "reachable": False,
                "reason": f"unknown vision provider {provider!r}; "
                "expected 'mock' or 'gemini'"}
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
    log.warning("unknown vision provider %r; OCR-only flow", name)
    return None
