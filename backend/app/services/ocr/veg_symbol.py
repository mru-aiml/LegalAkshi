"""Vegetarian / non-vegetarian symbol detection (image analysis, not OCR).

The FSSAI mark is a filled circle inside a square outline — green for
vegetarian, brown/red for non-vegetarian — usually 4-12 mm on pack.
Detection is purely visual, on the ORIGINAL colour image (never the
OCR grayscale/threshold variants, which destroy colour):

1. square-outline candidates from OpenCV contours (geometry first);
2. per-candidate HSV verification: mark-coloured border ring + filled
   centre disc of the SAME hue, with circularity of the centre blob;
3. whole-frame fallback only when hue-gated colour AND square geometry
   agree on the same region.

Colour alone never decides: many packs (e.g. amber cooking oil,
yellow labels) contain warm pixels, so yellow/amber hues are excluded
by the hue gates and every verdict needs the square+circle geometry.

Return shape:
  {status, classification, confidence, provenance, reason, ...}
status: DETECTED | NOT_DETECTED | NEEDS_REVIEW
classification: VEGETARIAN | NON_VEGETARIAN | UNKNOWN

"Not detected" is NEVER a finding of non-compliance — poor image
quality returns NEEDS_REVIEW so the inspector verifies visually, and
UNKNOWN is never coerced into NON_VEGETARIAN.
"""
from __future__ import annotations

from typing import Any

# --- HSV gates (OpenCV hue: 0..179; red=0, yellow~25-35, green~60) ---
# Vegetarian green. Amber/yellow cooking-oil hues (~15-35) are
# deliberately OUTSIDE both gates: they are neither verdict.
_GREEN_HUE_LO = 35
_GREEN_HUE_HI = 85
# Non-veg brown/red wraps the hue origin, hence two intervals.
_BROWN_HUE = ((0, 12), (160, 180))
_MIN_SAT = 60
_MIN_VAL = 40

# --- geometry gates (anchored to the square re-located in the crop) ---
# Minimum share of the border ring covered by mark-coloured pixels.
_RING_MIN_FILL = 0.15
# Minimum share of the centre disc covered by mark-coloured pixels.
_DISC_MIN_FILL = 0.35
# Minimum circularity (4*pi*area/perimeter^2) of the centre blob.
_MIN_CIRCULARITY = 0.50
# Whole-frame fallback: colour blob size as a fraction of the frame.
_BLOB_MIN_FRAC = 0.0002
_BLOB_MAX_FRAC = 0.02
# Minimum IoU between the colour blob box and a square contour box.
_MIN_BLOB_SQUARE_IOU = 0.25
# Verified-candidate confidence floor for a DETECTED verdict.
_VERIFY_MIN_CONF = 0.55
# Confidence when nothing mark-like is found (numeric, never None, so
# callers can always render "no evidence" honestly).
_NO_EVIDENCE_CONF = 0.3


def detect_veg_symbol(images: list[bytes]) -> dict[str, Any]:
    """Detect the veg/non-veg mark across uploaded package images."""
    if not images:
        return {"status": "NOT_DETECTED", "classification": "UNKNOWN",
                "confidence": None, "provenance": "IMAGE",
                "reason": "no package images supplied for symbol detection."}
    try:
        from PIL import Image
    except ImportError:
        return {"status": "NEEDS_REVIEW", "classification": "UNKNOWN",
                "confidence": None, "provenance": "IMAGE",
                "reason": "image analysis unavailable (Pillow missing); "
                          "inspector must verify the symbol visually."}
    import io

    best: dict[str, Any] | None = None
    failures = 0
    for index, raw in enumerate(images):
        if not raw:
            continue
        try:
            img = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            failures += 1
            continue
        verdict = _score_image(img, index)
        if verdict is None:
            continue
        if best is None or verdict["confidence"] > (best["confidence"] or 0):
            best = verdict
    if best is None:
        if failures == len([r for r in images if r]):
            return {"status": "NEEDS_REVIEW", "classification": "UNKNOWN",
                    "confidence": None, "provenance": "IMAGE",
                    "reason": "package images could not be decoded for "
                              "symbol analysis; inspector must verify visually."}
        return {"status": "NOT_DETECTED", "classification": "UNKNOWN",
                "confidence": _NO_EVIDENCE_CONF, "provenance": "IMAGE",
                "reason": "no vegetarian/non-vegetarian symbol pattern found "
                          "in the supplied images; not a finding of "
                          "non-compliance — inspector may verify visually."}
    return best


def _cv2_module() -> Any | None:
    try:
        import cv2

        return cv2
    except Exception:
        return None


def _to_hsv(image_np: Any) -> Any | None:
    """RGB numpy image -> OpenCV HSV array. None when cv2 missing."""
    cv2 = _cv2_module()
    if cv2 is None:
        return None
    try:
        import numpy as np

        arr = np.asarray(image_np)
        if arr.size == 0:
            return None
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        return cv2.cvtColor(arr.astype("uint8"), cv2.COLOR_RGB2HSV)
    except Exception:
        return None


def _color_masks(hsv: Any) -> tuple[Any, Any] | tuple[None, None]:
    """Hue-gated (green, brown-red) masks. Amber/yellow excluded."""
    cv2 = _cv2_module()
    if cv2 is None or hsv is None:
        return None, None
    try:
        import numpy as np

        green = cv2.inRange(
            hsv,
            np.array([_GREEN_HUE_LO, _MIN_SAT, _MIN_VAL], dtype=np.uint8),
            np.array([_GREEN_HUE_HI, 255, 255], dtype=np.uint8))
        brown = None
        for lo, hi in _BROWN_HUE:
            part = cv2.inRange(
                hsv,
                np.array([lo, _MIN_SAT, _MIN_VAL], dtype=np.uint8),
                np.array([hi, 255, 255], dtype=np.uint8))
            brown = part if brown is None else cv2.bitwise_or(brown, part)
        return green, brown
    except Exception:
        return None, None


def _circularity(contour: Any) -> float:
    cv2 = _cv2_module()
    if cv2 is None:
        return 0.0
    try:
        area = float(cv2.contourArea(contour))
        peri = float(cv2.arcLength(contour, True))
        if area <= 0 or peri <= 0:
            return 0.0
        import math

        return min(1.0, 4.0 * math.pi * area / (peri * peri))
    except Exception:
        return 0.0


def _quad_boxes_in_crop(crop_np: Any) -> list[tuple[int, int, int, int]]:
    """Square-outline boxes found INSIDE a candidate crop.

    Self-calibrating: the candidate box from the full frame is often
    loose (margin), so the verifier re-locates the square here and
    anchors the border ring / centre disc to it instead of the crop
    edge. Pure geometry; never raises.
    """
    cv2 = _cv2_module()
    if cv2 is None:
        return []
    try:
        import numpy as np

        arr = np.asarray(crop_np)
        if arr.size == 0:
            return []
        gray = (cv2.cvtColor(arr.astype("uint8"), cv2.COLOR_RGB2GRAY)
                if arr.ndim == 3 else arr.astype("uint8"))
        h, w = gray.shape[:2]
        if min(h, w) < 12:
            return []
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(h * w)
        boxes: list[tuple[float, tuple[int, int, int, int]]] = []
        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            frac = area / max(frame_area, 1.0)
            # The mark dominates its own candidate crop.
            if not 0.05 <= frac <= 0.95:
                continue
            peri = cv2.arcLength(cnt, True)
            if peri <= 0:
                continue
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            pts = approx.reshape(4, 2).astype("float32")
            xs, ys = pts[:, 0], pts[:, 1]
            bw = float(xs.max() - xs.min())
            bh = float(ys.max() - ys.min())
            if bw <= 0 or bh <= 0:
                continue
            if min(bw, bh) / max(bw, bh) < 0.70:
                continue
            boxes.append((frac, (int(xs.min()), int(ys.min()),
                                int(xs.max()), int(ys.max()))))
        boxes.sort(reverse=True)
        return [b for _, b in boxes[:3]]
    except Exception:
        return []


def _verify_symbol_crop(crop_np: Any) -> tuple[str, float, str]:
    """Verify one candidate crop: coloured border ring + filled centre.

    The square is re-located inside the crop (see
    :func:`_quad_boxes_in_crop`); the border ring hugs that square and
    the centre disc sits at its middle. Colour (hue-gated HSV) AND
    geometry must agree for one hue; anything else is UNKNOWN (never
    a negative verdict).
    Returns (classification, confidence, note).
    """
    import numpy as np

    hsv = _to_hsv(crop_np)
    if hsv is None:
        return "UNKNOWN", 0.0, "cv2 unavailable for crop verification"
    green, brown = _color_masks(hsv)
    if green is None or brown is None:
        return "UNKNOWN", 0.0, "colour masks unavailable"
    h, w = hsv.shape[:2]
    quads = _quad_boxes_in_crop(np.asarray(crop_np))
    if not quads:
        return ("UNKNOWN", 0.0,
                "no square outline re-located inside the candidate crop")
    yy, xx = np.mgrid[0:h, 0:w]
    out: list[tuple[float, str, str]] = []
    for qx0, qy0, qx1, qy1 in quads:
        bw, bh = qx1 - qx0, qy1 - qy0
        if bw < 8 or bh < 8:
            continue
        ring_w = max(2, int(min(bw, bh) * 0.10))
        in_box = (xx >= qx0) & (xx <= qx1) & (yy >= qy0) & (yy <= qy1)
        inner = (xx >= qx0 + ring_w) & (xx <= qx1 - ring_w) & (
            yy >= qy0 + ring_w) & (yy <= qy1 - ring_w)
        ring = in_box & ~inner
        cx, cy = (qx0 + qx1) / 2.0, (qy0 + qy1) / 2.0
        disc_r = max(3.0, min(bw, bh) * 0.28)
        disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= disc_r ** 2
        for mask, cls in ((green, "VEGETARIAN"), (brown, "NON_VEGETARIAN")):
            mask_bool = mask > 0
            ring_fill = float(mask_bool[ring].mean()) if ring.any() else 0.0
            disc_fill = float(mask_bool[disc].mean()) if disc.any() else 0.0
            if ring_fill < _RING_MIN_FILL or disc_fill < _DISC_MIN_FILL:
                continue
            # Circularity of the largest centre blob (the filled dot).
            circ = 0.0
            try:
                cv2 = _cv2_module()
                centre = np.zeros_like(mask)
                centre[disc] = mask[disc]
                contours, _ = cv2.findContours(
                    centre, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if contours:
                    biggest = max(contours, key=cv2.contourArea)
                    circ = _circularity(biggest)
            except Exception:
                circ = 0.0
            if circ < _MIN_CIRCULARITY:
                continue
            conf = min(0.95, 0.45 + 0.25 * min(ring_fill * 3.0, 1.0)
                       + 0.20 * disc_fill + 0.10 * circ)
            out.append((round(conf, 3), cls,
                        f"ring {ring_fill:.2f}, disc {disc_fill:.2f}, "
                        f"circularity {circ:.2f}"))
    if not out:
        return ("UNKNOWN", 0.0,
                "square geometry present but no hue shows a coloured "
                "border with a filled centre disc")
    out.sort(reverse=True)
    return out[0][1], out[0][0], out[0][2]


def _crop_rel(img: Any, rel_box: list | tuple,
              margin: float = 1.6) -> Any | None:
    """Crop a relative box (0..1) with margin from a PIL image."""
    try:
        w, h = img.size
        pad_w = (rel_box[2] - rel_box[0]) * (margin - 1.0) / 2.0
        pad_h = (rel_box[3] - rel_box[1]) * (margin - 1.0) / 2.0
        x0 = max(0, int((rel_box[0] - pad_w) * w))
        y0 = max(0, int((rel_box[1] - pad_h) * h))
        x1 = min(w, int((rel_box[2] + pad_w) * w))
        y1 = min(h, int((rel_box[3] + pad_h) * h))
        if x1 - x0 < 12 or y1 - y0 < 12:
            return None
        return img.crop((x0, y0, x1, y1))
    except Exception:
        return None


def _score_image(img: Any, index: int) -> dict[str, Any] | None:
    """Score one image: geometry-first candidates, then strict fallback."""
    if _cv2_module() is None:
        # No OpenCV: square+circle geometry cannot be checked, and the
        # legacy colour-blob heuristic misfires on amber/green packs.
        # Refuse to guess instead of risking a wrong verdict.
        return {"status": "NEEDS_REVIEW", "classification": "UNKNOWN",
                "confidence": None, "provenance": "IMAGE",
                "image_index": index,
                "reason": "OpenCV unavailable: symbol geometry cannot be "
                          "verified; inspector must verify visually. This "
                          "is not a finding of non-compliance."}
    # 1. Verify each square-outline candidate crop (colour + geometry).
    try:
        cands = find_symbol_candidates(img, max_candidates=3)
    except Exception:
        cands = []
    verified: list[tuple[float, str, str, list]] = []
    for cand in cands:
        rel = cand.get("rel_box") or []
        if len(rel) != 4:
            continue
        crop = _crop_rel(img, rel)
        if crop is None:
            continue
        cls, conf, note = _verify_symbol_crop(crop)
        if cls != "UNKNOWN" and conf >= _VERIFY_MIN_CONF:
            verified.append((conf, cls, note, list(rel)))
    if verified:
        verified.sort(reverse=True)
        conf, cls, note, rel = verified[0]
        return {"status": "DETECTED", "classification": cls,
                "confidence": conf, "provenance": "IMAGE",
                "image_index": index, "rel_box": rel,
                "reason": f"{cls.lower()} symbol verified visually "
                          f"(square border + filled centre, {note})."}
    # 2. Whole-frame fallback: hue-gated colour AND an overlapping
    # square contour must agree; colour without geometry is only
    # UNCERTAIN (amber/green packs must not become verdicts).
    fallback = _whole_frame_verdict(img, cands)
    if fallback is not None:
        status, cls, conf, reason = fallback
        return {"status": status, "classification": cls,
                "confidence": conf, "provenance": "IMAGE",
                "image_index": index, "reason": reason}
    return None


def _whole_frame_verdict(
        img: Any, cands: list[dict[str, Any]]
) -> tuple[str, str, float, str] | None:
    """Strict whole-frame check for images with no verifiable crop."""
    import numpy as np

    cv2 = _cv2_module()
    if cv2 is None:
        return None
    try:
        w, h = img.size
        scale = min(1.0, 900.0 / max(w, h))
        small = img.resize((max(1, int(w * scale)),
                            max(1, int(h * scale))))
        hsv = _to_hsv(np.asarray(small))
        green, brown = _color_masks(hsv)
        if green is None or brown is None:
            return None
        frame_area = float(small.size[0] * small.size[1])
        squares = [c.get("rel_box") for c in cands
                   if isinstance(c.get("rel_box"), list)]
        for mask, cls in ((green, "VEGETARIAN"),
                          (brown, "NON_VEGETARIAN")):
            n, _, stats, _ = cv2.connectedComponentsWithStats(
                mask, connectivity=8)
            for label in range(1, n):
                area = float(stats[label, cv2.CC_STAT_AREA])
                frac = area / max(frame_area, 1.0)
                if not _BLOB_MIN_FRAC <= frac <= _BLOB_MAX_FRAC:
                    continue
                bx = float(stats[label, cv2.CC_STAT_LEFT]) / small.size[0]
                by = float(stats[label, cv2.CC_STAT_TOP]) / small.size[1]
                bw = float(stats[label, cv2.CC_STAT_WIDTH]) / small.size[0]
                bh = float(stats[label, cv2.CC_STAT_HEIGHT]) / small.size[1]
                blob_box = [bx, by, bx + bw, by + bh]
                if _overlaps_square(blob_box, squares):
                    conf = round(min(0.78, 0.55 + frac * 8.0), 3)
                    return ("DETECTED", cls, conf,
                            f"{cls.lower()} colour region coincides with "
                            f"a square outline (coverage {frac:.4f}); "
                            f"crop verification inconclusive.")
            # Colour present but geometry missing: uncertain, never a
            # verdict (this is the amber-oil case — warm pixels with no
            # square+circle mark stay UNKNOWN).
            for mask2 in (green, brown):
                if float((mask2 > 0).mean()) >= _BLOB_MIN_FRAC:
                    return ("NEEDS_REVIEW", "UNKNOWN", 0.4,
                            "mark-like colours present but no square+circle "
                            "geometry found; inspector must verify "
                            "visually. This is not a finding of "
                            "non-compliance.")
        return None
    except Exception:
        return None


def _overlaps_square(blob_box: list[float],
                     squares: list[list[float]]) -> bool:
    """Whether a blob box overlaps any candidate square (IoU gate)."""
    try:
        bx0, by0, bx1, by1 = blob_box
        b_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
        if b_area <= 0:
            return False
        for sq in squares:
            if not isinstance(sq, list) or len(sq) != 4:
                continue
            ix0, iy0 = max(bx0, sq[0]), max(by0, sq[1])
            ix1, iy1 = min(bx1, sq[2]), min(by1, sq[3])
            inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
            s_area = max(0.0, sq[2] - sq[0]) * max(0.0, sq[3] - sq[1])
            union = b_area + s_area - inter
            if union > 0 and inter / union >= _MIN_BLOB_SQUARE_IOU:
                return True
        return False
    except Exception:
        return False


def classify_symbol_crop(image_np: Any,
                         rel_box: list | tuple | None = None,
                         margin: float = 1.5,
                         min_size: int = 120) -> dict[str, Any]:
    """Classify one symbol-crop ROI (Stage 3C §K).

    Crops ``rel_box`` (0..1 relative coords; full frame when None) with
    a margin, upscales small marks to a readable floor, then verifies
    the square-border + filled-centre geometry in HSV — never
    whole-image colours, never ingredient names. Returns
    {classification, confidence, status, reason, rel_box}. Ambiguous
    crops yield UNKNOWN / NEEDS_REVIEW. Never raises.
    """
    try:
        from PIL import Image as _Image

        import numpy as np

        if isinstance(image_np, (bytes, bytearray)):
            import io as _io

            img = _Image.open(_io.BytesIO(bytes(image_np))).convert("RGB")
        elif hasattr(image_np, "convert"):
            img = image_np.convert("RGB")
        else:
            img = _Image.fromarray(np.asarray(image_np)).convert("RGB")
        if rel_box is not None and len(rel_box) == 4:
            crop = _crop_rel(img, list(rel_box), margin=margin)
            if crop is not None:
                img = crop
        cw, ch = img.size
        if max(cw, ch) < min_size and max(cw, ch) > 0:
            factor = min(min_size / max(cw, ch), 8.0)
            img = img.resize((max(1, int(cw * factor)),
                              max(1, int(ch * factor))))
        cls, conf, note = _verify_symbol_crop(img)
        if cls == "UNKNOWN":
            return {"classification": "UNKNOWN",
                    "confidence": None, "status": "NEEDS_REVIEW",
                    "reason": "symbol crop verification inconclusive "
                    f"({note}); inspector must verify visually.",
                    "rel_box": list(rel_box) if rel_box else None}
        return {"classification": cls, "confidence": conf,
                "status": "DETECTED",
                "reason": f"symbol-crop verified: {note}",
                "rel_box": list(rel_box) if rel_box else None}
    except Exception as exc:
        return {"classification": "UNKNOWN", "confidence": None,
                "status": "NEEDS_REVIEW",
                "reason": "symbol crop classification failed safely: "
                f"{type(exc).__name__}",
                "rel_box": list(rel_box) if rel_box else None}


def find_symbol_candidates(image_np: Any,
                           max_candidates: int = 3) -> list[dict[str, Any]]:
    """Targeted veg-symbol candidate regions (Stage 3B.12).

    Finds small square-outline contours (the FSSAI mark is a circle in
    a ~4-12mm square) on a small working copy and returns them in
    resolution-independent relative coordinates::

        [{rel_box: [x0, y0, x1, y1], score, reason}]

    Pure geometry + size gating — colour verdicts still come from
    :func:`_verify_symbol_crop` on the crop. Never raises; empty list
    means "no candidate geometry found" (caller uses the fallback).
    Costs no OCR calls.
    """
    out: list[dict[str, Any]] = []
    try:
        from PIL import Image as _Image

        import numpy as np

        if isinstance(image_np, (bytes, bytearray)):
            import io as _io

            img = _Image.open(_io.BytesIO(bytes(image_np))).convert("RGB")
        elif hasattr(image_np, "convert"):
            img = image_np.convert("RGB")
        else:
            img = _Image.fromarray(np.asarray(image_np)).convert("RGB")
        w, h = img.size
        if max(w, h) <= 0:
            return out
        scale = min(1.0, 600.0 / max(w, h))
        small = img.resize((max(1, int(w * scale)),
                            max(1, int(h * scale))))
        sw, sh = small.size
    except Exception:
        return out
    cv2 = None
    try:
        import cv2 as _cv2

        cv2 = _cv2
    except Exception:
        return out
    try:
        import numpy as np

        gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(sw * sh)
        scored: list[tuple[float, list[float]]] = []
        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            frac = area / max(frame_area, 1.0)
            # A pack mark is small: between a speck and a panel.
            if not 0.0002 <= frac <= 0.02:
                continue
            peri = cv2.arcLength(cnt, True)
            if peri <= 0:
                continue
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            pts = approx.reshape(4, 2).astype("float32")
            xs, ys = pts[:, 0], pts[:, 1]
            bw = float(xs.max() - xs.min())
            bh = float(ys.max() - ys.min())
            if bw <= 0 or bh <= 0:
                continue
            squareness = min(bw, bh) / max(bw, bh)
            if squareness < 0.75:
                continue
            score = round(min(1.0, 0.5 + squareness * 0.4
                              + min(frac * 20, 0.1)), 3)
            rel = [round(float(xs.min()) / sw, 4),
                   round(float(ys.min()) / sh, 4),
                   round(float(xs.max()) / sw, 4),
                   round(float(ys.max()) / sh, 4)]
            scored.append((score, rel))
        scored.sort(reverse=True)
        for score, rel in scored[:max(1, max_candidates)]:
            out.append({"rel_box": rel, "score": score,
                        "reason": "small square-outline contour; "
                        "colour verdict still required"})
    except Exception:
        return out
    return out
