"""Evidence-region proposals for region-first extraction (Stage 2C §5).

Pipeline per image: orientation -> OCR boxes -> semantic anchors ->
region proposal -> crop/ROI -> OCR + Vision on that ROI -> validator.

Region types (target = sent to Vision as a crop; exclusion = boundary
hints that must never enter a neighbouring region, e.g. ingredient
stop markers):

  PRODUCT_FRONT, QUANTITY, MRP, DATE, BATCH, MANUFACTURER, FSSAI,
  CONSUMER_CARE, INGREDIENTS, NUTRITION, VEG_SYMBOL (targets),
  STORAGE, PREPARATION, MARKETING (exclusions).

Rects are in OCR stage coordinates (same frame as diagnostics
ocr_boxes); callers map them to original pixels for cropping. The full
image is always available as context; the crop is the primary ROI.
"""
from __future__ import annotations

import re
from typing import Any

TARGET_REGIONS = ("PRODUCT_FRONT", "QUANTITY", "MRP", "DATE", "BATCH",
                  "MANUFACTURER", "FSSAI", "CONSUMER_CARE", "INGREDIENTS",
                  "NUTRITION", "VEG_SYMBOL", "BARCODE_QR")
EXCLUSION_REGIONS = ("STORAGE", "PREPARATION", "MARKETING")
REGION_TYPES = TARGET_REGIONS + EXCLUSION_REGIONS

# Stage-2B vision group -> region preference order (first hit wins).
GROUP_REGIONS: dict[str, list[str]] = {
    "A_product": ["PRODUCT_FRONT"],
    "B_declarations": ["MRP", "DATE", "BATCH"],
    "C_business": ["FSSAI", "MANUFACTURER", "CONSUMER_CARE"],
    "D_ingredients": ["INGREDIENTS"],
    "E_nutrition": ["NUTRITION"],
    "F_symbols": ["VEG_SYMBOL", "PRODUCT_FRONT"],
    "F_other": ["PRODUCT_FRONT"],
}

# Stage 3A.6 field -> region types (each region record carries image_id,
# region type, bbox, source lines, confidence, anchor, and — once a crop
# is taken — crop dimensions + preprocessing variant, which the vision
# stage attaches to its group reports).
FIELD_REGION_TYPES: dict[str, list[str]] = {
    "product_name": ["PRODUCT_FRONT"],
    "brand_name": ["PRODUCT_FRONT"],
    "common_generic_name": ["PRODUCT_FRONT"],
    "quantity": ["QUANTITY"],
    "unit": ["QUANTITY"],
    "mrp": ["MRP"],
    "manufacturing_date": ["DATE"],
    "best_before": ["DATE"],
    "use_by": ["DATE"],
    "batch_lot": ["BATCH"],
    "batch": ["BATCH"],
    "manufacturer": ["MANUFACTURER"],
    "manufacturer_address": ["MANUFACTURER"],
    "fssai_license": ["FSSAI"],
    "consumer_care": ["CONSUMER_CARE"],
    "ingredients": ["INGREDIENTS"],
    "allergens": ["INGREDIENTS"],
    "energy": ["NUTRITION"],
    "protein": ["NUTRITION"],
    "carbohydrate": ["NUTRITION"],
    "total_sugars": ["NUTRITION"],
    "added_sugars": ["NUTRITION"],
    "total_fat": ["NUTRITION"],
    "saturated_fat": ["NUTRITION"],
    "trans_fat": ["NUTRITION"],
    "sodium": ["NUTRITION"],
    "serving_size": ["NUTRITION"],
    "veg_nonveg": ["VEG_SYMBOL"],
    "country_of_origin": ["PRODUCT_FRONT"],
}


def regions_for_field(
    field: str,
    regions: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Region records relevant to one field, in preference order."""
    out: list[tuple[str, dict[str, Any]]] = []
    for rtype in FIELD_REGION_TYPES.get(field, []):
        if rtype in regions:
            out.append((rtype, regions[rtype]))
    return out


def _points_rect(box: Any) -> tuple[float, float, float, float] | None:
    """OCR box (4-point or dict) -> (x0, y0, x1, y1)."""
    try:
        if isinstance(box, dict):
            x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
            return (x, y, x + w, y + h)
        pts = [list(map(float, p)) for p in box]  # type: ignore[union-attr]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _pad(rect: tuple[float, float, float, float],
         x_mult: float = 3.0, y_mult: float = 1.2,
         extra: float = 4.0) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    h = max(y1 - y0, 1.0)
    return (max(0.0, x0 - h * x_mult), max(0.0, y0 - h * y_mult - extra),
            x1 + h * x_mult, y1 + h * y_mult + extra)


def _clip(rect: tuple[float, float, float, float],
          size: tuple[float, float]) -> tuple[float, float, float, float]:
    w, h = size
    x0, y0, x1, y1 = rect
    return (max(0.0, min(x0, w)), max(0.0, min(y0, h)),
            max(0.0, min(x1, w)), max(0.0, min(y1, h)))


def propose_regions(
    ocr_result: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    """Evidence regions for one image label (never raises).

    Returns {region_type: {rect, reason, source}}. Absent types mean
    "no anchor found — use the full image". Pure geometry over OCR
    evidence; costs zero OCR calls.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        diag_images = ((ocr_result.get("diagnostics") or {}).get("images")
                       or {})
        info = diag_images.get(label) or {}
        boxes = info.get("ocr_boxes") or []
        stage_size = tuple(info.get("stage_dimensions") or (0, 0))
        cand_boxes = info.get("candidate_boxes") or {}
    except Exception:
        return out

    def _first(kind: str) -> dict[str, Any] | None:
        rows = cand_boxes.get(kind) or []
        return rows[0] if rows else None

    def _add(rtype: str, entry: dict[str, Any] | None,
             pad_args: tuple = (), confidence: float = 0.85) -> None:
        if not entry or not entry.get("box"):
            return
        rect = _points_rect(entry.get("box"))
        if rect is None:
            return
        rect = _pad(rect, *(pad_args or (3.0, 1.2, 4.0)))
        if stage_size[0] and stage_size[1]:
            rect = _clip(rect, (float(stage_size[0]),
                                float(stage_size[1])))
        anchor = str(entry.get("text", ""))[:60]
        out[rtype] = {"region": rtype, "image_id": label,
                      "rect": [round(v, 1) for v in rect],
                      "bbox": [round(v, 1) for v in rect],
                      "anchor": anchor,
                      "source_lines": [str(entry.get("text", ""))],
                      "confidence": confidence,
                      "reason": f"{rtype} anchor: {anchor}",
                      "source": "ocr-anchors"}

    _add("MRP", _first("mrp"))
    _add("DATE", _first("date"))
    _add("FSSAI", _first("fssai"))
    _add("CONSUMER_CARE", _first("care"))
    _add("NUTRITION", _first("nutrition"), (1.5, 1.5, 6.0))
    heading = _first("ingredient_heading")
    if heading and heading.get("box"):
        rect = _points_rect(heading["box"])
        if rect is not None:
            _, _, _, y1 = rect
            h = max(rect[3] - rect[1], 1.0)
            w = float((stage_size or (0, 0))[0] or rect[2])
            band = (0.0, y1, w, y1 + 0.45 * float(
                (stage_size or (0, h))[1] or h * 10))
            if stage_size[0] and stage_size[1]:
                band = _clip(band, (float(stage_size[0]),
                                    float(stage_size[1])))
            out["INGREDIENTS"] = {
                "region": "INGREDIENTS", "image_id": label,
                "rect": [round(v, 1) for v in band],
                "bbox": [round(v, 1) for v in band],
                "anchor": str(heading.get("text", ""))[:60],
                "source_lines": [str(heading.get("text", ""))],
                "confidence": 0.8,
                "reason": "ingredient heading band: "
                f"{str(heading.get('text', ''))[:60]}",
                "source": "ocr-anchors"}
    # Lexical scans over stage lines for anchors the candidate pass
    # does not classify (batch codes, maker blocks, net quantity).
    try:
        from app.services.ocr import fields as fields_mod

        for ln in boxes:
            text = str(ln.get("text") or "")
            if not text.strip() or not ln.get("box"):
                continue
            rect = _points_rect(ln["box"])
            if rect is None:
                continue
            if "BATCH" not in out and fields_mod._BATCH_RE.search(text):
                out["BATCH"] = {
                    "region": "BATCH", "image_id": label,
                    "rect": [round(v, 1) for v in _pad(rect)],
                    "bbox": [round(v, 1) for v in _pad(rect)],
                    "anchor": text[:60], "source_lines": [text],
                    "confidence": 0.8,
                    "reason": f"batch anchor: {text[:60]}",
                    "source": "ocr-anchors"}
            if "MANUFACTURER" not in out and \
                    fields_mod._MAKER_CTX.search(text):
                x0, y0, x1, y1 = rect
                h = max(y1 - y0, 1.0)
                # Address block usually sits BELOW the opener line.
                grown = (max(0.0, x0 - h * 4), y0,
                         x1 + h * 4, y1 + h * 4)
                if stage_size[0] and stage_size[1]:
                    grown = _clip(grown, (float(stage_size[0]),
                                          float(stage_size[1])))
                out["MANUFACTURER"] = {
                    "region": "MANUFACTURER", "image_id": label,
                    "rect": [round(v, 1) for v in grown],
                    "bbox": [round(v, 1) for v in grown],
                    "anchor": text[:60], "source_lines": [text],
                    "confidence": 0.8,
                    "reason": f"maker anchor: {text[:60]}",
                    "source": "ocr-anchors"}
            if "QUANTITY" not in out and (
                    fields_mod._QTY_CTX.search(text)
                    or fields_mod._QTY_CTX_OCR.search(text)):
                out["QUANTITY"] = {
                    "region": "QUANTITY", "image_id": label,
                    "rect": [round(v, 1) for v in _pad(rect, 4.0, 1.5,
                                                        6.0)],
                    "bbox": [round(v, 1) for v in _pad(rect, 4.0, 1.5,
                                                        6.0)],
                    "anchor": text[:60], "source_lines": [text],
                    "confidence": 0.8,
                    "reason": f"net-quantity anchor: {text[:60]}",
                    "source": "ocr-anchors"}
            low = text.lower()
            for rtype, marker in (
                    ("STORAGE", ("storage", "store in", "keep in")),
                    ("PREPARATION", ("prepar", "directions for use",
                                     "how to")),
                    ("MARKETING", ("tasty", "delicious", "new pack",
                                   "offer"))):
                if rtype not in out and any(m in low for m in marker):
                    out[rtype] = {
                        "region": rtype, "image_id": label,
                        "rect": [round(v, 1) for v in _pad(rect)],
                        "bbox": [round(v, 1) for v in _pad(rect)],
                        "anchor": text[:60], "source_lines": [text],
                        "confidence": 0.6,
                        "reason": f"exclusion marker: {text[:60]}",
                        "source": "ocr-anchors"}
    except Exception:
        pass
    # BARCODE_QR regions double as FSSAI cross-check evidence (a 14-digit
    # run overlapping a barcode box is never a licence number).
    try:
        from app.services.ocr import fields as _fields_mod

        import types as _types

        shims = []
        for ln in boxes:
            shims.append(_types.SimpleNamespace(
                text=str(ln.get("text") or ""), confidence=0.0,
                image=label, box=ln.get("box")))
        for cand in _fields_mod.find_barcode_candidates(shims):
            brect = _points_rect(cand.get("box"))
            if brect is None:
                continue
            if stage_size[0] and stage_size[1]:
                brect = _clip(_pad(brect, 1.0, 1.0, 6.0),
                              (float(stage_size[0]),
                               float(stage_size[1])))
            out.setdefault("BARCODE_QR", {
                "region": "BARCODE_QR", "image_id": label,
                "rect": [round(v, 1) for v in brect],
                "bbox": [round(v, 1) for v in brect],
                "anchor": str(cand.get("digits", "")),
                "source_lines": [],
                "confidence": 0.7 if cand.get("ean13_checksum_valid")
                else 0.5,
                "reason": "barcode-shaped digit run "
                f"({cand.get('digits', '')[:16]})",
                "source": "ocr-anchors"})
            break  # first barcode region suffices for cross-checks
    except Exception:
        pass
    # VEG_SYMBOL from targeted contour candidates (service-computed,
    # relative coordinates). No OCR anchor exists for a logo.
    try:
        info_veg = (diag_images.get(label) or {}).get("veg_candidates") \
            or []
        if info_veg and stage_size[0] and stage_size[1]:
            best = max(info_veg,
                       key=lambda c: float(c.get("score") or 0))
            rel = best.get("rel_box") or []
            if len(rel) == 4:
                sw, sh = float(stage_size[0]), float(stage_size[1])
                vrect = (rel[0] * sw, rel[1] * sh,
                         rel[2] * sw, rel[3] * sh)
                vrect = _pad(vrect, 2.0, 2.0, 4.0)
                vrect = _clip(vrect, (sw, sh))
                out["VEG_SYMBOL"] = {
                    "region": "VEG_SYMBOL", "image_id": label,
                    "rect": [round(v, 1) for v in vrect],
                    "bbox": [round(v, 1) for v in vrect],
                    "anchor": str(best.get("nearby_text", ""))[:60],
                    "source_lines": [str(best.get("nearby_text", ""))]
                    if best.get("nearby_text") else [],
                    "confidence": float(best.get("score") or 0.0),
                    "reason": str(best.get("reason", ""))[:120],
                    "source": "symbol-candidates"}
    except Exception:
        pass
    return out


def region_for_group(
    group: str,
    regions: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    """Preferred evidence region for a vision group (Stage 2C §5)."""
    for rtype in GROUP_REGIONS.get(group, ["PRODUCT_FRONT"]):
        if rtype in regions:
            return rtype, regions[rtype]
    return None, None


def decode_raw(raw: bytes) -> Any:
    """Raw upload bytes -> RGB PIL image (raises on undecodable)."""
    import io as _io

    from PIL import Image, ImageOps

    img = Image.open(_io.BytesIO(raw)).convert("RGB")
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img


def crop_region_jpeg(raw: bytes, rect_stage: list | tuple,
                     stage_size: list | tuple) -> bytes | None:
    """Crop a stage-coordinate rect at full resolution, JPEG-encoded.

    Returns None when the crop is degenerate or undecodable (caller
    falls back to the full image).
    """
    import io as _io

    try:
        img = decode_raw(raw)
        orig_w, orig_h = img.size
        st_w = max(float(stage_size[0]), 1.0)
        st_h = max(float(stage_size[1]), 1.0)
        sx, sy = orig_w / st_w, orig_h / st_h
        x0, y0, x1, y1 = (float(v) for v in rect_stage)
        crop = img.crop((max(0, int(x0 * sx)), max(0, int(y0 * sy)),
                         min(orig_w, int(x1 * sx)),
                         min(orig_h, int(y1 * sy))))
        if crop.size[0] < 8 or crop.size[1] < 8:
            return None
        buf = _io.BytesIO()
        crop.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None


def guess_mime(raw: bytes) -> str:
    """Upload mime from magic bytes (logs only, never content)."""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:2] == b"\xff\xd8":
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"
