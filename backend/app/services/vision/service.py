"""Bounded grouped vision extraction service.

Strategy: determine unresolved / high-value fields (from analysis
requirements), map them onto VISION_FIELD_GROUPS, and issue ONE provider
call per group (never one call per field). Results are cached per
inspection so the same image is never sent repeatedly. Any provider
failure -> OCR-only fallback with the reason recorded; inspection is
never blocked.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.services.vision.schemas import (
    MAX_VISION_CALLS_PER_INSPECTION,
    groups_for_fields,
    validate_vision_candidate,
)

log = logging.getLogger("legalakshi.vision")

# In-process per-inspection cache: inspection_id -> {(image_id, group): cands}.
_cache: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {}


def clear_vision_cache(inspection_id: str | None = None) -> None:
    if inspection_id is None:
        _cache.clear()
    else:
        _cache.pop(str(inspection_id), None)


def extract_with_vision(
    provider: Any | None,
    images: list[tuple[Any, str]],
    requested_fields: list[str],
    ocr_candidates: list[dict[str, Any]] | None = None,
    layout_context: dict[str, Any] | None = None,
    inspection_id: str | None = None,
    group_override: str | None = None,
    per_call_timeout_s: float = 30.0,
    call_log: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run grouped vision extraction within the call budget.

    ``group_override`` treats the request as one pre-planned Stage-2B
    group (single provider call on the supplied image). Otherwise the
    requested fields are mapped onto VISION_FIELD_GROUPS. Every
    candidate is stamped with provider + model (§5). Each provider call
    runs under ``per_call_timeout_s`` (timeouts degrade to OCR-only,
    never block inspection). ``call_log`` (when supplied) receives one
    metadata entry per call — image_id, group, region, crop flag,
    byte size, mime, provider, model, latency, status — never image
    contents or key material (Stage 2C §4).

    Returns {candidates, calls, latency_ms, provider, model,
    fields_requested, fields_returned, error, cached}.
    Never raises: provider None/failure -> empty candidates + reason.
    """
    t0 = time.perf_counter()
    wanted = [f for f in dict.fromkeys(requested_fields or []) if f]
    summary: dict[str, Any] = {
        "candidates": [], "calls": 0, "latency_ms": 0.0,
        "provider": getattr(provider, "name", None),
        "model": getattr(provider, "model", None),
        "fields_requested": wanted, "fields_returned": [],
        "error": None, "cached": 0,
    }
    if provider is None:
        summary["error"] = "vision provider unavailable (disabled or " \
            "unconfigured); OCR-only flow"
        summary["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return summary
    if not wanted or not images:
        summary["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return summary
    groups = {group_override: wanted} if group_override \
        else groups_for_fields(wanted)
    # Prefer the first image (front panel) unless layout says otherwise;
    # one representative image per group keeps calls bounded.
    insp_cache = _cache.setdefault(str(inspection_id or "adhoc"), {})
    calls = 0
    candidates: list[dict[str, Any]] = []
    provider_name = getattr(provider, "name", None)
    provider_model = getattr(provider, "model", None)
    for group, fields in groups.items():
        if calls >= MAX_VISION_CALLS_PER_INSPECTION:
            summary["error"] = (summary["error"] or "") + \
                " vision call budget exhausted;"
            break
        image_np, image_id = images[0]
        # Stage 2B §10: identical image + group + prompt share one call;
        # a different prompt (fields/OCR context) is a different request.
        key = (str(image_id), group, _prompt_digest(
            fields, ocr_candidates, layout_context))
        if key in insp_cache:
            summary["cached"] += 1
            candidates.extend(insp_cache[key])
            if call_log is not None:
                call_log.append(_log_entry(
                    image_id, group, image_np, provider_name,
                    provider_model, 0.0, "cached", layout_context))
            continue
        t_call = time.perf_counter()
        try:
            raw = _call_with_timeout(
                provider, image_np, fields, ocr_candidates,
                layout_context, max(0.5, float(per_call_timeout_s)))
            call_status = "ok"
        except TimeoutError:
            summary["error"] = f"vision call timed out ({group}); " \
                "OCR-only for this group"
            if call_log is not None:
                call_log.append(_log_entry(
                    image_id, group, image_np, provider_name,
                    provider_model,
                    round((time.perf_counter() - t_call) * 1000, 1),
                    "timeout", layout_context))
            continue
        except Exception as exc:
            summary["error"] = f"vision call failed ({group}): " \
                f"{type(exc).__name__}"
            if call_log is not None:
                call_log.append(_log_entry(
                    image_id, group, image_np, provider_name,
                    provider_model,
                    round((time.perf_counter() - t_call) * 1000, 1),
                    "error", layout_context))
            continue
        call_ms = round((time.perf_counter() - t_call) * 1000, 1)
        calls += 1
        valid: list[dict[str, Any]] = []
        for item in raw or []:
            cand = validate_vision_candidate(item) if isinstance(
                item, dict) else None
            if cand is not None:
                cand["image_id"] = cand.get("image_id") or image_id
                cand["provider"] = provider_name
                cand["model"] = provider_model
                valid.append(cand)
        insp_cache[key] = valid
        candidates.extend(valid)
        if call_log is not None:
            call_log.append(_log_entry(
                image_id, group, image_np, provider_name,
                provider_model, call_ms, "ok", layout_context))
    summary["candidates"] = candidates
    summary["calls"] = calls
    summary["fields_returned"] = sorted({c["field"] for c in candidates
                                        if c.get("value")})
    summary["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return summary


def _log_entry(image_id: Any, group: str, payload: Any,
               provider_name: Any, provider_model: Any,
               latency_ms: float, status: str,
               layout_context: dict[str, Any] | None) -> dict[str, Any]:
    """Metadata-only call record (Stage 2C §4): sizes, never contents."""
    size: int | None = None
    mime: str | None = None
    try:
        if isinstance(payload, (bytes, bytearray)):
            size = len(payload)
            first = bytes(payload[:12])
            if first[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif first[:2] == b"\xff\xd8":
                mime = "image/jpeg"
            else:
                mime = "application/octet-stream"
    except Exception:
        pass
    ctx = layout_context or {}
    entry: dict[str, Any] = {
        "image_id": str(image_id), "group": group,
        "image_bytes_size": size, "mime_type": mime,
        "provider": provider_name, "model": provider_model,
        "latency_ms": latency_ms, "status": status,
        "region": ctx.get("region"), "crop": bool(ctx.get("crop_rect")),
        "crop_rect": ctx.get("crop_rect"),
    }
    try:
        log.debug("vision call image=%s group=%s region=%s crop=%s "
                  "bytes=%s mime=%s provider=%s model=%s latency=%sms "
                  "status=%s", entry["image_id"], group,
                  entry["region"], entry["crop"], size, mime,
                  provider_name, provider_model, latency_ms, status)
    except Exception:
        pass
    return entry


def _prompt_digest(fields: list[str],
                   ocr_candidates: list[dict[str, Any]] | None,
                   layout_context: dict[str, Any] | None) -> str:
    """Stable hash of the prompt-varying inputs (Stage 2B §10)."""
    import hashlib as _hl
    import json as _json

    try:
        blob = _json.dumps({"f": sorted(fields or []),
                            "o": ocr_candidates or [],
                            "l": layout_context or {}},
                           sort_keys=True, default=str)[:4000]
    except Exception:
        blob = str(sorted(fields or []))
    return _hl.sha256(blob.encode()).hexdigest()[:16]


def _call_with_timeout(provider: Any, image: Any,
                       fields: list[str],
                       ocr_candidates: list[dict[str, Any]] | None,
                       layout_context: dict[str, Any] | None,
                       timeout_s: float) -> Any:
    """One provider call under a wall-clock timeout (Stage 2B §12).

    Slow/hung models degrade to a TimeoutError (caller falls back to
    OCR-only for that group) instead of stalling the inspection.
    """
    import concurrent.futures as _fut

    with _fut.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(provider.extract_package_fields, image,
                             fields, ocr_candidates, layout_context)
        try:
            return future.result(timeout=timeout_s)
        except _fut.TimeoutError as exc:
            raise TimeoutError(
                f"vision call exceeded {timeout_s}s") from exc
