"""OCR service: staged extraction — one fast pass, then targeted regions.

Pipeline (per image):
  STAGE 1 — exactly ONE fast RapidOCR pass on a downscaled working copy
      (large images are shrunk; small ones are left alone). Full-page OCR
      is never rerun for individual fields (budget: MAX_FULL_PAGE_CALLS ==
      number of images).
  STAGE 2 — region proposals from Stage-1 text + boxes: the ingredient
      column below an INGREDIENTS heading, MRP/date/FSSAI lines whose
      context was seen but whose value is missing or implausible, and —
      only for non-front views — the bottom band when MRP is absent.
      Capped at REGION_CALL_BUDGET logical regions per image.
  STAGE 3 — targeted OCR on those crops only
      (MAX_TARGETED_CALLS_PER_IMAGE provider calls per image, shared by
      ingredient variants, region crops and the single low-conf band;
      equivalent rects are never re-OCR'd for preprocessing variety;
      ingredient variants abort early on a coherent read).
  STAGE 4 — a single rotated pass, only when the required fields
      (quantity, MRP, manufacturing date) are ALL still unresolved and
      Stage 1 barely produced any lines (orientation suspect).
  STAGE 5 (opt-in) — ONE Tesseract ingredient crop per image, only when
      the ingredient gate fires (MAX_TESSERACT_CALLS_PER_IMAGE = 1).

Call-graph profile (why 4 images once cost 29 passes / ~176 s): every
image paid 1 Stage-1 + up to 4 ingredient variants + up to 5 further
region crops + up to 2 low-confidence bands + rotation + Tesseract with
no per-image ceiling and no duplicate-crop suppression, each RapidOCR
pass costing seconds on CPU. Stage-1B caps each image at
MAX_PROVIDER_CALLS_PER_IMAGE provider calls total and logs every
invocation (image_id, stage, purpose, provider, crop_rect, crop_size,
preprocessing_variant, duration_ms, reason) with aggregate
{total_ms, provider_calls, rapidocr_calls, tesseract_calls,
per_image_ms, stage_timings}.

The RapidOCR engine stays a lazy class-level singleton: it is created once
and reused across passes/images. Provider failures and empty results both
yield NEEDS_REVIEW with null fields — inspection continues with manual
entry instead of crashing.

OpenCV preprocessing/analysis layer (opencv_preprocessor, zero OCR
calls of its own): per-image quality diagnostics, EXIF transpose,
pixel-geometry orientation vote (chooses the single rotation
fallback's direction, never extra passes), conservative deskew and
inner-quad rectification of targeted crops only, a heading-anchored
layout map that tightens the ingredient band and proposes one
nutrition-table region, and quality-driven variant ordering within
the unchanged call budget. Every decision is recorded in timings /
diagnostics; cv2 absence degrades gracefully to the legacy path.

Every extracted field retains value, confidence, provenance (OCR), source
image, bounding box, per-field status (DETECTED / NEEDS_REVIEW /
NOT_DETECTED) and, for key fields seen on several photos, the reconciled
evidence sources. Low-confidence values are surfaced as NEEDS_REVIEW
downstream — never guessed.
"""
from __future__ import annotations

import io
import logging
import time
from typing import Any

from app.services.ocr.base import OcrError, OcrLine
from app.services.ocr.fields import (
    FIELD_KEYS,
    STATUS_CONF_THRESHOLD,
    extract_contact_codes,
    extract_fields,
    extract_fields_detailed,
    extract_with_status,
)

log = logging.getLogger("legalakshi.ocr")

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 6
# Stage-1 working size: big enough to read normal print, small enough that
# a single detector pass finishes in seconds on CPU.
STAGE1_MAX_DIM = 1280
# --- Stage-1B explicit OCR call budget (spec §2) ---
# One full-page OCR per image maximum; only targeted crops afterwards;
# Tesseract stays targeted (ingredient crops) and optional (gate + 1 max).
# MAX_FULL_PAGE_CALLS is dynamic: exactly the number of supplied images
# (one Stage-1 pass each; the Stage-4 rotation exception is separately
# capped at one per image and fires only on orientation evidence).
MAX_TARGETED_CALLS_PER_IMAGE = 3
MAX_TESSERACT_CALLS_PER_IMAGE = 1
# Total provider-call ceiling per image (full + targeted + tess/rot).
MAX_PROVIDER_CALLS_PER_IMAGE = 5
# Upper bound on targeted region kinds per image (Stage 2 proposals).
REGION_CALL_BUDGET = 3
# Ingredient OCR variants: original crop, then upscaled-contrast. At most
# this many provider calls per ingredient region; the loop aborts early
# once a variant reads coherently. Never run multiple equivalent crops
# merely for preprocessing variety.
INGREDIENT_VARIANT_BUDGET = 2
INGREDIENT_VARIANTS = ("orig", "up")
# Coherence at/above which further ingredient variants are skipped.
INGREDIENT_COHERENT_ABORT = 0.70
# Fields whose joint absence suggests a sideways photo (Stage 4 gate).
REQUIRED_FIELDS = ("quantity", "mrp", "manufacturing_date")

# Opt-in default for the hybrid Tesseract ingredient fallback. Stays None
# unless explicitly configured (production route enables it per request);
# unit tests never touch it, so legacy behaviour is byte-identical there.
_tess_provider_default: Any | None = None


def configure_tesseract_fallback(provider: Any | None) -> None:
    """Explicit opt-in for the hybrid ingredient fallback (app wiring).

    Pass None to disable. Never called implicitly: direct service calls
    keep pure-RapidOCR behaviour unless a caller opts in.
    """
    global _tess_provider_default
    _tess_provider_default = provider


def _resolve_tess_provider(explicit: Any | None) -> Any | None:
    return explicit if explicit is not None else _tess_provider_default


def preprocess(image_bytes: bytes) -> Any:
    """Decode + enhance a working copy. Raises OcrError when undecodable."""
    img = _decode(image_bytes)
    return _to_array(_enhance(_fit(img, STAGE1_MAX_DIM)))


def _decode(image_bytes: bytes) -> Any:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise OcrError(f"Pillow is not installed: {exc}")
    try:
        # EXIF orientation first: phone captures usually store rotation in
        # EXIF rather than pixels. Authoritative and free; no-op otherwise.
        # (This is pixel normalization, not an OCR pass.) The tag value is
        # recorded on the image so diagnostics can report it.
        exif_applied = False
        img = Image.open(io.BytesIO(image_bytes))
        try:
            tag = img.getexif().get(0x0112, 1)
            exif_applied = tag not in (None, 1)
        except Exception:
            pass
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img = img.convert("RGB")
        try:
            img.info["exif_orientation_applied"] = bool(exif_applied)
        except Exception:
            pass
        return img
    except Exception as exc:
        raise OcrError(f"cannot decode image: {exc}")


def _to_array(img: Any) -> Any:
    import numpy as np

    return np.array(img)


# ------------------------------------------------- Stage 3A dupes/norm ---
# Difference-hash threshold for near-duplicate uploads (64-bit dHash on
# a 9x8 grayscale thumb). Small enough that distinct panels never
# collide; tolerant of recompression/rescaling of the same shot.
# Reuse additionally requires both frames to carry real ink (or an
# exact hash match): near-blank frames are cheap to OCR and must never
# borrow another panel's text evidence.
DHASH_THRESHOLD = 5
DHASH_MIN_INK = 0.02


def _dhash_pil(img: Any) -> tuple[int | None, float]:
    """Perceptual difference hash + ink fraction of a PIL image.

    Returns (hash_or_None, ink_fraction). Ink is the fraction of dark
    thumb pixels; near-zero means an effectively blank frame.
    """
    try:
        small = img.convert("L").resize((9, 8))
        px = list(small.tobytes())
        bits = 0
        dark = 0
        for y in range(8):
            row = px[y * 9:(y + 1) * 9]
            for x in range(8):
                if row[x] < 128:
                    dark += 1
            for x in range(8):
                bits = (bits << 1) | (1 if row[x + 1] > row[x] else 0)
        return bits, dark / 72.0
    except Exception:
        return None, 0.0


def _hamming(a: int, b: int) -> int:
    """Hamming distance between two dHash ints."""
    try:
        return bin(int(a) ^ int(b)).count("1")
    except Exception:
        return 64


def _normalize_working_image(original: Any,
                             cv_orientation: dict[str, Any] | None,
                             image_quality: dict[str, Any] | None = None,
                             ) -> tuple[Any, dict[str, Any]]:
    """Stage 3A.1/3A.2 gated working-image normalization.

    Returns (working_image, metadata). The ORIGINAL upload bytes are
    never altered (callers keep them for evidence); only this working
    copy is adjusted, and only when evidence supports it:

    - decisive transpose vote -> rotate +90 (the remaining 90-vs-270
      ambiguity is resolved by the single rotation fallback, which
      then tries 180 on the normalized frame);
    - deskew when the helper applies it (same-size warp);
    - perspective warp only when a confident quad is found AND the
      readability grade is POOR (never on already-readable frames).

    Low-confidence orientation keeps prior behavior and is marked
    uncertain. Never raises.
    """
    from app.services.ocr import opencv_preprocessor as ocv

    meta: dict[str, Any] = {
        "orientation": {"original": "unknown", "normalized": "upright",
                        "rotation_applied": 0, "uncertain": True,
                        "exif_applied": False},
        "deskew_applied": False, "deskew_angle": 0.0,
        "perspective_corrected": False, "notes": []}
    working = original
    try:
        try:
            meta["orientation"]["exif_applied"] = bool(
                getattr(original, "info", {}).get(
                    "exif_orientation_applied", False))
        except Exception:
            pass
        cv_orientation = cv_orientation or {}
        decisive = bool(cv_orientation.get("decisive"))
        transpose = bool(cv_orientation.get("transpose"))
        if decisive and transpose:
            try:
                from PIL import Image as _Image

                working = working.transpose(_Image.ROTATE_90)
                meta["orientation"] = {
                    "original": "transposed-90/270",
                    "normalized": "upright-assumed",
                    "rotation_applied": 90, "uncertain": False,
                    "exif_applied": meta["orientation"]["exif_applied"]}
                meta["notes"].append(
                    "decisive transpose vote: working frame rotated +90; "
                    "the 90-vs-270 ambiguity stays with the rotation "
                    "fallback (180 on this frame)")
            except Exception as exc:
                meta["notes"].append(f"transpose rotation skipped: {exc}")
        elif decisive:
            meta["orientation"] = {
                "original": "upright (0/180)",
                "normalized": "upright",
                "rotation_applied": 0, "uncertain": False,
                "exif_applied": meta["orientation"]["exif_applied"]}
        else:
            meta["notes"].append(
                "orientation evidence indecisive; original geometry kept")
        # Deskew: same-size warp, helper-gated (0.5-12deg + text mask).
        try:
            import numpy as _np

            gray = _np.asarray(working.convert("L"))
            straight, applied, angle, notes = ocv.deskew_image(gray)
            meta["notes"].extend(notes)
            if applied:
                from PIL import Image as _Image

                working = _Image.fromarray(straight).convert("RGB")
                meta["deskew_applied"] = True
                meta["deskew_angle"] = angle
        except Exception as exc:
            meta["notes"].append(f"deskew skipped: {exc}")
        # Perspective: confident inner quad AND poor readability only.
        try:
            readability = ""
            if isinstance(image_quality, dict):
                readability = str(image_quality.get("readability", ""))
            if readability == "POOR":
                quad = ocv.find_quad(_np.asarray(working))
                meta["notes"].extend(quad.get("notes", []))
                if quad.get("found") and (quad.get("confidence") or 0) \
                        >= 0.70:
                    import numpy as _np2

                    from PIL import Image as _Image2

                    rel = quad["quad"]
                    h, w = working.size[1], working.size[0]
                    pts = _np2.array(
                        [[[x * w, y * h] for x, y in rel]],
                        dtype="float32")
                    ordered = ocv._order_points(pts.reshape(4, 2))
                    if ordered is not None:
                        (tl, tr, br, bl) = ordered
                        width = int(max(
                            float(_np2.linalg.norm(br - bl)),
                            float(_np2.linalg.norm(tr - tl))))
                        height = int(max(
                            float(_np2.linalg.norm(tr - tl)),
                            float(_np2.linalg.norm(bl - tl))))
                        if width >= 40 and height >= 40:
                            import cv2 as _cv2

                            dst = _np2.array(
                                [[0, 0], [width - 1, 0],
                                 [width - 1, height - 1], [0, height - 1]],
                                dtype="float32")
                            matrix = _cv2.getPerspectiveTransform(
                                ordered, dst)
                            warped = _cv2.warpPerspective(
                                _np2.asarray(working), matrix,
                                (width, height),
                                borderMode=_cv2.BORDER_REPLICATE)
                            working = _Image2.fromarray(warped).convert(
                                "RGB")
                            meta["perspective_corrected"] = True
                            meta["notes"].append(
                                "confident quad on poor-readability frame: "
                                "frontal warp applied")
            else:
                meta["notes"].append(
                    "perspective warp skipped (readability not poor)")
        except Exception as exc:
            meta["notes"].append(f"perspective skipped: {exc}")
    except Exception as exc:  # never break the pipeline
        meta["notes"].append(f"normalization skipped: {exc}")
        working = original
    return working, meta


def _enhance(img: Any, contrast: float = 1.3, sharpness: float = 1.2,
             cutoff: int = 1) -> Any:
    from PIL import ImageEnhance, ImageOps

    img = ImageOps.autocontrast(img, cutoff=cutoff)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Sharpness(img).enhance(sharpness)
    return img


def _fit(img: Any, max_dim: int) -> Any:
    """Downscale-only fit: large photos shrink, small ones are untouched."""
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    scale = max_dim / max(w, h)
    try:
        from PIL import Image

        resample = Image.LANCZOS
    except Exception:  # pragma: no cover
        resample = 1
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                      resample)


def _upscale_capped(img: Any, factor: float = 2.0,
                    max_dim: int = 1800) -> Any:
    w, h = img.size
    factor = min(factor, max_dim / max(w, h))
    if factor <= 1.0:
        return img
    try:
        from PIL import Image

        resample = Image.LANCZOS
    except Exception:  # pragma: no cover
        resample = 1
    return img.resize((int(w * factor), int(h * factor)), resample)


# ---------------------------------------------------------- box geometry ---
def _box_rect(box: Any) -> tuple[float, float, float, float] | None:
    """Normalise an OCR box to (x0, y0, x1, y1). None when unusable."""
    try:
        if isinstance(box, dict):
            x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
            return (x, y, x + w, y + h)
        pts = [list(map(float, p)) for p in box]  # type: ignore[union-attr]
        if len(pts) == 4 and all(len(p) == 2 for p in pts):
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            return (min(xs), min(ys), max(xs), max(ys))
        flat = [float(v) for p in pts for v in
                (p if isinstance(p, (list, tuple)) else [p])]
        if len(flat) == 8:
            xs, ys = flat[0::2], flat[1::2]
            return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return None


def _detect_orientation(stage_lines: list[OcrLine]
                        ) -> dict[str, Any]:
    """Estimate package rotation from Stage-1 box geometry.

    Text lines are wider than tall in reading direction. When the median
    boxed line is clearly taller than wide, the frame is treated as
    transposed (90-or-270 portrait capture) and region math runs in a
    transposed coordinate frame, mapped back to original coordinates for
    evidence. 0-vs-180 cannot be told apart geometrically (both read
    wide); upside-down text is left to the rotation fallback. Never
    rotates pixels here — only the coordinate frame for rectangles.
    """
    widths: list[float] = []
    heights: list[float] = []
    for ln in stage_lines or []:
        rect = _box_rect(getattr(ln, "box", None))
        if rect is None:
            continue
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w > 0 and h > 0:
            widths.append(w)
            heights.append(h)
    if not widths:
        return {"orientation": "0", "transpose": False, "ratio": None,
                "n": 0}
    widths.sort()
    heights.sort()
    mid = len(widths) // 2
    med_w = widths[mid] if len(widths) % 2 else \
        (widths[mid - 1] + widths[mid]) / 2.0
    med_h = heights[mid] if len(heights) % 2 else \
        (heights[mid - 1] + heights[mid]) / 2.0
    transpose = med_h > 1.25 * med_w
    return {"orientation": "transposed-90/270" if transpose else "0",
            "transpose": transpose,
            "ratio": round(med_h / med_w, 3) if med_w else None,
            "n": len(widths)}


def _transpose_rect(rect: tuple[float, float, float, float],
                    w: float, h: float
                    ) -> tuple[float, float, float, float]:
    """Map a rect into the transposed frame (swap axes). Self-inverse."""
    x0, y0, x1, y1 = rect
    return (y0, x0, y1, x1)


def _call_provider(provider: Any, image_np: Any, side: str,
                   errors: list[str],
                   call_log: list[dict[str, Any]] | None = None,
                   meta: dict[str, Any] | None = None) -> list[OcrLine]:
    """One provider pass; failures are recorded, never raised.

    Every invocation appends a structured timing diagnostic to
    ``call_log`` (when supplied)::

        {image_id, stage, purpose, provider, crop_rect, crop_size,
         preprocessing_variant, duration_ms, reason}
    """
    meta = meta or {}
    try:
        shape = getattr(image_np, "shape", None)
        if shape is not None:
            crop_size = [int(shape[1]), int(shape[0])]
        else:
            crop_size = None
    except Exception:
        crop_size = None
    t0 = time.perf_counter()
    try:
        lines = provider.extract(image_np, side).lines
        err = None
    except OcrError as exc:
        log.warning("ocr %s failed: %s", side, exc)
        errors.append(f"{side}: {exc}")
        lines, err = [], str(exc)
    except Exception as exc:  # defensive: provider contract says OcrError
        log.warning("ocr %s failed: %s", side, exc)
        errors.append(f"{side}: {exc}")
        lines, err = [], str(exc)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if call_log is not None:
        entry: dict[str, Any] = {
            "image_id": meta.get("image_id"),
            "stage": meta.get("stage"),
            "purpose": meta.get("purpose"),
            "provider": getattr(provider, "name", "ocr"),
            "crop_rect": meta.get("crop_rect"),
            "crop_size": crop_size,
            "preprocessing_variant": meta.get("preprocessing_variant"),
            "duration_ms": ms,
            "reason": err or meta.get("reason"),
            "lines": len(lines),
        }
        call_log.append(entry)
    return lines


def _rect_iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Intersection-over-union of two (x0, y0, x1, y1) rects."""
    try:
        ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
        ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0
    except (TypeError, IndexError):
        return 0.0


def _tag(lines: list[OcrLine], label: str, index: int,
         variant: str) -> list[OcrLine]:
    for ln in lines:
        ln.image = label
        try:
            ln.confidence = round(float(ln.confidence or 0), 3)
        except (TypeError, ValueError):
            ln.confidence = 0.0
        object.__setattr__(ln, "variant", variant)
        object.__setattr__(ln, "image_index", index)
    return lines


# ------------------------------------------------------- region proposals ---
def _propose_regions(label: str, stage_lines: list[OcrLine],
                     statuses: dict[str, dict[str, Any]],
                     stage_size: tuple[int, int]) -> list[dict[str, Any]]:
    """Stage-2 regions: targeted crops for missing/low-confidence fields.

    A region is proposed only when Stage-1 evidence suggests the field
    exists (heading/context line seen) but its value is unresolved — never
    blind full-frame reprocessing.
    """
    from app.services.ocr import fields as fields_mod
    from app.services.ocr import food as food_mod

    regions: list[dict[str, Any]] = []
    by_kind = {r["kind"] for r in regions}

    def _add(kind: str, rect: tuple[float, float, float, float] | None,
             prep: str) -> None:
        if rect is None or kind in by_kind:
            return
        regions.append({"kind": kind, "rect": rect, "prep": prep})
        by_kind.add(kind)

    # Ingredients: column-aware band below the heading. The heading box
    # anchors a visual text column (x-center / overlap clustering); the
    # crop follows that column downward instead of spanning the full
    # frame width, so adjacent marketing/storage/care columns cannot
    # enter ingredient OCR. Without usable boxes the legacy full-width
    # band applies (unchanged behaviour).
    orientation = _detect_orientation(stage_lines)
    transpose = bool(orientation.get("transpose"))
    frame_w, frame_h = stage_size
    work_w, work_h = (frame_h, frame_w) if transpose else (frame_w, frame_h)

    def _to_work(rect):
        return _transpose_rect(rect, frame_w, frame_h) if transpose else rect

    def _to_stage(rect):
        # Transposition is its own inverse with swapped dimensions.
        return _transpose_rect(rect, work_w, work_h) if transpose else rect

    # Strong openers first so a gated CONTENTS/CONTAINS line (or an
    # allergen "Contains: …" row) can never shadow the real heading.
    heading_hits: list[int] = []
    for i, ln in enumerate(stage_lines):
        text = ln.text or ""
        lo, hi = max(0, i - 2), i + 3
        ctx = " ".join((l.text or "") for l in stage_lines[lo:hi]
                       if (l.text or "") != text)
        if not food_mod._is_ingredient_heading(text, ctx):
            continue
        heading_hits.append(i)
    heading_hits.sort(key=lambda i: (
        0 if food_mod._heading_core(stage_lines[i].text or "")
        not in ("contents", "contains") else 1, i))
    for i in heading_hits:
        ln = stage_lines[i]
        text = ln.text or ""
        rect = _box_rect(ln.box)
        if rect is None:
            continue
        seed = _heading_remainder(text)
        hrect = _to_work(rect)
        # Cluster working-coord line rects into visual columns; the
        # heading selects its own column (boundary rule inside).
        import types as _types

        shims = []
        for j, other in enumerate(stage_lines):
            brect = _box_rect(getattr(other, "box", None))
            if brect is None:
                continue
            wr = _to_work(brect)
            shims.append(_types.SimpleNamespace(
                text=getattr(other, "text", "") or "", box=[
                    [wr[0], wr[1]], [wr[2], wr[1]],
                    [wr[2], wr[3]], [wr[0], wr[3]]], _idx=j))
        columns = food_mod.cluster_text_columns(shims)
        body_col = _select_body_column(
            hrect, columns, shims, food_mod, work_h)
        if body_col is not None:
            col_x0 = body_col["x0"]
            col_x1 = body_col["x1"]
            col_source = "heading-column"
            col_confidence = "high"
            height_cap = 0.45
        else:
            # Stage 3A.7 conservative fallback: no column resolved, so
            # do NOT fail open to a full-height band. Keep full width
            # (no x evidence) but cap the height tighter (30%) and mark
            # low confidence — coherence gates + NEEDS_REVIEW decide.
            col_x0, col_x1 = 0.0, work_w
            col_source = "full-width-fallback"
            col_confidence = "low"
            height_cap = 0.30
        _, _, _, y1 = hrect
        line_h = max(hrect[3] - hrect[1], 1.0)
        pad = min(max(0.6 * line_h, 4.0), 16.0)
        # Section stop: only lines inside the body column can end the
        # block, so a neighbouring column's MRP/care text never
        # truncates (or enters) the ingredient paragraph. Height cap
        # stays (45% column-resolved, 30% fallback).
        bottom = work_h
        for nxt in stage_lines[i + 1:i + 8]:
            nrect = _box_rect(getattr(nxt, "box", None))
            if nrect is None:
                continue
            wr = _to_work(nrect)
            if not _x_overlap(wr, (col_x0, col_x1), 0.3):
                continue
            if food_mod._is_section_head(nxt.text or "") and wr[1] > y1:
                bottom = wr[1]
                break
        bottom = min(bottom, y1 + height_cap * work_h)
        work_rect = (max(0.0, col_x0 - pad), y1,
                     min(work_w, col_x1 + pad),
                     min(work_h, y1 + max(bottom - y1, 0)))
        stage_rect = _to_stage(work_rect)
        _add("ingredients", stage_rect, "ingredients")
        for region in regions:
            if region["kind"] == "ingredients":
                if seed:
                    region["seed"] = seed
                region["column"] = {
                    "x0": round(col_x0, 1), "x1": round(col_x1, 1),
                    "source": col_source,
                    "confidence": col_confidence,
                    "frame": "transposed" if transpose else "stage"}
                region["orientation"] = orientation.get("orientation", "0")
        break


    mrp_status = statuses.get("mrp", {}).get("status")
    if mrp_status != "DETECTED":
        for ln in stage_lines:
            if (fields_mod._MRP_CTX.search(ln.text or "")
                    or fields_mod._MRP_CTX_OCR.search(ln.text or "")) \
                    and ln.box:
                r = _box_rect(ln.box)
                if r is not None:
                    pad = (r[3] - r[1]) * 1.2 + 4
                    _add("mrp", (max(0, r[0] - pad * 3), max(0, r[1] - pad),
                                 r[2] + pad * 3, r[3] + pad), "smallprint")
                    break
        else:
            # No MRP context at all: MRP usually sits at the bottom of the
            # back/side panel — one bottom-band pass, never for the front
            # principal display where it legitimately does not belong.
            if label != "front":
                w, h = stage_size
                _add("mrp", (0, h * 0.55, w, h), "smallprint")

    mfg_status = statuses.get("manufacturing_date", {}).get("status")
    if mfg_status != "DETECTED":
        for ln in stage_lines:
            text = ln.text or ""
            mfg_hit = fields_mod._MFG_CTX.search(text) or (
                fields_mod._MFG_CTX_OCR.search(text)
                and fields_mod._find_date(text) is not None)
            if mfg_hit and ln.box:
                r = _box_rect(ln.box)
                if r is not None:
                    pad = (r[3] - r[1]) * 1.2 + 4
                    _add("mfg_date", (max(0, r[0] - pad * 3),
                                      max(0, r[1] - pad),
                                      r[2] + pad * 3, r[3] + pad),
                         "smallprint")
                    break

    fssai_status = statuses.get("fssai_license", {}).get("status")
    if fssai_status != "DETECTED":
        for ln in stage_lines:
            if (fields_mod._FSSAI_CTX.search(ln.text or "")
                    or fields_mod._FSSAI_CTX_OCR.search(ln.text or "")) \
                    and ln.box:
                r = _box_rect(ln.box)
                if r is not None:
                    pad = (r[3] - r[1]) * 1.2 + 4
                    _add("fssai", (max(0, r[0] - pad * 3),
                                   max(0, r[1] - pad),
                                   r[2] + pad * 3, r[3] + pad),
                         "smallprint")
                    break
        else:
            # Stage-2B §7: aggressively target a bare 14-digit run when
            # OCR is weak and no licence context was read — the targeted
            # re-OCR plus the context/validation gates decide; a barcode
            # or phone run can never pass those gates.
            import re as _re

            for ln in stage_lines:
                if not ln.box:
                    continue
                digits = _re.sub(r"\D", "",
                                 str(ln.text or ""))
                if len(digits) == 14:
                    r = _box_rect(ln.box)
                    if r is not None:
                        pad = (r[3] - r[1]) * 1.2 + 4
                        _add("fssai", (max(0, r[0] - pad * 3),
                                       max(0, r[1] - pad),
                                       r[2] + pad * 3, r[3] + pad),
                             "smallprint")
                        break

    # Consumer care: expand a care-context line; the MRP bottom band
    # already covers the bottom panel for non-front views, so no second
    # blind band is proposed here.
    care_status = statuses.get("consumer_care", {}).get("status")
    if care_status != "DETECTED":
        for ln in stage_lines:
            text = ln.text or ""
            care_hit = fields_mod._CARE_CTX.search(text) or (
                fields_mod._CARE_CTX_OCR.search(text)
                and (fields_mod._PHONE_1800.search(text)
                     or fields_mod._PHONE_91.search(text)
                     or fields_mod._PHONE_10.search(text)
                     or "@" in text))
            if care_hit and ln.box:
                r = _box_rect(ln.box)
                if r is not None:
                    pad = (r[3] - r[1]) * 1.5 + 6
                    _add("care", (max(0, r[0] - pad * 4),
                                  max(0, r[1] - pad),
                                  r[2] + pad * 4, r[3] + pad * 2),
                         "smallprint")
                    break

    # No-heading fallback: densest small-text band (back/side panels carry
    # the ingredient paragraph even when the heading itself misread).
    # Front principal displays are excluded — ingredients don't belong there.
    if "ingredients" not in by_kind and label != "front":
        band = food_mod.find_dense_text_band(stage_lines, stage_size)
        if band is not None:
            regions.append({"kind": "ingredients-scan",
                            "rect": band["rect"], "prep": "ingredients",
                            "reason": band.get("reason", "")})
            by_kind.add("ingredients-scan")
    return regions[:REGION_CALL_BUDGET]


def _merge_layout_regions(
        regions: list[dict[str, Any]],
        layout: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Fold layout-map evidence into Stage-2 proposals (Part I).

    - A high-confidence layout INGREDIENTS panel *tightens* (never
      widens) the proposed ingredient x-range: a narrower column wins
      over the full-width fallback band.
    - A confident NUTRITION panel (heading anchor and/or table
      structure) is appended as one targeted small-print region so the
      nutrition table gets its own OCR pass instead of sharing the
      pooled full-page text.
    Everything stays inside REGION_CALL_BUDGET via the caller's slice;
    the merge itself adds at most one region and never invents text.
    """
    if not layout:
        return regions
    out = list(regions)
    panels = {r.get("kind"): r for r in (layout.get("regions") or [])
              if isinstance(r, dict)}
    for region in out:
        if region.get("kind") not in ("ingredients", "ingredients-scan"):
            continue
        panel = panels.get("INGREDIENTS")
        if panel is None or (panel.get("confidence") or 0) < 0.6:
            continue
        try:
            px0, _, px1, _ = panel["rect"]
            rx0, ry0, rx1, ry1 = region["rect"]
            # Tighten only: intersect, keep at least 40% of the band.
            nx0, nx1 = max(rx0, px0), min(rx1, px1)
            if nx1 - nx0 >= 0.4 * max(rx1 - rx0, 1.0) and nx1 > nx0:
                region["rect"] = (nx0, ry0, nx1, ry1)
                region["layout_refined"] = {
                    "panel_confidence": panel.get("confidence"),
                    "reason": panel.get("reason", "")}
        except (TypeError, IndexError, KeyError):
            continue
    nutri = panels.get("NUTRITION")
    if nutri is not None and (nutri.get("confidence") or 0) >= 0.5:
        if not any(r.get("kind") == "nutrition" for r in out):
            try:
                out.append({"kind": "nutrition", "rect": tuple(
                    float(v) for v in nutri["rect"]),
                    "prep": "smallprint",
                    "reason": nutri.get("reason", ""),
                    "layout_confidence": nutri.get("confidence")})
            except (TypeError, ValueError):
                pass
    # Honor REGION_CALL_BUDGET: core evidence (ingredient + nutrition
    # regions) keeps its slots; overflow drops from the tail, and the
    # per-image targeted gate + dedupe remain the final backstop.
    if len(out) > REGION_CALL_BUDGET:
        core = [r for r in out if r.get("kind") in (
            "ingredients", "ingredients-scan", "nutrition")]
        rest = [r for r in out if r.get("kind") not in (
            "ingredients", "ingredients-scan", "nutrition")]
        out = (core + rest)[:REGION_CALL_BUDGET]
    return out


def choose_fallback_rotation(stage_lines: list[OcrLine],
                             cv_orientation: dict[str, Any] | None
                             ) -> int:
    """Evidence-chosen single rotation fallback angle (Part E).

    Returns 90 (sideways evidence: transposed geometry), 180
    (upside-down evidence: horizontal pixel structure yet almost no
    usable OCR lines), or 0 (no fallback — keep the existing gate).
    At most ONE rotated full-page pass ever runs; this only *chooses
    its direction*. 0-vs-180 can never be told apart from pixels
    alone, so 180 fires solely when recognition catastrophically
    failed on a strongly horizontal frame.
    """
    cv_orientation = cv_orientation or {}
    usable = 0
    for ln in stage_lines or []:
        rect = _box_rect(getattr(ln, "box", None))
        if rect is None:
            continue
        if rect[2] - rect[0] > 0 and rect[3] - rect[1] > 0:
            usable += 1
    if usable > 5:
        return 0
    # Upside-down evidence: strong horizontal pixel structure yet
    # almost no usable OCR lines. (0-vs-180 is pixel-ambiguous, so
    # this fires only on catastrophic recognition failure.)
    if usable <= 2 and cv_orientation.get("decisive") is True and \
            not cv_orientation.get("transpose"):
        return 180
    # Legacy default: sideways photos get the 90-degree pass.
    return 90


def _x_overlap(rect: tuple[float, float, float, float],
                span: tuple[float, float], minimum: float = 0.3) -> bool:
    """Whether a rect horizontally overlaps an x-span by >= minimum."""
    x0, _, x1, _ = rect
    s0, s1 = span
    width = max(x1 - x0, 1e-6)
    inter = max(0.0, min(x1, s1) - max(x0, s0))
    return (inter / width) >= minimum


def _select_body_column(heading_rect: tuple[float, float, float, float],
                        columns: list[dict[str, Any]],
                        shims: list[Any],
                        food_mod: Any,
                        frame_h: float) -> dict[str, Any] | None:
    """Pick the heading's text column for the ingredient body.

    The column containing the heading x-center wins; when the heading
    sits close to a column boundary, every adjacent column is scored by
    vertical continuity below the heading (non-section-head lines
    overlapping it) and the strongest wins. Returns None when no
    columns resolve (caller falls back to the legacy full-width band).
    """
    if not columns:
        return None
    hx0, _, hx1, hy1 = heading_rect
    hxc = (hx0 + hx1) / 2.0
    col_w = max(hx1 - hx0, 1.0)

    def _contains(col: dict[str, Any]) -> bool:
        return col["x0"] <= hxc <= col["x1"]

    def _continuity(col: dict[str, Any]) -> int:
        n = 0
        for s in shims:
            r = _box_rect(s.box)
            if r is None or r[1] <= hy1:
                continue
            if _x_overlap(r, (col["x0"], col["x1"]), 0.4):
                if not food_mod._is_section_head(s.text or ""):
                    n += 1
        return n

    inside = [c for c in columns if _contains(c)]
    if len(inside) == 1:
        edge_tol = max(col_w * 0.25, 8.0)
        col = inside[0]
        near_edge = min(abs(hxc - col["x0"]), abs(col["x1"] - hxc))
        if near_edge > edge_tol or len(columns) == 1:
            return col
        # Boundary case: score neighbours by vertical continuity.
        contenders = [col] + [c for c in columns if c is not col]
        scored = [(_continuity(c), -abs((c["x0"] + c["x1"]) / 2.0 - hxc), c)
                  for c in contenders]
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return scored[0][2]
    if inside:
        # Overlapping columns (nested clusters): most continuity wins.
        return max(inside, key=_continuity)
    # Heading center in a gutter: nearest column by edge distance with
    # any continuity wins, else nearest overall.
    scored = []
    for c in columns:
        edge = 0.0 if c["x0"] <= hxc <= c["x1"] else min(
            abs(hxc - c["x0"]), abs(hxc - c["x1"]))
        scored.append((_continuity(c), -edge, c))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    if scored and (scored[0][0] > 0 or len(columns) == 1):
        return scored[0][2]
    return None


def _heading_remainder(text: str) -> str:
    """Post-heading text sharing the heading line ("INGREDIENTS: Refined
    Wheat Flour ...") — preserved because the crop starts below the row."""
    import re as _re

    m = _re.search(r":", text)
    if m:
        return text[m.end():].strip(" .")
    return _re.sub(r"(?i)^\s*(ingredients?|composition|contents|contains|"
                   r"\u0938\u093e\u092e\u0917\u094d\u0930\u0940|samagri\w*)"
                   r"\s*[.:/]*", "", text).strip()


def _lowconf_bands(stage_lines: list[OcrLine],
                   stage_size: tuple[int, int],
                   max_bands: int = 2) -> list[dict[str, Any]]:
    """Full-resolution bands over Stage-1 lines read with low confidence.

    Tiny print is often *detected* (boxes exist) but misread at Stage-1
    scale. Re-OCR of just those bands at full resolution recovers the
    glyphs at a fraction of a full-frame pass. Fires only when a required
    field (quantity/MRP/manufacturing date) is still unresolved.
    """
    weak = [(ln, _box_rect(ln.box)) for ln in stage_lines
            if (ln.confidence or 0) < 0.75 and _box_rect(ln.box) is not None]
    if not weak:
        return []
    weak.sort(key=lambda item: item[1][1])  # type: ignore[index]
    w, _ = stage_size
    bands: list[list[float]] = []
    for _, rect in weak:
        assert rect is not None
        top, bottom = rect[1], rect[3]
        height = max(bottom - top, 1.0)
        pad = height * 1.5 + 4
        placed = False
        for band in bands:
            if top - pad <= band[1] and bottom + pad >= band[0]:
                band[0] = min(band[0], top - pad)
                band[1] = max(band[1], bottom + pad)
                placed = True
                break
        if not placed:
            bands.append([top - pad, bottom + pad])
        if len(bands) >= max_bands * 2:
            break
    # Keep the tallest bands (most text), capped.
    bands.sort(key=lambda b: b[1] - b[0], reverse=True)
    _, h = stage_size
    return [{"kind": "lowconf-band",
             "rect": (0, max(0, b[0]), w, min(h, b[1])),
             "prep": "smallprint"} for b in bands[:max_bands]]


def _prep_region(crop: Any, prep: str) -> Any:
    """Light, punctuation-safe preprocessing for a targeted crop.

    Ingredients: grayscale + moderate upscale, no harsh binarisation
    (commas, parentheses, percentages and INS numbers must survive).
    Small print (MRP/dates/FSSAI): stronger upscale + contrast.
    """
    from PIL import ImageEnhance, ImageOps

    if prep == "ingredients":
        crop = _upscale_capped(crop, 1.6, 1800).convert("L")
        crop = ImageOps.autocontrast(crop, cutoff=1)
        crop = ImageEnhance.Sharpness(crop).enhance(1.1)
    else:
        crop = _upscale_capped(crop, 2.0, 1800).convert("L")
        crop = ImageOps.autocontrast(crop, cutoff=2)
        crop = ImageEnhance.Contrast(crop).enhance(1.5)
        crop = ImageEnhance.Sharpness(crop).enhance(1.3)
    return _to_array(crop.convert("RGB"))


def _otsu_threshold(gray: Any) -> Any:
    """Otsu binarisation via numpy histogram (no cv2 dependency)."""
    import numpy as np

    arr = np.asarray(gray, dtype=np.uint8)
    hist = np.bincount(arr.ravel(), minlength=256).astype(float)
    total = arr.size
    sum_all = float((hist * np.arange(256)).sum())
    sum_b = w_b = 0.0
    best, thresh = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        between = (sum_all * w_b - sum_b) ** 2 / (w_b * w_f)
        if between > best:
            best, thresh = between, t
    return (arr > thresh) * 255


def _prep_ingredient_variant(crop: Any, variant: str) -> Any:
    """One of the bounded ingredient OCR variants (crops only, never the
    full frame): original, upscaled grayscale+contrast, Otsu-binarised,
    sharpened. Returns an RGB numpy array for the provider."""
    from PIL import ImageEnhance, ImageOps

    from PIL import Image as _Image

    crop = crop.convert("RGB")
    if max(crop.size) > 1800:
        crop = _fit(crop, 1800)
    if variant == "orig":
        return _to_array(crop)
    gray = _upscale_capped(crop, 1.6, 1800).convert("L")
    if variant == "up":
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = ImageEnhance.Contrast(gray).enhance(1.4)
    elif variant == "thresh":
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = _Image.fromarray(_otsu_threshold(gray).astype("uint8"))
    elif variant == "sharp":
        gray = ImageOps.autocontrast(gray, cutoff=1)
        gray = ImageEnhance.Sharpness(gray).enhance(2.0)
    else:  # pragma: no cover - defensive
        raise OcrError(f"unknown ingredient variant: {variant}")
    return _to_array(gray.convert("RGB"))


def _crop_ingredient_band(original: Any,
                            rect_stage: tuple[float, float, float, float],
                            scale_xy: tuple[float, float]) -> Any | None:
    """Crop the ingredient band at full resolution. None when degenerate."""
    orig_w, orig_h = original.size
    sx, sy = scale_xy
    x0, y0, x1, y1 = rect_stage
    crop = original.crop((
        max(0, int(x0 * sx)), max(0, int(y0 * sy)),
        min(orig_w, int(x1 * sx)), min(orig_h, int(y1 * sy))))
    if crop.size[0] < 8 or crop.size[1] < 8:
        return None
    return crop


def _prepare_crop_for_ocr(crop: Any, purpose: str,
                          quality: dict[str, Any] | None = None
                          ) -> tuple[Any, dict[str, Any]]:
    """OpenCV crop conditioning before variant rendering (Part F/G).

    Applies conservative deskew plus optional inner-quad rectification
    to the PIL crop. Pure preprocessing — costs zero OCR calls. Returns
    (crop, info) with applied flags for diagnostics; never raises.
    """
    info: dict[str, Any] = {"deskewed": False, "deskew_angle": 0.0,
                            "perspective_corrected": False, "notes": []}
    try:
        from app.services.ocr import opencv_preprocessor as ocv

        import numpy as np

        arr = np.asarray(crop.convert("L"))
        straight, applied, angle, notes = ocv.deskew_image(arr)
        info["notes"].extend(notes)
        if applied:
            from PIL import Image as _Image

            crop = _Image.fromarray(straight).convert("RGB")
            info["deskewed"] = True
            info["deskew_angle"] = angle
        crop, corrected, rect_info = ocv.rectify_crop(crop)
        info["notes"].extend(rect_info.get("notes", []))
        if corrected:
            info["perspective_corrected"] = True
    except Exception as exc:
        info["notes"].append(f"crop conditioning skipped: {exc}")
    return crop, info


def _run_ingredient_variants(provider: Any, original: Any,
                             rect_stage: tuple[float, float, float, float],
                             scale_xy: tuple[float, float],
                             label: str, index: int,
                             errors: list[str],
                             call_log: list[dict[str, Any]] | None = None,
                             variant_order: tuple[str, ...] | None = None,
                             quality: dict[str, Any] | None = None,
                             ) -> tuple[list[OcrLine], list[dict[str, Any]],
                                        dict[str, Any],
                                        list[tuple[str, str, float]]]:
    """OCR the ingredient band with up to INGREDIENT_VARIANT_BUDGET crops.

    Each variant is reconstructed and coherence-scored on its own lines;
    the loop aborts once a variant reads coherently. Only the winning
    variant's lines are returned (losing variants would duplicate garbage
    into the pooled text). ``variant_order`` (default INGREDIENT_VARIANTS)
    only reorders which bounded variants run first — the budget never
    grows. Returns (lines, variant_timings, best_score, variant_texts)
    where variant_texts holds (name, text, coherence) per ran variant
    for disagreement detection. Every provider call is logged to
    ``call_log`` with its crop rect, size, variant and duration.
    """
    from app.services.ocr import food as food_mod
    from app.services.ocr import opencv_preprocessor as ocv

    crop = _crop_ingredient_band(original, rect_stage, scale_xy)
    if crop is None:
        return [], [], {"score": 0.0, "reasons": ["no variant ran"]}, []
    crop_conditioning: dict[str, Any] = {"deskewed": False,
                                         "perspective_corrected": False}
    try:
        crop, _cond = _prepare_crop_for_ocr(crop, "ingredients", quality)
        crop_conditioning.update({
            "deskewed": bool(_cond.get("deskewed")),
            "perspective_corrected": bool(
                _cond.get("perspective_corrected"))})
    except Exception:
        pass
    best_lines: list[OcrLine] = []
    best_score: dict[str, Any] = {"score": 0.0, "reasons": ["no variant ran"]}
    variant_timings: list[dict[str, Any]] = []
    variant_texts: list[tuple[str, str, float]] = []
    order = list(variant_order or INGREDIENT_VARIANTS)
    for num, vname in enumerate(order, start=1):
        if num > INGREDIENT_VARIANT_BUDGET:
            break
        side = f"{label}:region:ingredients:v{num}-{vname}"
        t0 = time.perf_counter()
        try:
            arr = ocv.prepare_variant(crop, vname)
        except OcrError as exc:
            variant_timings.append({"kind": f"ingredients:v{num}-{vname}",
                                    "ms": 0.0, "lines": 0,
                                    "coherence": 0.0,
                                    "error": str(exc)})
            continue
        vlines = _tag(_call_provider(
            provider, arr, side, errors, call_log,
            {"image_id": label, "stage": "targeted-ingredients",
             "purpose": "ingredients",
             "crop_rect": [round(v, 1) for v in rect_stage],
             "preprocessing_variant": vname,
             "reason": f"ingredient crop variant {vname}"}),
            label, index, f"ingredients:v{num}-{vname}")
        text, _notes = food_mod.reconstruct_ingredient_text(vlines)
        confs = [ln.confidence for ln in vlines]
        scored = food_mod.score_ingredient_coherence(text, confs)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        variant_timings.append({"kind": f"ingredients:v{num}-{vname}",
                                "ms": ms, "lines": len(vlines),
                                "coherence": scored["score"]})
        variant_texts.append((vname, text, scored["score"]))
        if scored["score"] > best_score["score"]:
            best_score = scored
            best_lines = vlines
        if scored["score"] >= INGREDIENT_COHERENT_ABORT:
            break  # coherent read: further variants would only cost time
    if variant_timings:
        variant_timings[0]["deskewed"] = crop_conditioning.get(
            "deskewed", False)
        variant_timings[0]["perspective_corrected"] = crop_conditioning.get(
            "perspective_corrected", False)
    return best_lines, variant_timings, best_score, variant_texts


# ------------------------------------------------- hybrid fallback ---
def _mean_or_none(confs: list[float]) -> float | None:
    vals = [c for c in confs if c]
    return round(sum(vals) / len(vals), 3) if vals else None


def _median_line_height(stage_lines: list[OcrLine]) -> float | None:
    """Median Stage-1 box height: the local text-row scale estimate."""
    heights: list[float] = []
    for ln in stage_lines or []:
        rect = _box_rect(getattr(ln, "box", None))
        if rect is not None and rect[3] > rect[1]:
            heights.append(rect[3] - rect[1])
    if not heights:
        return None
    heights.sort()
    mid = len(heights) // 2
    if len(heights) % 2:
        return heights[mid]
    return (heights[mid - 1] + heights[mid]) / 2.0


def _maybe_tesseract_fallback(tess_provider: Any, original: Any,
                              region: dict[str, Any],
                              scale_xy: tuple[float, float],
                              label: str, index: int,
                              errors: list[str],
                              rapid_lines: list[OcrLine],
                              variant_texts: list[tuple[str, str, float]],
                              stage_lines: list[OcrLine],
                              call_log: list[dict[str, Any]] | None = None,
                              ) -> tuple[list[OcrLine], dict[str, Any]]:
    """Optionally reconcile RapidOCR ingredient lines with Tesseract.

    Never raises: unavailability, inference failure, and empty output all
    degrade to RapidOCR-only behaviour with the reason recorded. At most
    ONE Tesseract call per ingredient region (PSM 6, plain capped crop).
    Returns (final_lines, fallback_info).
    """
    from app.services.ocr import food as food_mod
    from app.services.ocr import ingredient_hybrid as hybrid_mod

    info: dict[str, Any] = {"triggered": False, "reasons": [],
                            "ocr_source": "rapidocr", "uncertain": False,
                            "notes": [], "tess_ms": 0.0,
                            "reconcile_ms": 0.0}
    if tess_provider is None:
        info["reasons"] = ["no tesseract provider configured"]
        return rapid_lines, info
    rapid_text, _ = food_mod.reconstruct_ingredient_text(rapid_lines)
    rapid_confs = [float(getattr(ln, "confidence", 0) or 0)
                   for ln in rapid_lines]
    rapid_coh = food_mod.score_ingredient_coherence(rapid_text, rapid_confs)
    rect = region.get("rect") or (0, 0, 0, 0)
    line_h = _median_line_height(stage_lines)
    fire, reasons = hybrid_mod.needs_tesseract_fallback(
        rapid_text=rapid_text, rapid_coherence=rapid_coh,
        region_height_px=float(rect[3] - rect[1]) if rect else None,
        line_height_px=line_h, variant_texts=variant_texts)
    info["reasons"] = reasons
    if not fire:
        info["notes"] = ["rapid result accepted without fallback"]
        return rapid_lines, info
    info["triggered"] = True
    available = getattr(tess_provider, "available", None)
    if callable(available) and not available():
        info["notes"] = ["tesseract unavailable; rapid-only behaviour"]
        return rapid_lines, info
    crop = _crop_ingredient_band(original, region.get("rect"), scale_xy)
    if crop is None:
        info["notes"] = ["empty ingredient crop; rapid-only behaviour"]
        return rapid_lines, info
    side = f"{label}:region:ingredients:tesseract"
    t0 = time.perf_counter()
    try:
        arr = _prep_ingredient_variant(crop, "orig")
    except OcrError as exc:
        info["notes"] = [f"ingredient crop prep failed: {exc}"]
        return rapid_lines, info
    psm = int(hybrid_mod.TESS_FALLBACK_CONFIG.get("psm", 6))
    try:
        try:
            tess_out = tess_provider.extract(arr, side, psm=psm)
        except TypeError:
            tess_out = tess_provider.extract(arr, side)
    except OcrError as exc:
        info["notes"] = [f"tesseract failed ({exc}); rapid-only behaviour"]
        if call_log is not None:
            call_log.append({
                "image_id": label, "stage": "tesseract-fallback",
                "purpose": "ingredients",
                "provider": getattr(tess_provider, "name", "tesseract"),
                "crop_rect": [round(v, 1) for v in region.get("rect")],
                "crop_size": list(getattr(arr, "shape", [0, 0])[:2][::-1]),
                "preprocessing_variant": "orig",
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                "reason": f"tesseract failed: {exc}", "lines": 0})
        return rapid_lines, info
    except Exception as exc:  # never let fallback break the pipeline
        info["notes"] = [f"tesseract errored ({exc}); rapid-only behaviour"]
        return rapid_lines, info
    tess_ms = round((time.perf_counter() - t0) * 1000, 1)
    info["tess_ms"] = tess_ms
    if call_log is not None:
        try:
            _shape = getattr(arr, "shape", None)
            _size = [int(_shape[1]), int(_shape[0])] if _shape else None
        except Exception:
            _size = None
        call_log.append({
            "image_id": label, "stage": "tesseract-fallback",
            "purpose": "ingredients",
            "provider": getattr(tess_provider, "name", "tesseract"),
            "crop_rect": [round(v, 1) for v in region.get("rect")],
            "crop_size": _size,
            "preprocessing_variant": "orig",
            "duration_ms": tess_ms,
            "reason": f"ingredient gate fired: {reasons}",
            "lines": len(list(tess_out.lines or []))})
    tess_lines = _tag(list(tess_out.lines or []), label, index,
                      "ingredients:tesseract")
    tess_text, _ = food_mod.reconstruct_ingredient_text(tess_lines)
    tess_confs = [float(getattr(ln, "confidence", 0) or 0)
                  for ln in tess_lines]
    t_rec = time.perf_counter()
    rec = hybrid_mod.reconcile_ingredient_texts(
        rapid_text, tess_text, rapid_conf=(_mean_or_none(rapid_confs)),
        tess_conf=(_mean_or_none(tess_confs)))
    info["reconcile_ms"] = round((time.perf_counter() - t_rec) * 1000, 1)
    info["ocr_source"] = rec["ocr_source"]
    info["uncertain"] = rec["uncertain"]
    info["notes"].extend(rec["notes"])
    info["tess_entry"] = {"kind": "ingredients:tesseract",
                          "engine": "tesseract", "ms": tess_ms,
                          "lines": len(tess_lines),
                          "trigger": reasons}
    if rec["ocr_source"] == "rapidocr":
        return rapid_lines, info
    if rec["ocr_source"] == "tesseract":
        return tess_lines, info
    # Hybrid merge: one synthetic line carrying the reconciled text. The
    # confidence reflects the primary (RapidOCR) evidence quality; the box
    # is intentionally None (merged text spans the whole crop, not a box).
    mean_conf = (round(sum(rapid_confs) / len(rapid_confs), 3)
                 if rapid_confs else 0.0)
    merged = OcrLine(text=rec["text"], confidence=mean_conf, box=None,
                     image=label)
    _tag([merged], label, index, "ingredients:hybrid")
    return [merged], info


# ------------------------------------------------- cross-image reconcile ---
def _norm_candidate(key: str, value: Any, unit: Any = None) -> Any:
    """Normalised comparison form: '50' == '50.00', '70 g' == '70.0 g'."""
    text = str(value or "").strip().replace(",", "")
    if key == "mrp":
        try:
            return round(float(text), 2)
        except ValueError:
            return text.lower()
    if key == "quantity":
        try:
            num = float(text)
        except ValueError:
            return (text.lower(), str(unit or "").lower())
        return (num, str(unit or "").lower())
    return text.lower()


def _reconcile(key: str, per_image: list[dict[str, Any]],
               role_rank: dict[str, int] | None = None) -> dict[str, Any]:
    """Merge one field's per-image hits.

    Agreement (or a single source) → best confidence wins with all sources
    retained. Conflict → NEEDS_REVIEW with every candidate preserved, so an
    officer — never a heuristic — breaks the tie. ``role_rank`` maps an
    image label to its field-priority rank (lower wins) and breaks only
    exact ties of the legacy key — reported values and confidences are
    never synthesized.
    """
    if not per_image:
        return {"value": None, "confidence": None, "status": "NOT_DETECTED",
                "sources": [], "candidates": []}
    first = _norm_candidate(
        key, per_image[0]["value"], per_image[0].get("unit"))
    if all(_norm_candidate(key, h["value"], h.get("unit")) == first
           for h in per_image):
        # Rank by cross-image agreement, then OCR confidence, then
        # front-panel and box evidence. The REPORTED confidence stays
        # the winner's measured OCR confidence (never synthesized);
        # ranking only decides which agreed candidate leads.
        _rr = role_rank or {}

        def _rank_key(h: dict[str, Any]) -> tuple:
            agree = sum(
                1 for o in per_image
                if _norm_candidate(key, o["value"], o.get("unit"))
                == _norm_candidate(key, h["value"], h.get("unit")))
            return (agree, h.get("confidence") or 0,
                    h.get("image") == "front", h.get("box") is not None,
                    -_rr.get(h.get("image"), 99))

        ordered = sorted(per_image, key=_rank_key, reverse=True)
        best = ordered[0]
        conf = best.get("confidence")
        return {
            "value": best["value"], "confidence": conf,
            "status": ("DETECTED" if conf is None
                       or conf >= STATUS_CONF_THRESHOLD else "NEEDS_REVIEW"),
            "sources": [
                {"image": h.get("image"), "image_index": h.get("image_index"),
                 "confidence": h.get("confidence"), "box": h.get("box"),
                 "value": h.get("value")} for h in ordered],
            "candidates": [],
            "image": best.get("image"), "image_index": best.get("image_index"),
            "box": best.get("box"),
        }
    ordered = sorted(per_image, key=lambda h: (h.get("confidence") or 0),
                     reverse=True)
    return {
        "value": None, "confidence": ordered[0].get("confidence"),
        "status": "NEEDS_REVIEW",
        "sources": [
            {"image": h.get("image"), "image_index": h.get("image_index"),
             "confidence": h.get("confidence"), "box": h.get("box"),
             "value": h.get("value"),
             "unit": h.get("unit")} for h in ordered],
        "candidates": [
            {"image": h.get("image"), "confidence": h.get("confidence"),
             "value": h.get("value"),
             "unit": h.get("unit")} for h in ordered],
        "image": ordered[0].get("image"),
        "image_index": ordered[0].get("image_index"),
        "box": ordered[0].get("box"),
    }


_RECONCILED_KEYS = ("quantity", "mrp", "manufacturing_date", "best_before",
                    "use_by", "fssai_license", "batch_lot", "consumer_care")


def _hit_anchored(key: str, hit: dict[str, Any],
                    per_image_lines: list[list[OcrLine]]) -> bool | None:
    """Whether a per-image hit carries contextual anchor support.

    A bare quantity ("8 g" inside a nutrition table) or a bare-Rs fragment
    must never outvote — or even tie — an anchored declaration ("NET WT
    70 g", "MRP Rs. 50"). Returns None when the source line cannot be
    located (treated as participating, i.e. legacy behaviour).
    """
    from app.services.ocr import fields as fields_mod

    # Map hit image label -> position in per_image_lines via image_index.
    label = hit.get("image")
    idx = hit.get("image_index")
    line_text: str | None = None
    try:
        for lines in per_image_lines:
            if lines and getattr(lines[0], "image", None) == label:
                line_text = getattr(lines[int(idx)], "text", "")
                neighbours = [getattr(lines[j], "text", "")
                              for j in (int(idx) - 2, int(idx) - 1,
                                        int(idx) + 1, int(idx) + 2)
                              if 0 <= j < len(lines)]
                break
        else:
            return None
    except (TypeError, ValueError, IndexError):
        return None
    if line_text is None:
        return None
    if key == "quantity":
        # Spaceless-tolerant: OCR merges print ("NETWT70g"), defeating
        # \b word boundaries, so fall back to substring matching.
        import re as _re

        def _qty_anchored(text: str | None) -> bool:
            if not text:
                return False
            if fields_mod._QTY_CTX.search(text):
                return True
            return bool(_re.search(
                r"net|qty|quantity|weight|contents?", text, _re.I))
        if _qty_anchored(line_text):
            return True
        return any(_qty_anchored(n) for n in neighbours)
    if key == "mrp":
        # MRP-word anchored beats a bare-Rs fragment elsewhere.
        if fields_mod._MRP_CTX.search(line_text or ""):
            return True
        return any(fields_mod._MRP_CTX.search(n or "")
                   for n in neighbours)
    return True


def _reconcile_fields(per_image_detailed: list[dict[str, Any]],
                      pooled: dict[str, dict[str, Any]],
                      per_image_lines: list[list[OcrLine]] | None = None
                      ) -> dict[str, Any]:
    """Overlay cross-image reconciliation onto the pooled extraction."""
    from app.services.ocr import image_roles as _roles_mod

    out = {k: dict(v) for k, v in pooled.items()}
    # Stage 3C: role-aware tie-breaks. Roles come from Stage-1 lines
    # only (region re-OCR lines must not sway panel classification).
    _role_ranks: dict[str, dict[str, int]] = {}
    try:
        _labels = sorted({str(getattr(ln, "image", "") or "")
                          for lines in (per_image_lines or [])
                          for ln in (lines or []) if
                          getattr(ln, "image", None)})
        _stage1 = {
            _lab: [ln for lines in (per_image_lines or [])
                   for ln in (lines or [])
                   if getattr(ln, "image", None) == _lab
                   and getattr(ln, "variant", "stage1") == "stage1"]
            for _lab in _labels}
        _rmap = _roles_mod.classify_roles(_labels, _stage1)
        for _key in _RECONCILED_KEYS:
            _order = _roles_mod.ordered_labels_for_field(
                _key, _labels,
                {lab: (_rmap.get(lab) or "UNKNOWN") for lab in _labels})
            _role_ranks[_key] = {lab: i for i, lab in enumerate(_order)}
    except Exception:
        _role_ranks = {}
    for key in _RECONCILED_KEYS:
        hits = []
        for img_det in per_image_detailed:
            hit = img_det.get(key) or {}
            if hit.get("value") not in (None, ""):
                entry = dict(hit)
                if key == "quantity":
                    entry["unit"] = (img_det.get("unit") or {}).get("value")
                hits.append(entry)
        # Anchor-gated voting (quantity/MRP only): unanchored fragments
        # (nutrition "8 g", stray "Rs." bits) never veto an anchored
        # declaration. With no anchored hit anywhere, every hit votes
        # (legacy behaviour, e.g. two bare "70 g" photos agreeing).
        if key in ("quantity", "mrp") and per_image_lines is not None:
            flags = [_hit_anchored(key, h, per_image_lines) for h in hits]
            authoritative = [h for h, flag in zip(hits, flags) if flag]
            if authoritative:
                hits = authoritative
        if not hits:
            out[key] = {**out[key], "sources": [], "candidates": []}
            continue
        if len(hits) == 1:
            solo = hits[0]
            out[key] = {**out[key], "confidence": solo.get("confidence"),
                        "image": solo.get("image"),
                        "image_index": solo.get("image_index"),
                        "box": solo.get("box"),
                        "sources": [{
                            "image": solo.get("image"),
                            "image_index": solo.get("image_index"),
                            "confidence": solo.get("confidence"),
                            "box": solo.get("box"),
                            "value": solo.get("value")}],
                        "candidates": []}
            if key == "quantity":
                out["unit"] = {**out["unit"],
                               "confidence": solo.get("confidence"),
                               "image": solo.get("image"),
                               "image_index": solo.get("image_index"),
                               "box": solo.get("box")}
            continue
        merged = _reconcile(key, hits, _role_ranks.get(key))
        out[key] = {**out[key], **merged}
        if key == "quantity":
            if merged["value"] is None:
                out["unit"] = {**out["unit"], "value": None,
                               "confidence": merged["confidence"]}
            else:
                out["unit"] = {**out["unit"],
                               "confidence": merged["confidence"],
                               "image": merged.get("image"),
                               "image_index": merged.get("image_index"),
                               "box": merged.get("box")}
    for key in FIELD_KEYS:
        out[key].setdefault("sources", [])
        out[key].setdefault("candidates", [])
    return out


# --------------------------------------------------------------- pipeline ---
def extract_label_multi(images: list[tuple[bytes | None, str]] | None,
                        provider: Any | None = None,
                        tess_provider: Any | None = None,
                        targeted_stages: bool = True) -> dict[str, Any]:
    """Run staged OCR over N package images and combine evidence.

    ``tess_provider`` is opt-in: when supplied (or enabled via
    :func:`configure_tesseract_fallback`), weak RapidOCR ingredient reads
    are reconciled against one targeted Tesseract crop call (ingredient
    regions only). Otherwise pure-RapidOCR behaviour is preserved.

    ``targeted_stages=False`` is a legacy/benchmark mode: Stage-1
    full-page OCR plus deterministic field assembly only (no targeted
    region crops, low-confidence band, rotation fallback, or Tesseract).
    Decode, duplicate reuse, normalization, food/veg layers and budgets
    stay identical, so ``stage3c_benchmark.py`` can report a genuine
    generic-vs-field-aware delta on the same fixtures.
    """
    from app.services.ocr.rapidocr_provider import RapidOCRProvider

    t_start = time.perf_counter()
    provider = provider or RapidOCRProvider()
    tess_provider = _resolve_tess_provider(tess_provider)
    supplied = [(raw, label or f"image_{i}")
                for i, (raw, label) in enumerate(images or []) if raw]
    if len(supplied) > MAX_IMAGES:
        supplied = supplied[:MAX_IMAGES]

    image_results: list[dict[str, Any]] = []
    all_lines: list[OcrLine] = []
    per_image_lines: list[list[OcrLine]] = []
    ingredient_region_lines: list[OcrLine] = []
    ingredient_seeds: list[str] = []
    ingredient_fallbacks: list[dict[str, Any]] = []
    rejected_lines: list[dict[str, Any]] = []
    # Heading-column spans per image, in line-box (stage) coordinates,
    # for full-page assembly filtering. Transposed frames are excluded:
    # their columns do not map to a stage x-range (crop-level filtering
    # already covers them).
    image_columns: dict[str, tuple[float, float]] = {}
    errors: list[str] = []
    timings: dict[str, Any] = {"images": {}}
    # Stage-1B structured provider-call log: one entry per invocation.
    call_log: list[dict[str, Any]] = []
    # Per-image Stage-1 lines + geometry for the diagnostics artifact.
    diag_stage: dict[str, Any] = {}
    # Stage 3A.5: completed images for near-duplicate reuse. Maps label
    # -> {dhash, my_lines, serial-ready data, timings, diag}. A duplicate
    # is retained as evidence but skips every provider call.
    completed: dict[str, dict[str, Any]] = {}

    # NOTE (Stage 3A/3B performance): images are processed sequentially
    # on purpose. The RapidOCR engine is a shared class-level singleton
    # whose thread-safety is not guaranteed, and targeted crops reuse
    # per-image budgets that are simpler to enforce in one pass.
    # Parallelism is deliberately NOT introduced; speed comes from hard
    # call budgets, duplicate reuse, and early abort on coherent reads.
    for index, (raw, label) in enumerate(supplied):
        t_img = time.perf_counter()
        img_timings: dict[str, Any] = {"regions": []}
        # Targeted provider calls consumed by this image (ingredient
        # variants + region crops + low-conf bands share ONE budget;
        # Stage-1 full-page and the gated rotation/tesseract calls sit
        # outside it with their own caps).
        targeted_used = 0
        # Rects already OCR'd for this image: never run multiple
        # equivalent crops merely for preprocessing variety.
        seen_rects: list[tuple[float, ...]] = []
        if len(raw) > MAX_IMAGE_BYTES:
            errors.append(f"{label} image exceeds 10 MB")
            image_results.append({"image": label, "image_index": index,
                                  "text": "", "lines": [], "variants": [],
                                  "error": f"{label} image exceeds 10 MB",
                                  "skipped": False})
            per_image_lines.append([])
            continue
        try:
            _t_decode = time.perf_counter()
            original = _decode(raw)
            img_timings["decode_ms"] = round(
                (time.perf_counter() - _t_decode) * 1000, 1)
        except OcrError as exc:
            log.warning("ocr %s preprocess failed: %s", label, exc)
            errors.append(str(exc))
            image_results.append({"image": label, "image_index": index,
                                  "text": "", "lines": [], "variants": [],
                                  "error": str(exc), "skipped": False})
            per_image_lines.append([])
            continue
        # Stage 3A.5 duplicate detection: perceptual hash of the decoded
        # frame. Exact/near duplicates reuse the earlier image's OCR
        # lines (retagging image/index) and skip every provider call.
        # All images stay in the inspection as evidence; pooled text is
        # not duplicated (only per-image evidence lists carry the copy).
        # Guard: reuse needs a hash match AND real ink on both frames
        # (or an exact match) — near-blank frames are cheap to OCR and
        # must never borrow another panel's text.
        dup_hash, dup_ink = _dhash_pil(original)
        dup_of: str | None = None
        dup_dist: int | None = None
        if dup_hash is not None:
            for _plab, _prec in completed.items():
                if _prec.get("dhash") is None:
                    continue
                _dist = _hamming(dup_hash, _prec["dhash"])
                _ink_ok = (_dist == 0) or (
                    dup_ink >= DHASH_MIN_INK
                    and (_prec.get("ink") or 0.0) >= DHASH_MIN_INK)
                if _dist <= DHASH_THRESHOLD and _ink_ok and (
                        dup_dist is None or _dist < dup_dist):
                    dup_of, dup_dist = _plab, _dist
        if dup_of is not None:
            _src = completed[dup_of]
            my_lines = []
            for _ln in _src.get("my_lines", []):
                _cp = OcrLine(text=_ln.text, confidence=_ln.confidence,
                              box=_ln.box, image=label)
                object.__setattr__(
                    _cp, "variant", getattr(_ln, "variant", "stage1"))
                object.__setattr__(_cp, "image_index", index)
                my_lines.append(_cp)
            per_image_lines.append(my_lines)
            serial = [{"text": ln.text, "confidence": ln.confidence,
                       "box": ln.box, "image": label, "image_index": index,
                       "variant": getattr(ln, "variant", "reused-ocr")}
                      for ln in my_lines]
            image_results.append({
                "image": label, "image_index": index,
                "text": "\n".join(ln.text for ln in my_lines),
                "lines": serial, "variants": ["reused-ocr"],
                "error": None, "skipped": False,
                "duplicate_of": dup_of, "ocr_reused_from": dup_of,
                "duplicate_distance": dup_dist})
            img_timings["image_ms"] = round(
                (time.perf_counter() - t_img) * 1000, 1)
            img_timings["stage1_ms"] = 0.0
            img_timings["stage1_lines"] = len(my_lines)
            img_timings["regions"] = []
            img_timings["targeted_used"] = 0
            img_timings["rotation_ms"] = 0.0
            img_timings["rotated"] = False
            img_timings["duplicate_of"] = dup_of
            img_timings["ocr_reused_from"] = dup_of
            img_timings["duplicate_distance"] = dup_dist
            for _k in ("image_quality", "image_quality_notes",
                       "cv_orientation", "orientation", "normalization"):
                if _k in (_src.get("img_timings") or {}):
                    img_timings[_k] = (_src["img_timings"][_k])
            timings["images"][label] = img_timings
            _src_diag = (_src.get("diag") or {})
            diag_stage[label] = {**_src_diag, "index": index,
                                 "reused_from": dup_of}
            log.info("ocr %s: near-duplicate of %s (dhash distance %s); "
                     "reused %d lines, 0 provider calls",
                     label, dup_of, dup_dist, len(my_lines))
            continue

        # OpenCV pre-pass (zero OCR calls): quality diagnostics drive
        # variant order + deskew/rectify decisions; the pixel orientation
        # vote informs the single rotation fallback below.
        from app.services.ocr import opencv_preprocessor as _ocv

        try:
            import numpy as _np

            _full_arr = _np.asarray(original)
        except Exception:
            _full_arr = None
        image_quality: dict[str, Any] = _ocv.analyze_quality(_full_arr) \
            if _full_arr is not None else {"cv2": False, "notes": [
                "pixel buffer unavailable; quality neutral"]}
        cv_orientation: dict[str, Any] = _ocv.estimate_orientation(
            _full_arr) if _full_arr is not None else {
            "transpose": False, "decisive": False, "notes": []}
        img_timings["image_quality"] = {
            k: (v.get("grade") if isinstance(v, dict) and "grade" in v
                else v)
            for k, v in image_quality.items() if k != "notes"}
        img_timings["image_quality_notes"] = image_quality.get("notes", [])
        img_timings["cv_orientation"] = {
            "transpose": cv_orientation.get("transpose"),
            "decisive": cv_orientation.get("decisive"),
            "ratio": cv_orientation.get("ratio")}
        # Stage 3A.1/3A.2 gated working-image normalization (zero OCR
        # calls): decisive transpose votes rotate the working frame
        # upright; helper-gated deskew applies in place; perspective
        # warps only poor-readability frames with a confident quad.
        # The upload bytes are never altered; low-confidence geometry
        # keeps prior behavior and is marked uncertain.
        _t_norm = time.perf_counter()
        working, norm_meta = _normalize_working_image(
            original, cv_orientation, image_quality)
        img_timings["normalization_ms"] = round(
            (time.perf_counter() - _t_norm) * 1000, 1)
        try:
            norm_meta["decoded_size"] = [original.size[0],
                                         original.size[1]]
        except Exception:
            pass
        img_timings["normalization"] = norm_meta
        norm_transpose = bool(
            norm_meta.get("orientation", {}).get("rotation_applied"))
        # Pipeline-consistent frame from here on (as EXIF transpose
        # already was): crops, stage geometry and boxes all live in the
        # working frame; the upload bytes stay pristine for evidence.
        original = working
        try:
            import numpy as _np2

            gray_small = _ocv._as_gray_small(
                _np2.asarray(working), max_dim=400)
        except Exception:
            gray_small = None
        orig_w, orig_h = original.size
        # Stage 3 §1/§16: preprocessing covers decode + EXIF transpose +
        # quality + orientation vote + gated normalization (zero OCR
        # calls; the upload bytes stay pristine for evidence — crops
        # upscale, never the full frame).
        img_timings["preprocessing_ms"] = round(
            (time.perf_counter() - t_img) * 1000, 1)

        # STAGE 1 — exactly ONE full-page OCR per image (budget:
        # MAX_FULL_PAGE_CALLS == number of images). Never rerun full-page
        # OCR for individual fields; Stage 2+3 use targeted crops only.
        t0 = time.perf_counter()
        stage_img = _fit(original, STAGE1_MAX_DIM)
        stage_w, stage_h = stage_img.size
        stage_lines = _tag(
            _call_provider(
                provider, _to_array(_enhance(stage_img)),
                f"{label}:stage1", errors, call_log,
                {"image_id": label, "stage": "full-page",
                 "purpose": "stage1-full-frame",
                 "crop_rect": None,
                 "preprocessing_variant": "stage1-downscale-enhance",
                 "reason": "single full-page pass for this image"}),
            label, index, "stage1")
        img_timings["stage1_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        img_timings["stage1_lines"] = len(stage_lines)
        # Reading orientation from box geometry (evidence metadata;
        # never rotates pixels, only region math downstream).
        orientation_info = _detect_orientation(stage_lines)
        img_timings["orientation"] = orientation_info.get("orientation", "0")
        diag_stage[label] = {
            "index": index, "orientation": orientation_info,
            "stage_size": [stage_w, stage_h],
            "original_size": [orig_w, orig_h],
            "stage_lines": [
                {"text": ln.text, "confidence": ln.confidence,
                 "box": ln.box} for ln in stage_lines],
        }

        # STAGE 2+3 — targeted regions only for unresolved fields.
        t0 = time.perf_counter()
        my_lines = list(stage_lines)
        # At most ONE Tesseract ingredient call per image: the first
        # ingredient region that needs fallback consumes the budget.
        tess_used_this_image = False
        t_regions = time.perf_counter()
        img_timings["region_detection_ms"] = 0.0
        if stage_lines and targeted_stages:
            statuses = extract_with_status(stage_lines)
            regions = _propose_regions(label, stage_lines, statuses,
                                       (stage_w, stage_h))
            # Layout evidence (Part I): heading-anchored panels + table
            # structure refine proposals within the existing budget —
            # tighter ingredient x-range, plus a nutrition-region OCR
            # pass when a table is actually detected.
            try:
                layout = _ocv.detect_layout(stage_lines, gray_small,
                                            (stage_w, stage_h))
                regions = _merge_layout_regions(regions, layout)
                img_timings["layout_notes"] = layout.get("notes", [])
            except Exception as exc:
                img_timings["layout_notes"] = [
                    f"layout detection skipped: {exc}"]
            sx, sy = orig_w / max(stage_w, 1), orig_h / max(stage_h, 1)
            # Stage 3 §16: region/layout discovery time (proposals +
            # layout merge; pure geometry, zero OCR calls).
            img_timings["region_detection_ms"] = round(
                (time.perf_counter() - t_regions) * 1000, 1)
            for region in regions:
                if targeted_used >= MAX_TARGETED_CALLS_PER_IMAGE:
                    log.info("ocr %s targeted budget exhausted (%d); "
                             "skipping region %s", label, targeted_used,
                             region.get("kind"))
                    img_timings.setdefault("budget_skipped", []).append(
                        region.get("kind"))
                    continue
                if any(_rect_iou(tuple(region["rect"]), seen) > 0.85
                       for seen in seen_rects):
                    log.info("ocr %s skipping duplicate crop %s",
                             label, region.get("kind"))
                    img_timings.setdefault("budget_skipped", []).append(
                        str(region.get("kind")) + ":duplicate")
                    continue
                if region.get("seed"):
                    ingredient_seeds.append(region["seed"])
                if region["kind"] in ("ingredients", "ingredients-scan"):
                    # First-class ingredient pipeline: bounded variants,
                    # each coherence-scored, early abort on a clean read.
                    # A heading-less scan whose result is a declaration
                    # block (not ingredients) is discarded, never pooled.
                    # Then the opt-in Tesseract fallback reconciles weak
                    # RapidOCR reads (ingredient crops only, one call).
                    from app.services.ocr import food as _food_mod

                    ing_variant_order = tuple(_ocv.choose_variant(
                        "ingredients", image_quality))
                    ing_lines, ing_times, ing_best, variant_texts = \
                        _run_ingredient_variants(
                            provider, original, region["rect"], (sx, sy),
                            label, index, errors, call_log,
                            variant_order=ing_variant_order,
                            quality=image_quality)
                    img_timings["regions"].extend(ing_times)
                    targeted_used += len(ing_times)
                    seen_rects.append(tuple(region["rect"]))
                    # Column guard: drop off-column lines from the winning
                    # crop read (neighbouring marketing/storage/care
                    # columns caught by padding). Majority-column,
                    # fail-open; rejections recorded as evidence below.
                    kept_lines, col_rejected = \
                        _food_mod.filter_offcolumn_lines(ing_lines)
                    if col_rejected and col_rejected[0].get("text", "") != \
                            "(column filter abstained: kept all)":
                        for rej in col_rejected:
                            rejected_lines.append({
                                "image": label, "image_index": index,
                                "field": "ingredients",
                                "text": rej.get("text", ""),
                                "score": rej.get("score"),
                                "reasons": rej.get("reasons", [])})
                        log.info("ocr %s column filter rejected %d line(s)",
                                 label, len(col_rejected))
                        ing_lines = kept_lines
                    if ing_times:
                        ing_times[0]["column_rejected"] = len(col_rejected) \
                            if col_rejected and col_rejected[0].get(
                                "text", "") != \
                            "(column filter abstained: kept all)" else 0
                        ing_times[0]["variant_order"] = list(
                            ing_variant_order)
                        ing_times[0]["rect"] = [round(v, 1) for v in
                                                region["rect"]]
                        ing_times[0]["orientation"] = region.get(
                            "orientation", "0")
                        if region.get("column"):
                            ing_times[0]["column"] = region["column"]
                    # Full-page assembly filter: stage-frame columns only.
                    # Transposed frames carry working-frame columns that do
                    # not map to a stage x-range (crop-level filtering
                    # already covers them), so they are excluded here.
                    if region.get("column") and \
                            region["column"].get("frame") == "stage":
                        col = region["column"]
                        image_columns[label] = (col["x0"], col["x1"])
                    if region["kind"] == "ingredients-scan":
                        scan_text, _ = _food_mod.reconstruct_ingredient_text(
                            ing_lines)
                        if not _food_mod.scan_result_usable(
                                scan_text,
                                {"score": ing_best.get("score", 0.0)}):
                            log.info("ocr %s dense scan discarded "
                                     "(declaration block, coherence %s)",
                                     label, ing_best.get("score"))
                            continue
                    if tess_used_this_image:
                        # Budget spent: rapid variants only, no 2nd call.
                        fb_info = {"triggered": False,
                                   "reasons": ["tesseract budget already "
                                               "used for this image"],
                                   "ocr_source": "rapidocr",
                                   "uncertain": False, "notes": [],
                                   "tess_ms": 0.0, "reconcile_ms": 0.0}
                    else:
                        ing_lines, fb_info = _maybe_tesseract_fallback(
                            tess_provider, original, region, (sx, sy),
                            label, index, errors, ing_lines, variant_texts,
                            stage_lines, call_log)
                        if fb_info.get("tess_entry") is not None:
                            tess_used_this_image = True
                    fb_info["image"] = label
                    ingredient_fallbacks.append(fb_info)
                    if fb_info.get("tess_entry"):
                        img_timings["regions"].append(
                            fb_info["tess_entry"])
                    my_lines.extend(ing_lines)
                    ingredient_region_lines.extend(ing_lines)
                    log.info("ocr %s ingredient variants: %d ran, best "
                             "coherence %s, tess fallback=%s", label,
                             len(ing_times), ing_best.get("score"),
                             fb_info.get("triggered"))
                    continue
                # Stage 3C §C: declaration crops go through the single
                # reusable field path (one crop, one OCR pass, evidence
                # preserved). Budgets, tagging, and timings are unchanged.
                from app.services.ocr import field_ocr as _field_ocr

                _kind_to_field = {
                    "mrp": "mrp", "mfg_date": "manufacturing_date",
                    "fssai": "fssai_license", "care": "consumer_care",
                    "nutrition": None, "lowconf-band": None}
                _field_type = _kind_to_field.get(region["kind"])
                _region = {"rect": tuple(region["rect"]),
                           "scale": (sx, sy), "prep": region["prep"]}

                def _single_pass(arr: Any, prep_meta: dict[str, Any],
                                 _region=region, _field_type=_field_type,
                                 _label=label, _sx=sx, _sy=sy) -> list:
                    _ = prep_meta
                    return _tag(
                        _call_provider(
                            provider, arr,
                            f"{_label}:region:{_region['kind']}", errors,
                            call_log,
                            {"image_id": _label, "stage": "targeted",
                             "purpose": _region["kind"],
                             "crop_rect": [round(v, 1)
                                           for v in _region["rect"]],
                             "preprocessing_variant": _region["prep"],
                             "field_type": _field_type,
                             "reason": f"field unresolved after stage1: "
                                       f"{_region['kind']}"}),
                        _label, index, f"region:{_region['kind']}")

                tr0 = time.perf_counter()
                _fout = _field_ocr.extract_field_from_region(
                    original, _region, _field_type, provider,
                    image_id=label, call_provider=_single_pass)
                if not (_fout.get("lines") or []) and _fout.get(
                        "error") == "degenerate crop":
                    continue  # nothing to OCR; budget untouched
                region_lines = _fout.get("lines") or []
                _prep = _fout.get("preprocessing") or {}
                img_timings["regions"].append({
                    "kind": region["kind"],
                    "field_type": _field_type,
                    "ms": round((time.perf_counter() - tr0) * 1000, 1),
                    "lines": len(region_lines),
                    "deskewed": _prep.get("deskewed", False),
                    "perspective_corrected": _prep.get(
                        "perspective_corrected", False),
                    "crop_rect": [round(v, 1) for v in region["rect"]],
                    "crop_size": _prep.get("crop_size"),
                    "error": _fout.get("error")})
                targeted_used += 1
                seen_rects.append(tuple(region["rect"]))
                my_lines.extend(region_lines)
        img_timings["regions_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        img_timings["targeted_used"] = targeted_used

        # STAGE 3b — at most ONE full-resolution band over low-confidence
        # Stage-1 lines, only while a required field is still unresolved
        # AND the per-image targeted budget is not exhausted AND the band
        # does not duplicate a region already OCR'd.
        if my_lines and targeted_stages \
                and targeted_used < MAX_TARGETED_CALLS_PER_IMAGE:
            check_now = extract_with_status(my_lines)
            if any(check_now.get(f, {}).get("status") != "DETECTED"
                   for f in REQUIRED_FIELDS):
                for band in _lowconf_bands(stage_lines, (stage_w, stage_h),
                                           max_bands=1):
                    if targeted_used >= MAX_TARGETED_CALLS_PER_IMAGE:
                        img_timings.setdefault("budget_skipped", []).append(
                            "lowconf-band:budget")
                        break
                    if any(_rect_iou(tuple(band["rect"]), seen) > 0.85
                           for seen in seen_rects):
                        img_timings.setdefault("budget_skipped", []).append(
                            "lowconf-band:duplicate")
                        continue
                    x0, y0, x1, y1 = band["rect"]
                    crop = original.crop((
                        max(0, int(x0 * sx)), max(0, int(y0 * sy)),
                        min(orig_w, int(x1 * sx)), min(orig_h, int(y1 * sy))))
                    if crop.size[0] < 8 or crop.size[1] < 8:
                        continue
                    crop, band_info = _prepare_crop_for_ocr(
                        crop, band["prep"], image_quality)
                    tr0 = time.perf_counter()
                    band_lines = _tag(
                        _call_provider(
                            provider, _prep_region(crop, band["prep"]),
                            f"{label}:region:{band['kind']}", errors,
                            call_log,
                            {"image_id": label, "stage": "targeted",
                             "purpose": band["kind"],
                             "crop_rect": [round(v, 1)
                                           for v in band["rect"]],
                             "preprocessing_variant": band["prep"],
                             "reason": "low-confidence stage1 band for "
                                       "unresolved required field"}),
                        label, index, f"region:{band['kind']}")
                    img_timings["regions"].append({
                        "kind": band["kind"],
                        "ms": round((time.perf_counter() - tr0) * 1000, 1),
                        "lines": len(band_lines),
                        "deskewed": band_info.get("deskewed", False),
                        "perspective_corrected": band_info.get(
                            "perspective_corrected", False)})
                    targeted_used += 1
                    seen_rects.append(tuple(band["rect"]))
                    my_lines.extend(band_lines)
                    break  # one band max per image

        # STAGE 4 — rotation only when the required fields are ALL still
        # unresolved and Stage 1 barely saw any text. At most one
        # rotated full-frame pass per image; its DIRECTION is chosen by
        # evidence (transpose geometry -> 90, upside-down pattern ->
        # 180, legacy default 90). When Stage 3A normalization already
        # rotated a decisive-transpose frame +90 and Stage 1 still
        # failed (or still reads transposed), the remaining hypothesis
        # is the other transpose direction: exactly one 180 pass on the
        # normalized frame. Logged as a full-page call (the sole
        # permitted full-page rerun).
        t0 = time.perf_counter()
        img_timings["rotation_ms"] = 0.0
        img_timings["rotated"] = False
        _still_transposed = (
            norm_transpose
            and _detect_orientation(stage_lines).get("transpose"))
        if my_lines and targeted_stages and (
                len(stage_lines) <= 5 or _still_transposed):
            check = extract_with_status(my_lines)
            if _still_transposed or all(
                    check.get(f, {}).get("status") != "DETECTED"
                    for f in REQUIRED_FIELDS):
                if norm_transpose:
                    rot_angle = 180
                    rot_reason = ("transpose-normalized frame still "
                                  "unresolved; trying the other transpose "
                                  "direction (180 on normalized frame)")
                else:
                    rot_angle = choose_fallback_rotation(stage_lines,
                                                         cv_orientation)
                    rot_reason = (
                        "all required fields unresolved with "
                        "<=5 stage1 lines; evidence-chosen "
                        f"{rot_angle}deg fallback")
                if rot_angle:
                    turned = _enhance(_fit(
                        original.rotate(rot_angle, expand=True),
                        STAGE1_MAX_DIM))
                    rot_lines = _tag(
                        _call_provider(
                            provider, _to_array(turned),
                            f"{label}:rot{rot_angle}", errors, call_log,
                            {"image_id": label, "stage": "full-page-rotated",
                             "purpose": "rotation-fallback",
                             "crop_rect": None,
                              "preprocessing_variant":
                                  f"rot{rot_angle}-enhance",
                              "reason": rot_reason}),
                        label, index, f"rot{rot_angle}")
                    img_timings["rotation_ms"] = round(
                        (time.perf_counter() - t0) * 1000, 1)
                    img_timings["rotated"] = True
                    img_timings["fallback_angle"] = rot_angle
                    my_lines.extend(rot_lines)

        per_image_lines.append(my_lines)
        all_lines.extend(my_lines)
        serial = [{"text": ln.text, "confidence": ln.confidence,
                   "box": ln.box, "image": label, "image_index": index,
                   "variant": getattr(ln, "variant", "stage1")}
                  for ln in my_lines]
        variants = ["stage1", *[r["kind"] for r in img_timings["regions"]]]
        if img_timings["rotated"]:
            variants.append(
                f"rot{img_timings.get('fallback_angle', 90)}")
        image_results.append({
            "image": label, "image_index": index,
            "text": "\n".join(ln.text for ln in my_lines),
            "lines": serial, "variants": variants,
            "error": None, "skipped": False})
        img_timings["image_ms"] = round((time.perf_counter() - t_img) * 1000,
                                        1)
        timings["images"][label] = img_timings
        # Stage 3A.5 completion record for near-duplicate reuse by later
        # images in this same call (hash + ink + final lines + timings).
        completed[label] = {"dhash": dup_hash, "ink": dup_ink,
                            "my_lines": my_lines,
                            "img_timings": img_timings,
                            "diag": diag_stage.get(label, {})}
        log.info("ocr %s: stage1=%sms/%d lines regions=%d rotation=%s total=%sms",
                 label, img_timings["stage1_ms"],
                 img_timings["stage1_lines"],
                 len(img_timings["regions"]), img_timings["rotated"],
                 img_timings["image_ms"])

    t_rec = time.perf_counter()
    pooled = extract_with_status(all_lines)
    # Contact codes + barcode candidates: additive evidence outside
    # FIELD_KEYS (whose membership is contract-tested), same line pool.
    contact = extract_contact_codes(all_lines)
    per_image_detailed = [extract_fields_detailed(lines)
                          for lines in per_image_lines]
    detailed = _reconcile_fields(per_image_detailed, pooled,
                                 per_image_lines)
    timings["reconciliation_ms"] = round(
        (time.perf_counter() - t_rec) * 1000, 1)
    legacy = {k: {"value": v["value"], "confidence": v["confidence"]}
              for k, v in detailed.items()}
    fields = {key: {"value": legacy[key]["value"], "provenance": "OCR",
                    "confidence": legacy[key]["confidence"],
                    "image": detailed[key]["image"],
                    "image_index": detailed[key]["image_index"],
                    "status": detailed[key]["status"],
                    "box": detailed[key].get("box")}
              for key in FIELD_KEYS}
    for key in FIELD_KEYS:
        fields.setdefault(key, {"value": None, "provenance": "OCR",
                                "confidence": None, "image": None,
                                "image_index": None, "status": "NOT_DETECTED",
                                "box": None})

    # Aggregate hybrid provenance across images: "hybrid" when any
    # fallback contributed Tesseract content, "tesseract" when only
    # Tesseract lines survived, else "rapidocr" (legacy behaviour).
    variants_seen = {str(getattr(ln, "variant", "") or "")
                     for ln in ingredient_region_lines}
    has_rapid = any(v.startswith("ingredients:v") for v in variants_seen)
    has_tess = "ingredients:tesseract" in variants_seen
    has_hybrid = "ingredients:hybrid" in variants_seen
    if has_hybrid or (has_tess and has_rapid):
        agg_source = "hybrid"
    elif has_tess:
        agg_source = "tesseract"
    else:
        agg_source = "rapidocr"
    fired = [fb for fb in ingredient_fallbacks if fb.get("triggered")]
    agg_fallback: dict[str, Any] | None = None
    if fired:
        agg_fallback = {
            "triggered": True,
            "images": [fb.get("image") for fb in fired],
            "reasons": sorted({r for fb in fired
                               for r in fb.get("reasons", [])}),
            "notes": [n for fb in fired for n in fb.get("notes", [])],
        }
    agg_force_review = any(fb.get("uncertain") for fb in fired)
    food = _safe_food(
        all_lines, ingredient_region_lines,
        " ".join(ingredient_seeds) or None,
        ingredient_source={"ocr_source": agg_source,
                           "fallback": agg_fallback,
                           "force_review": agg_force_review,
                           "review_reason": "tesseract/rapidocr "
                           "reconciliation uncertain; inspector review "
                           "required"} if agg_fallback else None,
        image_columns=image_columns or None)
    t_sym = time.perf_counter()
    veg = _safe_veg([raw for raw, _ in supplied])
    timings["symbol_ms"] = round((time.perf_counter() - t_sym) * 1000, 1)
    # Stage 3B.12 targeted symbol candidates (no OCR cost): small
    # square-outline contours per image, in relative coordinates, with
    # nearby OCR text attached for context. The colour verdict still
    # comes from detect_veg_symbol; regions.py turns candidates into a
    # VEG_SYMBOL vision ROI. Never raises.
    try:
        from app.services.ocr import veg_symbol as _veg_mod

        for _raw, _label in supplied:
            _info = diag_stage.get(_label)
            if not isinstance(_info, dict) or _raw is None:
                continue
            try:
                _cands = _veg_mod.find_symbol_candidates(_raw)
            except Exception:
                _cands = []
            _stage = _info.get("stage_lines") or []
            for _cand in _cands:
                try:
                    _rb = _cand.get("rel_box") or []
                    _cx = (_rb[0] + _rb[2]) / 2.0
                    _cy = (_rb[1] + _rb[3]) / 2.0
                    _near = []
                    for _sl in _stage:
                        _bb = _box_rect(_sl.get("box"))
                        if _bb is None:
                            continue
                        _sw, _sh = 1.0, 1.0
                        try:
                            _ss = _info.get("stage_size") or [1, 1]
                            _sw = float(_ss[0]) or 1.0
                            _sh = float(_ss[1]) or 1.0
                        except Exception:
                            pass
                        _ncx = ((_bb[0] + _bb[2]) / 2.0) / _sw
                        _ncy = ((_bb[1] + _bb[3]) / 2.0) / _sh
                        if abs(_ncx - _cx) < 0.15 and \
                                abs(_ncy - _cy) < 0.15:
                            _near.append(str(_sl.get("text", "")))
                    _cand["nearby_text"] = " | ".join(_near)[:120]
                except Exception:
                    _cand["nearby_text"] = ""
            _info["veg_candidates"] = _cands
    except Exception:
        pass
    # Stage 3C §K: classify the single best symbol-candidate crop (never
    # whole-image colours alone, never ingredient names). Ambiguity stays
    # UNKNOWN / NEEDS_REVIEW; the verdict is evidence, not a verdict on
    # compliance.
    try:
        from app.services.ocr import veg_symbol as _veg_mod2

        _best = None
        for _raw, _label in supplied:
            if _raw is None:
                continue
            for _cand in ((diag_stage.get(_label) or {}).get(
                    "veg_candidates") or []):
                _sc = float(_cand.get("score") or 0)
                if _best is None or _sc > _best[0]:
                    _best = (_sc, _raw, _label, _cand.get("rel_box"))
        if _best is not None and isinstance(veg, dict):
            _verdict = _veg_mod2.classify_symbol_crop(_best[1], _best[3])
            _verdict["image"] = _best[2]
            veg["crop_verdict"] = _verdict
    except Exception:
        pass
    food_timings = (food.get("timings", {}) if isinstance(food, dict)
                    else {})
    timings["nutrition_ms"] = food_timings.get("nutrition_ms", 0.0)
    timings["stage1_ms"] = round(sum(
        float(v.get("stage1_ms", 0) or 0)
        for v in timings["images"].values()
        if isinstance(v, dict)), 1)
    timings["ingredient_ms"] = round(sum(
        float(r.get("ms", 0) or 0)
        for v in timings["images"].values() if isinstance(v, dict)
        for r in v.get("regions", [])
        if str(r.get("kind", "")).startswith("ingredients")), 1)
    timings["declaration_ms"] = round(sum(
        float(r.get("ms", 0) or 0)
        for v in timings["images"].values() if isinstance(v, dict)
        for r in v.get("regions", [])
        if not str(r.get("kind", "")).startswith("ingredients")), 1)
    timings["tesseract_fallback_ms"] = round(sum(
        float(r.get("ms", 0) or 0)
        for v in timings["images"].values() if isinstance(v, dict)
        for r in v.get("regions", [])
        if r.get("engine") == "tesseract"), 1)
    timings["ingredient_reconcile_ms"] = round(sum(
        float(fb.get("reconcile_ms", 0) or 0)
        for fb in ingredient_fallbacks), 1)

    timings["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
    # Authoritative provider-call count comes from the structured call
    # log (one entry per invocation); the legacy region-length formula is
    # kept as a cross-check only.
    timings["provider_calls"] = len(call_log)
    timings["provider_calls_detail"] = call_log
    timings["rapidocr_calls"] = sum(
        1 for e in call_log if e.get("provider") != "tesseract")
    timings["tesseract_calls"] = sum(
        1 for e in call_log if e.get("provider") == "tesseract")
    timings["per_image_ms"] = {
        label: (v.get("image_ms") if isinstance(v, dict) else None)
        for label, v in timings["images"].items()}
    timings["stage_timings"] = {
        "preprocessing_ms": round(sum(
            float(v.get("preprocessing_ms", 0) or 0)
            for v in timings["images"].values()
            if isinstance(v, dict)), 1),
        "decode_ms": round(sum(
            float(v.get("decode_ms", 0) or 0)
            for v in timings["images"].values()
            if isinstance(v, dict)), 1),
        "normalization_ms": round(sum(
            float(v.get("normalization_ms", 0) or 0)
            for v in timings["images"].values()
            if isinstance(v, dict)), 1),
        "full_page_ms": timings.get("stage1_ms", 0.0),
        "region_detection_ms": round(sum(
            float(v.get("region_detection_ms", 0) or 0)
            for v in timings["images"].values()
            if isinstance(v, dict)), 1),
        "targeted_ms": round(
            float(timings.get("ingredient_ms", 0) or 0)
            + float(timings.get("declaration_ms", 0) or 0), 1),
        "ingredient_ms": timings.get("ingredient_ms", 0.0),
        "declaration_ms": timings.get("declaration_ms", 0.0),
        "tesseract_ms": timings.get("tesseract_fallback_ms", 0.0),
        "reconciliation_ms": timings.get("reconciliation_ms", 0.0),
        "symbol_ms": timings.get("symbol_ms", 0.0),
    }
    # Stage 3C §M: duplicate reuse accounting (evidence retained, zero
    # provider calls for reused frames).
    timings["duplicates_reused"] = sum(
        1 for r in image_results
        if isinstance(r, dict) and r.get("duplicate_of"))
    # Stage 3 §2/§19: image roles + field resolution counts. Roles come
    # from upload slots tempered by OCR evidence; counts come from the
    # reconciled per-field statuses (DETECTED / NEEDS_REVIEW /
    # NOT_DETECTED) — never a compliance verdict, never one generic
    # "accuracy" number. Images that reused a duplicate's OCR are
    # labelled DUPLICATE (evidence retained, extraction skipped).
    _t_roles = time.perf_counter()
    try:
        from app.services.ocr import image_roles as _roles

        _labels = [label for _, label in supplied]
        _roles_by_label = _roles.classify_roles(
            _labels,
            {label: per_image_lines[i]
             if i < len(per_image_lines) else []
             for i, label in enumerate(_labels)})
    except Exception:
        _roles_by_label = {}
    try:
        from app.services.ocr.image_roles import DUPLICATE as _DUP

        for _res in image_results:
            if _res.get("duplicate_of"):
                _roles_by_label[_res["image"]] = _DUP
    except Exception:
        pass
    timings["role_detection_ms"] = round(
        (time.perf_counter() - _t_roles) * 1000, 1)
    timings["stage_timings"]["role_detection_ms"] = \
        timings["role_detection_ms"]
    for _label, _role in _roles_by_label.items():
        try:
            if isinstance(timings.get("images", {}).get(_label), dict):
                timings["images"][_label]["role"] = _role
            if isinstance(diagnostics.get("images", {}).get(_label),
                          dict):
                diagnostics["images"][_label]["role"] = _role
        except Exception:
            pass
    timings["image_roles"] = dict(_roles_by_label)
    _field_counts = {"detected": 0, "needs_review": 0, "not_detected": 0}
    try:
        for _hit in (detailed or {}).values():
            _st = (_hit or {}).get("status")
            if _st == "DETECTED":
                _field_counts["detected"] += 1
            elif _st == "NEEDS_REVIEW":
                _field_counts["needs_review"] += 1
            else:
                _field_counts["not_detected"] += 1
    except Exception:
        pass
    timings["field_counts"] = _field_counts
    # Call-budget contract (spec §2/§9): full-page calls == images,
    # targeted <= MAX_TARGETED per image, tesseract <= 1 per image.
    timings["call_budget"] = {
        "max_full_page_calls": len(supplied),
        "max_targeted_calls_per_image": MAX_TARGETED_CALLS_PER_IMAGE,
        "max_tesseract_calls_per_image": MAX_TESSERACT_CALLS_PER_IMAGE,
        "max_provider_calls_per_image": MAX_PROVIDER_CALLS_PER_IMAGE,
        "full_page_calls": sum(
            1 for e in call_log if e.get("stage") == "full-page"),
        "rotated_calls": sum(
            1 for e in call_log if e.get("stage") == "full-page-rotated"),
        "budget_respected": all(
            sum(1 for e in call_log
                if e.get("image_id") == label)
            <= MAX_PROVIDER_CALLS_PER_IMAGE
            for label in timings.get("images", {})),
    }
    log.info("ocr done: %d images, %d provider calls, %d lines, %sms total",
             len(image_results), timings["provider_calls"], len(all_lines),
             timings["total_ms"])

    # Stage-1B: merge food-level contamination rejections into the
    # top-level audit trail in spec shape
    # {text, reason, confidence, source_box} (+image/field context).
    try:
        _ing = (food.get("fields", {}).get("ingredients", {})
                if isinstance(food, dict) else {})
        for _rej in _ing.get("rejected_lines", []) or []:
            rejected_lines.append({
                "text": _rej.get("text", ""),
                "reason": _rej.get("reason", ""),
                "confidence": _rej.get("confidence"),
                "source_box": _rej.get("source_box"),
                "image": _ing.get("image"),
                "image_index": _ing.get("image_index"),
                "field": "ingredients"})
    except Exception:
        pass
    diagnostics = _build_package_diagnostics(
        diag_stage, image_columns, timings, call_log, supplied)

    status = "OK" if all_lines else "NEEDS_REVIEW"
    # Stage 3C §N: per-field provider + preprocessing-variant evidence.
    # Resolved from the source line's own tags (never assumed): the hit
    # image label locates the per-image line list, image_index the line.
    _engine_name = getattr(provider, "name", "ocr")
    _lines_by_label: dict[str, list] = {}
    try:
        for _i, (_raw, _lab) in enumerate(supplied):
            if _i < len(per_image_lines):
                _lines_by_label.setdefault(_lab,
                                            per_image_lines[_i])
    except Exception:
        _lines_by_label = {}

    def _variant_for(hit: dict[str, Any]) -> str:
        try:
            _lab = hit.get("image")
            _idx = hit.get("image_index")
            _lines = _lines_by_label.get(_lab) or []
            if isinstance(_idx, int) and 0 <= _idx < len(_lines):
                return str(getattr(_lines[_idx], "variant",
                                   "stage1") or "stage1")
        except Exception:
            pass
        return "stage1"

    out: dict[str, Any] = {
        "status": status,
        "engine": _engine_name,
        "images": image_results,
        "images_analyzed": len(image_results),
        "fields": fields,
        "fields_detailed": {k: {"value": v["value"],
                                "confidence": v["confidence"],
                                "provenance": "OCR",
                                "provider": _engine_name,
                                "preprocessing_variant": _variant_for(v),
                                "image": v["image"],
                                "image_index": v["image_index"],
                                "box": v.get("box"),
                                "status": v["status"],
                                "sources": v.get("sources", []),
                                "candidates": v.get("candidates", []),
                                # Stage-1D retrieval evidence (additive):
                                # retained candidate pool, fused honesty
                                # score, scoring reasons, brand pick.
                                "all_candidates": v.get(
                                    "all_candidates", []),
                                "fused_confidence": v.get(
                                    "fused_confidence"),
                                "score_reasons": v.get("score_reasons", []),
                                "brand": v.get("brand")}
                            for k, v in detailed.items()},
        "food": food,
        "veg_nonveg_symbol": veg,
        "contact": contact,
        "errors": errors,
        "timings": timings,
        # Rejected-line evidence (column filter + contamination guard),
        # never silently dropped; raw OCR audit trail is untouched.
        "rejected_lines": rejected_lines,
        # Real-package diagnostics (layout/region evidence, §8).
        "diagnostics": diagnostics,
    }
    # Back-compat aliases: existing clients/tests read front/back.
    by_label = {r["image"]: r for r in image_results}
    out["front"] = by_label.get("front", image_results[0] if image_results
                                else {"image": "front", "text": "",
                                      "lines": [], "error": None,
                                      "skipped": True})
    out["back"] = by_label.get("back", {"image": "back", "text": "",
                                        "lines": [], "error": None,
                                        "skipped": True})
    return out


def _field_candidate_boxes(stage_lines: list[dict[str, Any]]
                           ) -> dict[str, list[dict[str, Any]]]:
    """Field-candidate boxes from Stage-1 lines (diagnostics only).

    Pure evidence surfacing for the layout/region debug view: which
    Stage-1 lines look like MRP / date / FSSAI / care / nutrition /
    ingredient-heading candidates. Never used for extraction decisions.
    """
    from app.services.ocr import fields as _fields

    out: dict[str, list[dict[str, Any]]] = {
        "mrp": [], "date": [], "fssai": [], "care": [],
        "nutrition": [], "ingredient_heading": []}
    rows = list(stage_lines or [])
    for k, ln in enumerate(rows):
        text = str(ln.get("text", "") or "")
        if not text.strip():
            continue
        entry = {"text": text[:120], "box": ln.get("box")}
        if _fields._MRP_CTX.search(text):
            out["mrp"].append(entry)
        if _fields._MFG_CTX.search(text) or _fields._BB_CTX.search(text):
            out["date"].append(entry)
        if _fields._FSSAI_CTX.search(text):
            out["fssai"].append(entry)
        if _fields._CARE_CTX.search(text):
            out["care"].append(entry)
        try:
            from app.services.ocr import food as _food

            if _food._NUTRI_HEAD.search(text):
                out["nutrition"].append(entry)
            ctx = " ".join(str(r.get("text", "") or "")
                           for r in rows[max(0, k - 2):k + 3])
            if _food._is_ingredient_heading(text, ctx):
                out["ingredient_heading"].append(entry)
        except Exception:
            pass
    return out


def _field_regions_for_label(
        cand_boxes: dict[str, list[dict[str, Any]]],
        label: str) -> dict[str, list[dict[str, Any]]]:
    """Field-keyed evidence regions for one image (Stage 3C §B).

    Derived from the same anchor candidate boxes as the debug view, so
    no extra OCR is spent. Each record carries region, image_id, bbox,
    anchor, source_lines and a heuristic confidence. Absent fields mean
    "no anchor found" (callers fall back to full-image behavior, never
    a fabricated region).
    """
    kind_to_fields: dict[str, list[str]] = {
        "mrp": ["mrp"],
        "date": ["manufacturing_date", "best_before", "use_by"],
        "fssai": ["fssai_license"],
        "care": ["consumer_care"],
        "nutrition": ["energy", "protein", "carbohydrate", "total_sugars",
                      "added_sugars", "total_fat", "saturated_fat",
                      "trans_fat", "sodium"],
        "ingredient_heading": ["ingredients"],
    }
    kind_to_region: dict[str, str] = {
        "mrp": "MRP", "date": "DATE", "fssai": "FSSAI",
        "care": "CONSUMER_CARE", "nutrition": "NUTRITION",
        "ingredient_heading": "INGREDIENTS",
    }
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        for kind, entries in (cand_boxes or {}).items():
            if kind not in kind_to_fields:
                continue
            for entry in entries or []:
                if not isinstance(entry, dict) or not entry.get("box"):
                    continue
                for field in kind_to_fields[kind]:
                    out.setdefault(field, []).append({
                        "region": kind_to_region[kind],
                        "image_id": label,
                        "bbox": entry.get("box"),
                        "anchor": str(entry.get("text", ""))[:60],
                        "source_lines": [str(entry.get("text", ""))],
                        "confidence": 0.8,
                    })
    except Exception:
        pass
    return out


def _build_package_diagnostics(
        diag_stage: dict[str, Any],
        image_columns: dict[str, tuple[float, float]],
        timings: dict[str, Any],
        call_log: list[dict[str, Any]],
        supplied: list[tuple[Any, str]]) -> dict[str, Any]:
    """Real-package extraction diagnostic artifact (spec §8).

    Per image: orientation, dimensions, OCR boxes, ingredient heading
    box, proposed ingredient crop, accepted/rejected boxes are carried
    on the food-layer ingredient evidence; MRP/date/FSSAI/care/
    nutrition candidate boxes, provider calls and timings here. Purely
    additive metadata — the normal extraction response is unchanged.
    When ``LEGALAKSHI_OCR_DEBUG=1``, targeted crops are additionally
    saved under a local temp debug directory (never committed; user
    package photos are never written to the repository).
    """
    import os as _os

    images: dict[str, Any] = {}
    for label, info in (diag_stage or {}).items():
        stage_lines = info.get("stage_lines", []) or []
        heading_box = None
        try:
            from app.services.ocr import food as _food

            for k, ln in enumerate(stage_lines):
                ctx = " ".join(str(r.get("text", "") or "") for r in
                               stage_lines[max(0, k - 2):k + 3])
                if _food._is_ingredient_heading(
                        str(ln.get("text", "") or ""), ctx):
                    heading_box = ln.get("box")
                    break
        except Exception:
            pass
        img_calls = [e for e in (call_log or [])
                     if e.get("image_id") == label]
        cand_boxes = _field_candidate_boxes(stage_lines)
        images[label] = {
            "image_index": info.get("index"),
            "orientation": (info.get("orientation") or {}).get(
                "orientation", "0"),
            "orientation_detail": info.get("orientation"),
            "original_dimensions": info.get("original_size"),
            "stage_dimensions": info.get("stage_size"),
            "ocr_boxes": stage_lines,
            "ingredient_heading_box": heading_box,
            "heading_column": (list(image_columns[label])
                               if label in (image_columns or {}) else None),
            "proposed_ingredient_crops": [
                e.get("crop_rect") for e in img_calls
                if e.get("purpose") == "ingredients"],
            "candidate_boxes": cand_boxes,
            "field_regions": _field_regions_for_label(cand_boxes, label),
            "provider_calls": img_calls,
            "timings": (timings.get("images", {}) or {}).get(label, {}),
        }
    debug_dir = None
    if _os.environ.get("LEGALAKSHI_OCR_DEBUG") == "1":
        try:
            import tempfile as _tf

            debug_dir = _tf.mkdtemp(prefix="legalakshi-ocr-debug-")
        except Exception:
            debug_dir = None
    return {"images": images,
            "debug_crops_dir": debug_dir,
            "note": "layout/region evidence; set LEGALAKSHI_OCR_DEBUG=1 "
                    "to persist targeted debug crops locally (never "
                    "committed to the repository)."}


def _safe_food(lines: list[OcrLine],
               region_lines: list[OcrLine] | None = None,
               heading_remainder: str | None = None,
               ingredient_source: dict[str, Any] | None = None,
               image_columns: dict[str, tuple[float, float]] | None = None
               ) -> dict[str, Any]:
    try:
        from app.services.ocr.food import extract_food_label

        return extract_food_label(lines, region_lines=region_lines,
                                  heading_remainder=heading_remainder,
                                  ingredient_source=ingredient_source,
                                  image_columns=image_columns)
    except Exception as exc:  # extraction must never break OCR
        log.warning("food extraction failed: %s", exc)
        return {"status": "NEEDS_REVIEW", "error": str(exc), "fields": {}}


def _safe_veg(raws: list[bytes]) -> dict[str, Any]:
    try:
        from app.services.ocr.veg_symbol import detect_veg_symbol

        return detect_veg_symbol(raws)
    except Exception as exc:
        log.warning("veg symbol detection failed: %s", exc)
        return {"status": "NEEDS_REVIEW", "classification": "UNKNOWN",
                "confidence": None, "provenance": "IMAGE",
                "reason": f"detection unavailable: {exc}"}


def _side_result(side: str, raw: bytes | None,
                 provider: Any) -> dict[str, Any]:
    if not raw:
        return {"image": side, "text": "",
                "lines": [], "error": None, "skipped": True}
    try:
        output = provider.extract(preprocess(raw), side)
    except OcrError as exc:
        log.warning("ocr %s failed: %s", side, exc)
        return {"image": side, "text": "", "lines": [],
                "error": str(exc), "skipped": False}
    serial = [{"text": ln.text, "confidence": ln.confidence,
               "box": ln.box} for ln in output.lines]
    text = "\n".join(ln.text for ln in output.lines)
    return {"image": side, "text": text, "lines": serial,
            "error": None, "skipped": False}


def extract_label(front_bytes: bytes | None, back_bytes: bytes | None,
                  provider: Any | None = None,
                  tess_provider: Any | None = None) -> dict[str, Any]:
    """Run OCR on front/back images and extract structured fields.

    Kept for back-compat (endpoint, existing tests). Multi-image callers
    should prefer :func:`extract_label_multi`. ``tess_provider`` opt-in
    enables the hybrid Tesseract ingredient fallback.
    """
    multi = extract_label_multi(
        [(front_bytes, "front"), (back_bytes, "back")],
        provider=provider, tess_provider=tess_provider)
    images = {r["image"]: r for r in multi["images"]}
    front = images.get("front", {"image": "front", "text": "", "lines": [],
                                 "error": None, "skipped": True})
    back = images.get("back", {"image": "back", "text": "", "lines": [],
                               "error": None, "skipped": True})
    front_compat = {"image": "front", "text": front["text"],
                    "lines": [{"text": ln["text"],
                               "confidence": ln["confidence"],
                               "box": ln.get("box")}
                              for ln in front["lines"]],
                    "error": front["error"], "skipped": front["skipped"]}
    back_compat = {"image": "back", "text": back["text"],
                   "lines": [{"text": ln["text"],
                              "confidence": ln["confidence"],
                              "box": ln.get("box")}
                             for ln in back["lines"]],
                   "error": back["error"], "skipped": back["skipped"]}
    legacy_fields = {k: {"value": v["value"], "provenance": "OCR",
                         "confidence": v["confidence"]}
                     for k, v in multi["fields"].items()}
    return {"status": multi["status"], "engine": multi["engine"],
            "front": front_compat, "back": back_compat,
            "fields": legacy_fields,
            "fields_detailed": multi["fields_detailed"],
            "food": multi["food"],
            "veg_nonveg_symbol": multi["veg_nonveg_symbol"],
            "images": multi["images"],
            "images_analyzed": multi["images_analyzed"],
            "contact": multi.get("contact", {}),
            "errors": multi["errors"],
            "timings": multi["timings"]}
