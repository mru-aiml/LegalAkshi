"""Vegetarian / non-vegetarian symbol detection (image analysis, not OCR).

The FSSAI veg logo is a filled circle inside a square outline (green for
vegetarian, brown/red for non-vegetarian), usually 4-12 mm on pack. This
module looks for that visual pattern with PIL/numpy colour analysis and
falls back to OCR text cues ("veg logo", "non-veg").

Return shape:
  {status, classification, confidence, provenance, reason, ...}
status: DETECTED | NOT_DETECTED | NEEDS_REVIEW
classification: VEGETARIAN | NON_VEGETARIAN | UNKNOWN

"Not detected" is NEVER a finding of non-compliance — poor image quality
returns NEEDS_REVIEW so the inspector verifies visually.
"""
from __future__ import annotations

from typing import Any


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
                "confidence": 0.3, "provenance": "IMAGE",
                "reason": "no vegetarian/non-vegetarian symbol pattern found "
                          "in the supplied images; not a finding of "
                          "non-compliance — inspector may verify visually."}
    return best


def _score_image(img: Any, index: int) -> dict[str, Any] | None:
    """Score one image for square-outline + filled-circle colour blobs."""
    import numpy as np

    w, h = img.size
    if max(w, h) > 900:
        scale = 900 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)))
        w, h = img.size
    arr = np.asarray(img).astype(float)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    # vegetarian green: strong G, weak R/B. non-veg brown-red: strong R,
    # weak G/B.
    green = (g > 110) & (g > r + 25) & (g > b + 25) & (g < 235)
    brown = (r > 100) & (r > g + 20) & (b < g + 10) & (r < 240)
    out = []
    for mask, cls in ((green, "VEGETARIAN"), (brown, "NON_VEGETARIAN")):
        frac = float(mask.mean())
        if frac < 0.0002:  # fewer than ~a speck: no mark
            continue
        compact = _compactness(mask)
        # A logo is a small compact blob (<2% of frame, reasonably clustered).
        if frac > 0.02 or compact < 0.15:
            score = 0.35
        else:
            score = min(0.92, 0.55 + compact * 0.4 + min(frac * 40, 0.15))
        out.append((score, cls, frac, compact))
    if not out:
        return None
    out.sort(reverse=True)
    score, cls, frac, compact = out[0]
    if score < 0.5:
        return {"status": "NEEDS_REVIEW", "classification": "UNKNOWN",
                "confidence": round(score, 3), "provenance": "IMAGE",
                "image_index": index,
                "reason": "possible symbol colours present but the mark is "
                          "not clearly resolvable (poor lighting/crop); "
                          "inspector must verify visually. This is not a "
                          "finding of non-compliance."}
    return {"status": "DETECTED", "classification": cls,
            "confidence": round(score, 3), "provenance": "IMAGE",
            "image_index": index,
            "reason": f"{cls.lower()} symbol pattern (filled circle in "
                      f"square outline) detected by image analysis "
                      f"(coverage {frac:.4f}, compactness {compact:.2f})."}


def _compactness(mask: Any) -> float:
    """Fraction of colour pixels inside their bounding box (clusteredness)."""
    import numpy as np

    ys, xs = np.where(mask)
    if len(xs) < 8:
        return 0.0
    bw = max(1, xs.max() - xs.min())
    bh = max(1, ys.max() - ys.min())
    area = bw * bh
    if area <= 0:
        return 0.0
    return min(1.0, len(xs) / area)


def classify_symbol_crop(image_np: Any,
                         rel_box: list | tuple | None = None,
                         margin: float = 1.5,
                         min_size: int = 120) -> dict[str, Any]:
    """Classify one symbol-crop ROI (Stage 3C §K).

    Crops ``rel_box`` (0..1 relative coords; full frame when None) with
    a margin, upscales small marks to a readable floor, then scores ONLY
    the crop — never whole-image colours, never ingredient names.
    Returns {classification, confidence, status, reason, rel_box}.
    Ambiguous crops yield UNKNOWN / NEEDS_REVIEW. Never raises.
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
        w, h = img.size
        if rel_box is not None and len(rel_box) == 4:
            pad_w = (rel_box[2] - rel_box[0]) * (margin - 1.0) / 2.0
            pad_h = (rel_box[3] - rel_box[1]) * (margin - 1.0) / 2.0
            x0 = max(0, int((rel_box[0] - pad_w) * w))
            y0 = max(0, int((rel_box[1] - pad_h) * h))
            x1 = min(w, int((rel_box[2] + pad_w) * w))
            y1 = min(h, int((rel_box[3] + pad_h) * h))
            if x1 - x0 >= 8 and y1 - y0 >= 8:
                img = img.crop((x0, y0, x1, y1))
        cw, ch = img.size
        if max(cw, ch) < min_size and max(cw, ch) > 0:
            factor = min(min_size / max(cw, ch), 8.0)
            img = img.resize((max(1, int(cw * factor)),
                              max(1, int(ch * factor))))
        verdict = _score_image(img, 0)
        if verdict is None:
            return {"classification": "UNKNOWN", "confidence": None,
                    "status": "NEEDS_REVIEW",
                    "reason": "symbol crop has no resolvable colour "
                    "pattern; inspector must verify visually.",
                    "rel_box": list(rel_box) if rel_box else None}
        return {"classification": verdict["classification"],
                "confidence": verdict["confidence"],
                "status": verdict["status"],
                "reason": "symbol-crop verdict: "
                + str(verdict.get("reason", "")),
                "rel_box": list(rel_box) if rel_box else None}
    except Exception as exc:
        return {"classification": "UNKNOWN", "confidence": None,
                "status": "NEEDS_REVIEW",
                "reason": f"symbol crop classification failed safely: "
                f"{type(exc).__name__}",
                "rel_box": list(rel_box) if rel_box else None}
    """Fraction of colour pixels inside their bounding box (clusteredness)."""
    import numpy as np

    ys, xs = np.where(mask)
    if len(xs) < 8:
        return 0.0
    bw = max(1, xs.max() - xs.min())
    bh = max(1, ys.max() - ys.min())
    area = bw * bh
    if area <= 0:
        return 0.0
    return min(1.0, len(xs) / area)


def find_symbol_candidates(image_np: Any,
                           max_candidates: int = 3) -> list[dict[str, Any]]:
    """Targeted veg-symbol candidate regions (Stage 3B.12).

    Finds small square-outline contours (the FSSAI mark is a circle in
    a ~4-12mm square) on a small working copy and returns them in
    resolution-independent relative coordinates::

        [{rel_box: [x0, y0, x1, y1], score, reason}]

    Pure geometry + size gating — colour verdicts still come from
    :func:`detect_veg_symbol` on the crop/full frame. Never raises;
    empty list means "no candidate geometry found" (caller uses the
    full image). Costs no OCR calls.
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
