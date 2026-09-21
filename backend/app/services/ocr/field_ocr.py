"""Field-specific targeted OCR (Stage 3C §C).

``extract_field_from_region`` is the single reusable entry point for
re-reading one evidence region with the minimum required OCR:

1. crop the region (+ small context margin, never the full image),
2. upscale small text to a readable floor (capped, never full-frame),
3. run exactly ONE provider pass (no variant sweep),
4. return text + confidence + boxes + preprocessing metadata,
5. preserve evidence (source image, rect, variant).

The ingredient path keeps its own coherence-scored variant loop
(``service._run_ingredient_variants``); every other targeted region
crop goes through here so variant/prep policy stays in one place.
Callers own budgets and tagging — this function never calls the
provider more than once.
"""
from __future__ import annotations

import time
from typing import Any

from app.services.ocr.base import OcrLine

# Per-field crop policy: context margin (fraction of region height
# added on each side) and minimum crop height in pixels (upscale
# small text to this floor). Tight for dates/codes, roomier for
# addresses and care blocks.
_FIELD_CROP_POLICY: dict[str, dict[str, float]] = {
    "manufacturing_date": {"margin": 1.2, "min_height": 120.0},
    "best_before": {"margin": 1.2, "min_height": 120.0},
    "use_by": {"margin": 1.2, "min_height": 120.0},
    "batch_lot": {"margin": 1.2, "min_height": 120.0},
    "batch": {"margin": 1.2, "min_height": 120.0},
    "mrp": {"margin": 1.5, "min_height": 150.0},
    "fssai_license": {"margin": 1.5, "min_height": 150.0},
    "consumer_care": {"margin": 2.0, "min_height": 150.0},
    "manufacturer": {"margin": 2.0, "min_height": 200.0},
    "quantity": {"margin": 1.5, "min_height": 150.0},
}
_DEFAULT_POLICY = {"margin": 1.5, "min_height": 150.0}

# Hard ceiling: crops never exceed this (never full-image upscale).
_CROP_MAX_DIM = 1800


def _policy_for(field_type: str | None) -> dict[str, float]:
    return _FIELD_CROP_POLICY.get(str(field_type or ""), _DEFAULT_POLICY)


def crop_with_context(image_pil: Any, rect_stage: tuple,
                      scale_xy: tuple, field_type: str | None = None,
                      image_size: tuple | None = None) -> Any | None:
    """Crop a stage-coordinate rect with a field-appropriate margin.

    Returns None when degenerate. Never raises (caller falls back).
    """
    try:
        pol = _policy_for(field_type)
        x0, y0, x1, y1 = (float(v) for v in rect_stage)
        sx, sy = float(scale_xy[0]), float(scale_xy[1])
        h_stage = max(y1 - y0, 1.0)
        margin = h_stage * float(pol["margin"])
        orig_w, orig_h = image_pil.size
        crop = image_pil.crop((
            max(0, int((x0 - margin) * sx)),
            max(0, int((y0 - margin) * sy)),
            min(orig_w, int((x1 + margin) * sx)),
            min(orig_h, int((y1 + margin) * sy))))
        if crop.size[0] < 8 or crop.size[1] < 8:
            return None
        # Upscale small text to the readability floor (capped).
        min_h = float(pol["min_height"])
        if crop.size[1] < min_h:
            factor = min(min_h / max(crop.size[1], 1.0),
                         _CROP_MAX_DIM / max(crop.size))
            if factor > 1.0:
                try:
                    from PIL import Image as _Image

                    resample = _Image.LANCZOS
                except Exception:  # pragma: no cover
                    resample = 1
                crop = crop.resize(
                    (max(1, int(crop.size[0] * factor)),
                     max(1, int(crop.size[1] * factor))), resample)
        _ = image_size
        return crop
    except Exception:
        return None


def extract_field_from_region(
    image_pil: Any,
    region: dict[str, Any],
    field_type: str | None,
    provider: Any,
    image_id: str = "",
    call_provider: Any | None = None,
) -> dict[str, Any]:
    """One region, one OCR pass, structured evidence out.

    ``region`` needs ``rect`` (stage coords); ``scale`` (stage->pixel)
    and ``stage_size`` are read when present. ``call_provider`` is the
    service-level single-pass runner (keeps call_log/timings unified);
    when omitted the provider is invoked directly. Returns::

        {lines, text, confidence, boxes, preprocessing, ms, rect}

    ``preprocessing`` records variant + crop geometry. Never raises:
    failure yields empty lines with the reason recorded.
    """
    t0 = time.perf_counter()
    result: dict[str, Any] = {
        "lines": [], "text": "", "confidence": None, "boxes": [],
        "preprocessing": {"variant": "smallprint-single",
                          "field_type": field_type,
                          "crop_rect": None, "crop_size": None,
                          "deskewed": False,
                          "perspective_corrected": False},
        "ms": 0.0, "rect": (region or {}).get("rect"),
        "error": None,
    }
    try:
        rect = (region or {}).get("rect")
        scale = (region or {}).get("scale") or (1.0, 1.0)
        if rect is None:
            result["error"] = "no region rect"
            return result
        crop = crop_with_context(image_pil, tuple(rect), tuple(scale),
                                 field_type)
        if crop is None:
            result["error"] = "degenerate crop"
            return result
        # Crop conditioning (deskew/rectify): preprocessing only.
        try:
            from app.services.ocr import opencv_preprocessor as ocv

            import numpy as np

            arr = np.asarray(crop.convert("L"))
            straight, applied, angle, notes = ocv.deskew_image(arr)
            result["preprocessing"]["deskewed"] = bool(applied)
            result["preprocessing"]["deskew_angle"] = angle
            result["preprocessing"]["notes"] = notes
            if applied:
                from PIL import Image as _Image

                crop = _Image.fromarray(straight).convert("RGB")
        except Exception as exc:
            result["preprocessing"]["notes"] = [f"conditioning skipped: "
                                                f"{exc}"]
        try:
            result["preprocessing"]["crop_size"] = [crop.size[0],
                                                    crop.size[1]]
        except Exception:
            pass
        # Single small-print pass (no variant sweep — budget discipline).
        from app.services.ocr.service import _prep_region

        arr = _prep_region(crop.convert("RGB"), "smallprint")
        if call_provider is not None:
            lines = call_provider(arr, dict(result["preprocessing"]))
        else:
            out = provider.extract(arr, image_id)
            lines = list(getattr(out, "lines", []) or [])
        texts = [str(getattr(ln, "text", "") or "") for ln in lines]
        confs = []
        for ln in lines:
            try:
                confs.append(float(getattr(ln, "confidence", 0) or 0))
            except (TypeError, ValueError):
                pass
        result["lines"] = lines
        result["text"] = "\n".join(t for t in texts if t)
        result["confidence"] = (round(sum(confs) / len(confs), 3)
                                if confs else None)
        result["boxes"] = [getattr(ln, "box", None) for ln in lines]
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return result


def mean_confidence(lines: list[OcrLine]) -> float | None:
    """Mean line confidence (None when no usable confidences)."""
    vals = []
    for ln in lines or []:
        try:
            vals.append(float(getattr(ln, "confidence", 0) or 0))
        except (TypeError, ValueError):
            pass
    return round(sum(vals) / len(vals), 3) if vals else None
