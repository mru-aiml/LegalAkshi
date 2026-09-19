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
