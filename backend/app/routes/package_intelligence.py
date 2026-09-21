"""Package Intelligence endpoints (Stage 2, Parts A/G/H/O; Stage 2C §1/§2).

POST /api/v1/package-intelligence/reconcile — deterministic OCR(+vision)
    reconciliation over caller-supplied candidates (no pixels cross this
    boundary; vision calls happen server-side in the OCR flow).
GET /api/v1/package-intelligence/vision-status — configuration
    diagnosis (DISABLED / NOT_CONFIGURED / INVALID_CONFIGURATION /
    AVAILABLE) with key-presence boolean only. Never probes the network,
    never exposes secrets.
POST /api/v1/package-intelligence/vision-health — officer-only single
    minimal liveness probe (text-only, no package images, short
    timeout, sanitised result).
GET /api/v1/package-intelligence/readiness — required-field readiness
    for a context, from analysis_requirements (Rule Engine derived).

Authenticated callers only. Never a compliance verdict here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.auth import Principal, get_principal, require_roles
from app.services.package_intelligence.service import (
    readiness,
    run_package_intelligence,
)
from app.services.vision.provider import (
    VISION_AVAILABLE,
    describe_vision_status,
)

router = APIRouter(tags=["package-intelligence"])


@router.post("/package-intelligence/reconcile")
def reconcile(body: dict,
              principal: Principal = Depends(get_principal)):
    from fastapi import HTTPException

    if not principal.user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    ocr_result = body.get("ocr_result") or {}
    vision_summary = body.get("vision") or {}
    requirements = body.get("requirements")
    timings = body.get("timings") or {}
    # Normalise a compact caller shape: {ocr: {field: {...}},
    # vision_candidates: [...]} is also accepted.
    if not ocr_result and body.get("ocr"):
        ocr_result = {"fields": body.get("ocr")}
    if not vision_summary and body.get("vision_candidates"):
        vision_summary = {"candidates": body.get("vision_candidates")}
    pack = run_package_intelligence(ocr_result, vision_summary,
                                    requirements, timings)
    return pack


@router.get("/package-intelligence/vision-status")
def vision_status(principal: Principal = Depends(get_principal)):
    from fastapi import HTTPException

    if not principal.user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    detail = describe_vision_status()
    return {**detail,
            "note": "Credentials are never exposed; AI assists extraction "
            "only, never compliance decisions."}


@router.post("/package-intelligence/vision-health")
def vision_health(
        principal: Principal = Depends(require_roles("officer"))):
    """Officer-only single liveness probe (Stage 2C §2).

    Text-only request (never a package image), short timeout, response
    validated, failure sanitised. The inspection pipeline never depends
    on this — it is an explicit diagnostic action. Status is
    AVAILABLE / UNAVAILABLE (the UNREACHABLE configuration state is
    reported by GET vision-status).
    """
    detail = describe_vision_status()
    if detail.get("status") != VISION_AVAILABLE:
        return {"status": "UNAVAILABLE", "reason": detail.get(
            "reason", "vision not configured")}
    # Call-time lookup (not the module-level import) so the configured
    # provider is always resolved fresh.
    from app.services.vision import provider as provider_mod

    provider = provider_mod.get_vision_provider()
    if provider is None:
        return {"status": "UNAVAILABLE",
                "reason": "vision provider could not be constructed; "
                "OCR-only extraction is being used"}
    check = getattr(provider, "health_check", None)
    if not callable(check):
        return {"status": "UNAVAILABLE",
                "reason": "provider does not support health checks"}
    try:
        result = check(timeout_s=10.0)
    except Exception as exc:
        from app.services.package_intelligence.vision_stage import (
            _sanitize_error,
        )

        return {"status": "UNAVAILABLE",
                "reason": _sanitize_error(exc)}
    if isinstance(result, dict) and result.get("ok"):
        return {"status": "AVAILABLE",
                "provider": getattr(provider, "name", None),
                "model": getattr(provider, "model", None),
                "reason": str(result.get("reason", "ok"))}
    reason = "health probe failed"
    try:
        reason = str((result or {}).get("reason", reason))
    except Exception:
        pass
    return {"status": "UNAVAILABLE", "reason": reason}


@router.get("/package-intelligence/readiness")
def readiness_for(request: Request,
                  principal: Principal = Depends(get_principal)):
    from fastapi import HTTPException

    if not principal.user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    from app.services import analysis_requirements as req_mod

    params = dict(request.query_params)
    context: dict = {}
    for key in ("food", "imported", "ecommerce"):
        if key in params:
            context[key] = params[key].strip().lower() in (
                "1", "true", "yes", "y")
    for key in ("category", "quantity_type"):
        if key in params:
            context[key] = params[key]
    reqs = req_mod.get_analysis_field_requirements(
        request.app.state.repo, context)
    reconciled = {f: {"status": "NOT_DETECTED"} for f in
                  [r.get("ocr_key") or r.get("field")
                   for r in reqs.get("required", [])] if f}
    return {"requirements": reqs,
            "readiness": readiness(reconciled, reqs)}
