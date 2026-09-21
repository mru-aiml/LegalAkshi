"""OCR extraction endpoint — pixels to structured fields, never to verdicts.

POST /api/v1/ocr/extract (multipart: front_image, back_image, images[],
listing_image) runs the staged RapidOCR pipeline over ALL supplied
package photos (one fast pass per image, then targeted region crops only
for unresolved fields, rotation only as a last resort) and returns raw
text plus deterministically extracted fields with OCR
provenance/confidence/status, cross-image reconciliation, per-pass
timings, the structured food-label layer, and the image-analysis
veg/non-veg symbol verdict.
The caller reviews/edits and submits the FINAL declaration to the existing
analysis endpoint; the rule engine never sees raw OCR output directly.
Requires an authenticated caller (consumer, officer, or admin).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.core.auth import Principal, get_principal
from app.services.ocr import service as ocr_service
from app.services.ocr.base import OcrError
from app.services.ocr.tesseract_provider import TesseractProvider

router = APIRouter(tags=["ocr"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Production hybrid wiring: RapidOCR primary everywhere; this provider is
# consulted solely by the ingredient fallback path. Cheap to construct
# (no binary probing); safe to share across requests (stateless).
_TESS_PROVIDER = TesseractProvider()


async def _read_limited(upload: UploadFile | None, side: str) -> bytes | None:
    if upload is None:
        return None
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422,
                            detail=f"{side} image exceeds 10 MB")
    if not data:
        raise HTTPException(status_code=422,
                            detail=f"{side} image is empty")
    return data


@router.post("/ocr/extract")
async def ocr_extract(
    request: Request,
    front_image: UploadFile | None = File(None),
    back_image: UploadFile | None = File(None),
    images: list[UploadFile] | None = File(None),
    listing_image: UploadFile | None = File(None),
    # Stage 2B: optional inspection context for analysis requirements so
    # the vision stage requests only relevant unresolved groups. All
    # optional — absent context degrades to high-value unresolved fields.
    food: str | None = None,
    imported: str | None = None,
    ecommerce: str | None = None,
    category: str | None = None,
    quantity_type: str | None = None,
    inspection_id: str | None = None,
    principal: Principal = Depends(get_principal),
):
    if not principal.user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    front_bytes = await _read_limited(front_image, "front")
    back_bytes = await _read_limited(back_image, "back")
    extra: list[tuple[bytes | None, str]] = []
    for i, upload in enumerate(images or []):
        extra.append((await _read_limited(upload, f"image_{i}"),
                      f"image_{i}"))
    listing_bytes = await _read_limited(listing_image, "listing")
    supplied: list[tuple[bytes | None, str]] = [
        (front_bytes, "front"), (back_bytes, "back"), *extra]
    if listing_bytes is not None:
        supplied.append((listing_bytes, "listing"))
    if all(raw is None for raw, _ in supplied):
        raise HTTPException(status_code=422,
                            detail="at least one of front_image/back_image is required")
    try:
        # Hybrid ingredient fallback: RapidOCR primary, one targeted
        # Tesseract crop only when RapidOCR's ingredient read is weak.
        # _TESS_PROVIDER construction never touches the binary;
        # unavailability degrades to RapidOCR-only inside the service.
        # NOTE: the legacy call below keeps its exact (front, back)
        # signature — endpoint tests patch extract_label with a fixed
        # fake. Hybrid for that path is enabled via the explicit
        # module-level opt-in default, set here per request.
        ocr_service.configure_tesseract_fallback(_TESS_PROVIDER)
        if listing_bytes is None and not (images or []):
            # Legacy two-sided path (same behaviour, same patch point).
            result = ocr_service.extract_label(front_bytes, back_bytes)
            raws: list[tuple[bytes | None, str]] = [
                (front_bytes, "front"), (back_bytes, "back")]
        else:
            result = ocr_service.extract_label_multi(
                supplied, tess_provider=_TESS_PROVIDER)
            raws = list(supplied)
        return _maybe_enhance_with_vision(
            request, result, raws, food=food, imported=imported,
            ecommerce=ecommerce, category=category,
            quantity_type=quantity_type, inspection_id=inspection_id)
    except OcrError as exc:
        raise HTTPException(status_code=502, detail=f"OCR provider failed: {exc}")


def _opt_context_bool(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.strip().lower() in ("1", "true", "yes", "y")


def _maybe_enhance_with_vision(
    request: Request, result: dict,
    raws: list[tuple[bytes | None, str]], **context: str | None,
) -> dict:
    """Stage 2B vision stage on the REAL pipeline result.

    Runs AFTER the untouched OCR pipeline: resolves analysis
    requirements for the inspection context, invokes the vision
    provider on grouped unresolved fields (<=6 calls), reconciles,
    and attaches ``vision`` + ``reconciliation`` keys. Any failure —
    disabled provider, timeout, malformed output, requirements error —
    returns the OCR result with an unavailable block. Never raises for
    vision reasons (OCR errors propagate as before).
    """
    from app.services.package_intelligence import vision_stage as vs_mod

    inspection_id = context.get("inspection_id")
    try:
        from app.services import analysis_requirements as req_mod

        ctx = {k: v for k, v in {
            "food": _opt_context_bool(context.get("food")),
            "imported": _opt_context_bool(context.get("imported")),
            "ecommerce": _opt_context_bool(context.get("ecommerce")),
            "category": context.get("category"),
            "quantity_type": context.get("quantity_type"),
        }.items() if v is not None}
        requirements = req_mod.get_analysis_field_requirements(
            request.app.state.repo, ctx)
    except Exception:
        requirements = None
    try:
        return vs_mod.enhance_ocr_with_vision(
            result, raws, requirements,
            inspection_id=inspection_id)
    except Exception as exc:  # absolute last resort: OCR-only
        result["vision"] = {
            "vision_enabled": False, "vision_provider": None,
            "vision_model": None, "vision_status": "unavailable",
            "vision_error": vs_mod._sanitize_error(exc),
            "vision_calls": 0}
        return result
