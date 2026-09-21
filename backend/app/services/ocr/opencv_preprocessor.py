"""OpenCV preprocessing + image-analysis layer for package OCR.

Position in the pipeline (nothing here calls an OCR engine)::

    pixels
      -> analyze_quality ......... sharpness/brightness/contrast/readability
      -> estimate_orientation .... transpose evidence WITHOUT extra OCR passes
      -> rectify_full_frame ...... optional high-confidence deskew/quad warp
      -> Stage-1 RapidOCR (one pass, unchanged provider contract)
      -> detect_layout ........... panel/column/table map from boxes+edges
      -> targeted crops .......... deskew + optional quad rectify per crop
      -> choose_variant .......... quality-driven variant order ("adapt" new)
      -> RapidOCR regions + Tesseract fallback (unchanged, budgeted)

``cv2`` is guaranteed wherever RapidOCR runs (rapidocr-onnxruntime
requires opencv-python), but every public helper degrades gracefully
when cv2 is missing: quality reports ``cv2: False`` with neutral
grades, geometry helpers report "not applied", and variant selection
falls back to the legacy order. No helper ever raises for a bad image
— worst case is the unmodified input plus a note.
"""
from __future__ import annotations

import math
from typing import Any

# Quality grade thresholds. Calibrated on synthetic text panels rendered
# at the sizes this pipeline OCRs (Stage-1 ~1280px, crops ~300-1800px);
# see tests/test_opencv_preprocessor.py. Conservative by design: FAIR
# never triggers destructive transforms, only variant ordering.
_BLUR_GOOD = 120.0
_BLUR_FAIR = 40.0
# Text panels are bimodal (ink + paper) with modest global stddev;
# calibrated on synthetic panels (sharp ~18, faint ~9, dim ~7).
_CONTRAST_GOOD = 12.0
_CONTRAST_FAIR = 6.0
# Paper-white pages (mean ~250) are normal; only dim/blown exposures
# are poor.
_BRIGHT_GOOD_LO = 120.0
_BRIGHT_GOOD_HI = 253.0
_BRIGHT_FAIR_LO = 50.0
_BRIGHT_FAIR_HI = 253.0
_RES_GOOD = 1200
_RES_FAIR = 700
# Decisive margin for the transpose vote (best/second-best energy ratio).
_TRANSPOSE_MARGIN = 1.5
# Deskew gates: ignore sub-pixel noise, never warp wild tilts.
_DESKEW_MIN_DEG = 0.5
_DESKEW_MAX_DEG = 12.0
# Quad gates for perspective correction.
_QUAD_MIN_AREA_FRAC = 0.12
_QUAD_MAX_AREA_FRAC = 0.95
_QUAD_ANGLE_TOL_DEG = 15.0
_QUAD_MIN_SCORE = 0.55


def cv2_available() -> bool:
    """Whether OpenCV is importable in this process."""
    try:
        import cv2  # noqa: F401

        return True
    except Exception:
        return False


def _cv2() -> Any | None:
    try:
        import cv2

        return cv2
    except Exception:
        return None


def _as_gray_small(image_np: Any, max_dim: int = 400) -> Any | None:
    """Downscaled grayscale view for analysis (never for OCR)."""
    cv2 = _cv2()
    if cv2 is None:
        return None
    try:
        import numpy as np

        arr = np.asarray(image_np)
        if arr.size == 0:
            return None
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 \
            else arr.astype("uint8")
        h, w = gray.shape[:2]
        scale = min(1.0, max_dim / max(h, w))
        if scale < 1.0:
            gray = cv2.resize(gray, (max(1, int(w * scale)),
                                     max(1, int(h * scale))),
                              interpolation=cv2.INTER_AREA)
        return gray
    except Exception:
        return None


def _grade(value: float, good_lo: float, good_hi: float,
           fair_lo: float, fair_hi: float) -> str:
    if good_lo <= value <= good_hi:
        return "GOOD"
    if fair_lo <= value <= fair_hi:
        return "FAIR"
    return "POOR"


# Stage 3A.3 skew grades (degrees of dominant text-line tilt).
_SKEW_GOOD_DEG = 1.0
_SKEW_FAIR_DEG = 3.0


def text_density_metrics(gray_small: Any) -> dict[str, Any]:
    """Skew + text-density + text-coverage estimates (Stage 3A.3).

    Pure analysis on a small grayscale view: dominant text-line angle
    via minimum-area rectangle over the Otsu text mask, ink fraction,
    and text-mask bounding-box coverage. Never raises; never blocks —
    unmeasurable inputs report None values with a note. Informational
    only: poor values route fields to NEEDS_REVIEW downstream, they
    never fail an inspection here.
    """
    out: dict[str, Any] = {"skew_deg": None, "skew_grade": None,
                           "text_density": None, "text_coverage": None,
                           "notes": []}
    cv2 = _cv2()
    if cv2 is None or gray_small is None:
        out["notes"].append("cv2 unavailable; skew/density neutral")
        return out
    try:
        import numpy as np

        coords = _text_pixel_coords(gray_small)
        if coords is None:
            out["notes"].append("no text-like mask; skew/density unknown")
            return out
        angle = float(cv2.minAreaRect(coords)[-1])
        if angle < -45.0:
            angle += 90.0
        out["skew_deg"] = round(angle, 2)
        skew_abs = abs(angle)
        out["skew_grade"] = ("GOOD" if skew_abs <= _SKEW_GOOD_DEG
                             else "FAIR" if skew_abs <= _SKEW_FAIR_DEG
                             else "POOR")
        h, w = int(gray_small.shape[0]), int(gray_small.shape[1])
        total = max(h * w, 1)
        out["text_density"] = round(float(len(coords)) / total, 4)
        xs, ys = coords[:, 0], coords[:, 1]
        bw = max(float(xs.max() - xs.min()), 1.0)
        bh = max(float(ys.max() - ys.min()), 1.0)
        out["text_coverage"] = round(min(1.0, (bw * bh) / total), 4)
    except Exception as exc:
        out["notes"].append(f"density metrics failed safely: {exc}")
    return out


def analyze_quality(image_np: Any) -> dict[str, Any]:
    """Lightweight image diagnostics (no OCR): sharpness via Laplacian
    variance, brightness/contrast from grayscale statistics, resolution
    from pixel dimensions, plus an overall readability estimate.

    Never rejects anything — worst case is POOR grades with notes the
    officer UI can surface next to NEEDS_REVIEW fields.
    """
    info: dict[str, Any] = {"cv2": cv2_available(), "notes": []}
    try:
        import numpy as np

        arr = np.asarray(image_np)
        if arr.size == 0:
            raise ValueError("empty image")
        h, w = arr.shape[:2]
    except Exception as exc:
        info.update({"width": 0, "height": 0, "aspect": None,
                     "sharpness": "POOR", "brightness": "POOR",
                     "contrast": "POOR", "resolution": "POOR",
                     "readability": "POOR",
                     "notes": [f"unreadable pixels: {exc}"]})
        return info
    info["width"] = int(w)
    info["height"] = int(h)
    info["aspect"] = round(w / h, 3) if h else None
    gray = _as_gray_small(arr, max_dim=800)
    if gray is None:
        for key in ("sharpness", "brightness", "contrast"):
            info[key] = {"value": None, "grade": "FAIR"}
        info["notes"].append("cv2 unavailable; photometric grades neutral")
    else:
        cv2 = _cv2()
        assert cv2 is not None
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        mean = float(gray.mean())
        std = float(gray.std())
        info["sharpness"] = {
            "value": round(lap_var, 1),
            "grade": ("GOOD" if lap_var >= _BLUR_GOOD else
                      "FAIR" if lap_var >= _BLUR_FAIR else "POOR")}
        info["brightness"] = {
            "value": round(mean, 1),
            "grade": _grade(mean, _BRIGHT_GOOD_LO, _BRIGHT_GOOD_HI,
                            _BRIGHT_FAIR_LO, _BRIGHT_FAIR_HI)}
        info["contrast"] = {
            "value": round(std, 1),
            "grade": ("GOOD" if std >= _CONTRAST_GOOD else
                      "FAIR" if std >= _CONTRAST_FAIR else "POOR")}
        if info["sharpness"]["grade"] == "POOR":
            info["notes"].append("image looks blurry; small print may misread")
        if info["brightness"]["grade"] == "POOR":
            info["notes"].append("exposure looks off; contrast variants help")
    max_dim = max(int(w), int(h))
    info["resolution"] = {
        "value": max_dim,
        "grade": ("GOOD" if max_dim >= _RES_GOOD else
                  "FAIR" if max_dim >= _RES_FAIR else "POOR")}
    if info["resolution"]["grade"] == "POOR":
        info["notes"].append("low resolution; tiny print may be unreadable")
    grades = [info["sharpness"]["grade"] if isinstance(info["sharpness"], dict)
              else info["sharpness"],
              info["brightness"]["grade"] if isinstance(info["brightness"], dict)
              else info["brightness"],
              info["contrast"]["grade"] if isinstance(info["contrast"], dict)
              else info["contrast"],
              info["resolution"]["grade"]]
    poor = grades.count("POOR")
    if poor >= 2 or info["sharpness"] == "POOR" or (
            isinstance(info["sharpness"], dict)
            and info["sharpness"]["grade"] == "POOR"):
        info["readability"] = "POOR"
    elif "POOR" in grades or "FAIR" in grades:
        info["readability"] = "FAIR"
    else:
        info["readability"] = "GOOD"
    # Stage 3A.3 additive metrics: skew estimate + text density/coverage.
    # Never alter the grades above; poor values only inform NEEDS_REVIEW
    # downstream via notes.
    try:
        density = text_density_metrics(
            _as_gray_small(image_np, max_dim=400))
        info["skew"] = {"value": density.get("skew_deg"),
                        "grade": density.get("skew_grade") or "FAIR"}
        info["text_density"] = density.get("text_density")
        info["text_coverage"] = density.get("text_coverage")
        info["notes"].extend(density.get("notes", []))
        if (density.get("skew_grade") == "POOR"
                and info["readability"] == "GOOD"):
            info["notes"].append(
                "noticeable skew on an otherwise readable image; "
                "targeted deskew may help small print")
    except Exception:
        pass
    return info


def estimate_orientation(image_np: Any) -> dict[str, Any]:
    """Transpose evidence from pixels alone (zero OCR calls).

    Compares horizontal vs vertical projection energy on a small
    grayscale: running text yields strong row structure (high row-mean
    variance) and weak column structure. A decisive vertical win means
    the frame is transposed (90/270). 0-vs-180 is geometrically
    indistinguishable here and is NOT claimed — that ambiguity stays
    with the existing rotation fallback.
    """
    out: dict[str, Any] = {"transpose": False, "decisive": False,
                           "ratio": None, "notes": []}
    gray = _as_gray_small(image_np, max_dim=400)
    if gray is None:
        out["notes"].append("cv2 unavailable; no pixel orientation evidence")
        return out
    try:
        import numpy as np

        # Central crop: page margins and borders otherwise dominate the
        # column/row energy balance and invert the vote.
        h, w = gray.shape[:2]
        my, mx = int(h * 0.12), int(w * 0.12)
        core = gray[my:h - my, mx:w - mx] \
            if h > 2 * my and w > 2 * mx else gray
        row_energy = float(np.var(core.mean(axis=1)))
        col_energy = float(np.var(core.mean(axis=0)))
        if row_energy <= 0:
            out["notes"].append("flat projection; no orientation evidence")
            return out
        ratio = col_energy / row_energy
        out["ratio"] = round(ratio, 3)
        if ratio >= _TRANSPOSE_MARGIN:
            out.update(transpose=True, decisive=True)
            out["notes"].append(
                "strong vertical structure: frame looks transposed (90/270)")
        elif (1.0 / ratio) >= _TRANSPOSE_MARGIN:
            out.update(transpose=False, decisive=True)
            out["notes"].append("strong horizontal structure: frame looks "
                                "upright (0/180)")
        else:
            out["notes"].append("projection energies tied; keeping original")
        return out
    except Exception as exc:
        out["notes"].append(f"orientation estimate failed: {exc}")
        return out


def _text_pixel_coords(gray: Any) -> Any | None:
    """Dark-pixel coordinates of a thresholded text mask (or None)."""
    cv2 = _cv2()
    if cv2 is None:
        return None
    try:
        import numpy as np

        _, mask = cv2.threshold(gray, 0, 255,
                                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        frac = float((mask > 0).mean())
        if not 0.005 <= frac <= 0.6:  # not a text-bearing mask; bail out
            return None
        ys, xs = np.where(mask > 0)
        if len(xs) < 50:
            return None
        return np.stack([xs, ys], axis=1).astype("float32")
    except Exception:
        return None


def deskew_image(gray_np: Any,
                 max_angle: float = _DESKEW_MAX_DEG
                 ) -> tuple[Any, bool, float, list[str]]:
    """Lightweight deskew via the text mask's minimum-area rectangle.

    Returns (image, applied, angle_deg, notes). Applies ONLY a modest
    rotation (|angle| in [0.5, max_angle]) with solid text evidence;
    anything else returns the input untouched. Never distorts.
    """
    notes: list[str] = []
    cv2 = _cv2()
    if cv2 is None:
        return gray_np, False, 0.0, ["cv2 unavailable; deskew skipped"]
    try:
        import numpy as np

        small = _as_gray_small(gray_np, max_dim=800)
        if small is None:
            return gray_np, False, 0.0, ["empty input; deskew skipped"]
        coords = _text_pixel_coords(small)
        if coords is None:
            return gray_np, False, 0.0, \
                ["no text-like mask; deskew skipped"]
        angle = float(cv2.minAreaRect(coords)[-1])
        # OpenCV reports [-90, 0); normalise to a signed skew in [-45, 45].
        if angle < -45.0:
            angle += 90.0
        if abs(angle) < _DESKEW_MIN_DEG:
            return gray_np, False, round(angle, 2), \
                ["skew negligible; deskew skipped"]
        if abs(angle) > max_angle:
            return gray_np, False, round(angle, 2), [
                f"skew {angle:.1f}deg exceeds {max_angle}deg cap; "
                "left to the rotation fallback"]
        h, w = (np.asarray(gray_np).shape[0], np.asarray(gray_np).shape[1])
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle,
                                         1.0)
        straight = cv2.warpAffine(np.asarray(gray_np), matrix, (w, h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
        return straight, True, round(angle, 2), \
            [f"deskewed {angle:.1f}deg"]
    except Exception as exc:
        return gray_np, False, 0.0, [f"deskew failed safely: {exc}"]


def _order_points(pts: Any) -> Any | None:
    try:
        import numpy as np

        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        d = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(d)]
        rect[3] = pts[np.argmax(d)]
        return rect
    except Exception:
        return None


def find_quad(image_np: Any) -> dict[str, Any]:
    """Candidate package/document quadrilateral from edge contours.

    Strict gates only: convex 4-point contour, sane area fraction,
    near-right-angle corners. Anything doubtful reports found=False and
    the caller must keep the original image.
    """
    out: dict[str, Any] = {"found": False, "quad": None, "confidence": 0.0,
                           "notes": []}
    cv2 = _cv2()
    if cv2 is None:
        out["notes"].append("cv2 unavailable; quad search skipped")
        return out
    try:
        import numpy as np

        gray = _as_gray_small(image_np, max_dim=800)
        if gray is None:
            out["notes"].append("empty input; quad search skipped")
            return out
        h, w = gray.shape[:2]
        frame_area = float(h * w)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        best = None
        best_score = 0.0
        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            frac = area / frame_area
            if not _QUAD_MIN_AREA_FRAC <= frac <= _QUAD_MAX_AREA_FRAC:
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            pts = approx.reshape(4, 2).astype("float32")
            # Corner squareness: each interior angle near 90 degrees.
            worst = 0.0
            for i in range(4):
                a = pts[i] - pts[(i - 1) % 4]
                b = pts[(i + 1) % 4] - pts[i]
                denom = (float(np.linalg.norm(a) * np.linalg.norm(b))
                         or 1e-6)
                ang = abs(math.degrees(math.acos(max(-1.0, min(
                    1.0, float(np.dot(a, b)) / denom)))))
                worst = max(worst, abs(ang - 90.0))
            if worst > _QUAD_ANGLE_TOL_DEG:
                continue
            # Score: squareness blended with area presence.
            score = round(max(0.0, min(1.0,
                                       (1.0 - worst / 90.0) * 0.6
                                       + min(frac, 0.8) * 0.5)), 3)
            if score > best_score:
                best_score, best = score, pts
        if best is None or best_score < _QUAD_MIN_SCORE:
            out["notes"].append("no confident quadrilateral; keeping original")
            return out
        ordered = _order_points(best)
        if ordered is None:
            out["notes"].append("quad ordering failed; keeping original")
            return out
        # Report in 0..1 relative coordinates (resolution independent).
        rel = [[round(float(x) / w, 4), round(float(y) / h, 4)]
               for x, y in ordered.tolist()]
        out.update(found=True, quad=rel, confidence=best_score,
                   notes=[f"quad confidence {best_score}"])
        return out
    except Exception as exc:
        out["notes"].append(f"quad search failed safely: {exc}")
        return out


def rectify_crop(crop_pil: Any) -> tuple[Any, bool, dict[str, Any]]:
    """Optional perspective correction for ONE targeted crop.

    Warps only when a confident quadrilateral is found strictly INSIDE
    the crop (package photographed at an angle). Otherwise returns the
    input untouched. Never raises.
    """
    info: dict[str, Any] = {"perspective_corrected": False, "notes": []}
    cv2 = _cv2()
    if cv2 is None:
        info["notes"].append("cv2 unavailable; no rectification")
        return crop_pil, False, info
    try:
        import numpy as np

        arr = np.asarray(crop_pil.convert("RGB"))
        h, w = arr.shape[:2]
        if min(h, w) < 60:
            info["notes"].append("crop too small to rectify safely")
            return crop_pil, False, info
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(h * w)
        best = None
        best_score = 0.0
        for cnt in contours:
            area = float(cv2.contourArea(cnt))
            frac = area / frame_area
            # Strictly inside the crop: a tilted panel, not the frame.
            if not 0.25 <= frac <= 0.92:
                continue
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
            if len(approx) != 4 or not cv2.isContourConvex(approx):
                continue
            pts = approx.reshape(4, 2).astype("float32")
            worst = 0.0
            for i in range(4):
                a = pts[i] - pts[(i - 1) % 4]
                b = pts[(i + 1) % 4] - pts[i]
                denom = (float(np.linalg.norm(a) * np.linalg.norm(b))
                         or 1e-6)
                ang = abs(math.degrees(math.acos(max(-1.0, min(
                    1.0, float(np.dot(a, b)) / denom)))))
                worst = max(worst, abs(ang - 90.0))
            if worst > _QUAD_ANGLE_TOL_DEG:
                continue
            score = round(max(0.0, min(1.0, (1.0 - worst / 90.0))), 3)
            if score > best_score:
                best_score, best = score, pts
        if best is None or best_score < 0.70:
            info["notes"].append("no confident inner quad; crop unchanged")
            return crop_pil, False, info
        ordered = _order_points(best)
        if ordered is None:
            info["notes"].append("quad ordering failed; crop unchanged")
            return crop_pil, False, info
        (tl, tr, br, bl) = ordered
        width = int(max(float(np.linalg.norm(br - bl)),
                        float(np.linalg.norm(tr - tl))))
        height = int(max(float(np.linalg.norm(tr - tl)),
                         float(np.linalg.norm(bl - tl))))
        if width < 40 or height < 40:
            info["notes"].append("warped size degenerate; crop unchanged")
            return crop_pil, False, info
        dst = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1],
                        [0, height - 1]], dtype="float32")
        matrix = cv2.getPerspectiveTransform(ordered, dst)
        warped = cv2.warpPerspective(arr, matrix, (width, height),
                                     borderMode=cv2.BORDER_REPLICATE)
        from PIL import Image as _Image

        info.update(perspective_corrected=True,
                    notes=[f"rectified tilted panel (quad {best_score})"])
        return _Image.fromarray(warped), True, info
    except Exception as exc:
        info["notes"].append(f"rectification failed safely: {exc}")
        return crop_pil, False, info


def _long_lines_mask(gray: Any, horizontal: bool) -> Any | None:
    """Morphological mask of long straight line segments."""
    cv2 = _cv2()
    if cv2 is None:
        return None
    try:
        _, binary = cv2.threshold(gray, 0, 255,
                                  cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        h, w = binary.shape[:2]
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(20, w // 25), 1) if horizontal
            else (1, max(20, h // 25)))
        return cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    except Exception:
        return None


def find_table_rect(image_np: Any, stage_size: tuple[int, int] | None = None,
                    boxes: list[Any] | None = None) -> dict[str, Any]:
    """Candidate nutrition-table rectangle (Part K).

    Combines long horizontal/vertical line structure (tables print
    rules) with digit density from OCR boxes when supplied. Returns the
    rect in the INPUT pixel frame plus a confidence; found=False unless
    both structural and textual evidence agree.
    """
    out: dict[str, Any] = {"found": False, "rect": None, "confidence": 0.0,
                           "notes": []}
    cv2 = _cv2()
    if cv2 is None:
        out["notes"].append("cv2 unavailable; table search skipped")
        return out
    try:
        import numpy as np

        arr = np.asarray(image_np)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 \
            else arr.astype("uint8")
        h, w = gray.shape[:2]
        hm = _long_lines_mask(gray, True)
        vm = _long_lines_mask(gray, False)
        if hm is None or vm is None:
            out["notes"].append("line masks unavailable")
            return out
        grid = cv2.bitwise_or(hm, vm)
        contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(h * w)
        best = None
        best_score = 0.0
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = float(cw * ch)
            if area < 0.02 * frame_area or area > 0.80 * frame_area:
                continue
            if min(cw, ch) < 30:
                continue
            # Both orientations must contribute inside the candidate.
            roi_h = hm[y:y + ch, x:x + cw]
            roi_v = vm[y:y + ch, x:x + cw]
            h_frac = float((roi_h > 0).mean())
            v_frac = float((roi_v > 0).mean())
            if h_frac < 0.002 or v_frac < 0.0008:
                continue
            struct = min(1.0, (h_frac * 60.0 + v_frac * 150.0) / 2.0)
            digit = 0.0
            if boxes:
                inside = total = 0
                for b in boxes:
                    try:
                        r = _rect_of(b)
                        if r is None:
                            continue
                        cx = (r[0] + r[2]) / 2.0
                        cy = (r[1] + r[3]) / 2.0
                        sx = (stage_size[0] / w) if stage_size else 1.0
                        sy = (stage_size[1] / h) if stage_size else 1.0
                        px, py = cx / sx, cy / sy
                        if x <= px <= x + cw and y <= py <= y + ch:
                            total += 1
                            import re as _re

                            if _re.search(r"\d", str(
                                    b.get("text", "") if isinstance(
                                        b, dict) else getattr(
                                        b, "text", ""))):
                                inside += 1
                    except Exception:
                        continue
                digit = (inside / total) if total >= 3 else 0.0
                if total >= 3 and digit < 0.25:
                    continue  # text present but not numeric: not a table
            score = round(0.55 * min(1.0, struct) + 0.45 * digit, 3)
            if score > best_score:
                best_score = score
                best = (float(x), float(y), float(x + cw), float(y + ch))
        if best is None or best_score < 0.45:
            out["notes"].append("no table-like region with numeric "
                                "support found")
            return out
        out.update(found=True, rect=[round(v, 1) for v in best],
                   confidence=best_score,
                   notes=[f"table candidate confidence {best_score}"])
        return out
    except Exception as exc:
        out["notes"].append(f"table search failed safely: {exc}")
        return out


def _rect_of(box: Any) -> tuple[float, float, float, float] | None:
    try:
        if isinstance(box, dict):
            box = box.get("box")
        pts = [list(map(float, p)) for p in box]  # type: ignore[union-attr]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        return None


def detect_layout(stage_lines: list[Any],
                  gray_small: Any | None = None,
                  stage_size: tuple[int, int] | None = None
                  ) -> dict[str, Any]:
    """Panel/column/table map from OCR boxes + edge structure (Part I).

    Returns {"regions": [{kind, rect, confidence, reason}]} in STAGE
    coordinates. Kinds: INGREDIENTS, NUTRITION, MRP, DATE, FSSAI,
    MANUFACTURER, CONSUMER_CARE, STORAGE, ALLERGEN, MARKETING, SYMBOL,
    PRODUCT. Pure evidence — the service decides which regions to OCR,
    always within the existing call budget.
    """
    from app.services.ocr import food as food_mod

    regions: list[dict[str, Any]] = []
    notes: list[str] = []
    lines = []
    for ln in stage_lines or []:
        text = str(getattr(ln, "text", "") or "")
        box = getattr(ln, "box", None)
        rect = _rect_of(box)
        if rect is None:
            continue
        lines.append({"text": text, "rect": rect})
    if not lines:
        return {"regions": [], "notes": ["no boxed lines for layout"]}
    try:
        texts = [ln["text"] for ln in lines]
        boundary = []
        for k, ln in enumerate(lines):
            ctx = " ".join(texts[max(0, k - 2):k + 3])
            section = food_mod.match_section_boundary(ln["text"], ctx)
            if section is None and food_mod._is_ingredient_heading(
                    ln["text"], ctx):
                section = "INGREDIENTS"
            boundary.append((section, ln))
    except Exception:
        boundary = [(None, ln) for ln in lines]
    # Anchor each labelled section at its first heading line; the panel
    # extends down to the next anchored heading (or frame bottom).
    ordered = sorted(enumerate(lines), key=lambda p: (p[1]["rect"][1],
                                                      p[1]["rect"][0]))
    anchors: list[tuple[int, str, dict[str, Any]]] = []
    for pos, (section, ln) in enumerate(
            sorted(boundary, key=lambda p: (p[1]["rect"][1],
                                            p[1]["rect"][0]))):
        _ = pos
        if not section:
            continue
        # One anchor per line max: a line classified twice (e.g. by the
        # boundary pass and the INGREDIENTS fallback below) must not
        # appear twice, or the duplicate squeezes out neighbouring
        # same-row panels (e.g. an MRP column next to the heading).
        if any(existing is ln for _, _, existing in anchors):
            continue
        anchors.append((len(anchors), section, ln))
    # INGREDIENTS fallback for headings the boundary pass missed.
    # Only STRONG openers anchor a panel: a gated CONTENTS/CONTAINS
    # line never anchors on its own (an allergen row must not anchor
    # the ingredient panel); the crop-level proposal still uses
    # evidence-qualified openers for OCR targeting.
    if not any(s == "INGREDIENTS" for _, s, _ in anchors):
        for ln in lines:
            try:
                core = (ln["text"] or "").strip().split()
                first = (core[0] if core else "").lower().strip(":.,-/")
                if first in ("contents", "contains"):
                    continue
                if food_mod._is_ingredient_heading(ln["text"]):
                    anchors.append((len(anchors), "INGREDIENTS", ln))
                    break
            except Exception:
                continue
    anchors.sort(key=lambda a: a[2]["rect"][1])
    frame_top = min(ln["rect"][1] for ln in lines)
    frame_bottom = max(ln["rect"][3] for ln in lines)
    frame_h = max(frame_bottom - frame_top, 1.0)
    # Unheaded identity panels: the top band is the product panel, and
    # unusually tall lines up there are brand lettering (height >= 2x
    # the median line height). Low confidence, clearly reasoned — the
    # service uses them as evidence hints, never verdicts. SYMBOL stays
    # with the image-based veg detector; MARKETING is not classified.
    try:
        heights = sorted(ln["rect"][3] - ln["rect"][1] for ln in lines)
        med_h = heights[len(heights) // 2] if heights else 0.0
        top_band = [ln for ln in lines
                    if ln["rect"][1] < frame_top + 0.25 * frame_h]
        if top_band:
            regions.append({
                "kind": "PRODUCT",
                "rect": [round(min(ln["rect"][0] for ln in top_band), 1),
                         round(frame_top, 1),
                         round(max(ln["rect"][2] for ln in top_band), 1),
                         round(max(ln["rect"][3] for ln in top_band), 1)],
                "confidence": 0.4,
                "reason": "top panel without a heading"})
        if med_h > 0:
            tall = [ln for ln in lines
                    if ln["rect"][1] < frame_top + 0.40 * frame_h
                    and (ln["rect"][3] - ln["rect"][1]) >= 2.0 * med_h]
            for ln in tall[:3]:
                regions.append({
                    "kind": "BRAND",
                    "rect": [round(v, 1) for v in ln["rect"]],
                    "confidence": 0.5,
                    "reason": "display-size lettering in the top panel"})
    except Exception:
        pass
    def _x_overlap_frac(rect: tuple, span: tuple) -> float:
        x0, _, x1, _ = rect
        width = max(x1 - x0, 1e-6)
        inter = max(0.0, min(x1, span[1]) - max(x0, span[0]))
        return inter / width

    # Column-aware panels: an anchor is terminated only by a LATER
    # anchor in the SAME column (x-overlap), and members must overlap
    # the anchor column. Two sections sharing one row (left ingredient
    # column, right MRP column) therefore keep their own panels instead
    # of one squeezing out the other.
    row_tol = 6.0
    try:
        _hs = sorted(ln["rect"][3] - ln["rect"][1] for ln in lines)
        if _hs:
            row_tol = max(4.0, _hs[len(_hs) // 2] * 0.5)
    except Exception:
        pass
    for k, (_ord, section, ln) in enumerate(anchors):
        top = ln["rect"][1]
        ax = (ln["rect"][0], ln["rect"][2])
        bottom = frame_bottom
        for (_, _, other) in anchors[k + 1:]:
            if other["rect"][1] > top + row_tol and _x_overlap_frac(
                    other["rect"], ax) >= 0.3:
                bottom = other["rect"][1]
                break
        if bottom - top < 4:
            continue
        members = [m for m in lines
                   if m["rect"][1] >= top - row_tol
                   and m["rect"][1] < bottom
                   and _x_overlap_frac(m["rect"], ax) >= 0.3]
        if not members:
            continue
        x0 = min(m["rect"][0] for m in members)
        x1 = max(m["rect"][2] for m in members)
        density = sum(len(m["text"]) for m in members) / max(
            1.0, (x1 - x0) * (bottom - top)) * 1000.0
        conf = round(min(0.9, 0.45 + min(len(members) / 4.0, 1.0) * 0.30
                         + min(density / 40.0, 1.0) * 0.15), 3)
        regions.append({"kind": section,
                        "rect": [round(x0, 1), round(top, 1),
                                 round(x1, 1), round(bottom, 1)],
                        "confidence": conf,
                        "reason": f"heading-anchored panel "
                                  f"({len(members)} lines)"})
    # Table structure can only promote (never invent) a NUTRITION panel.
    # Boxes below are in STAGE coordinates, so stage_size is passed for
    # the stage->input mapping inside find_table_rect.
    if gray_small is not None and stage_size is not None:
        table = find_table_rect(
            gray_small,
            stage_size=stage_size,
            boxes=[{"text": ln["text"],
                    "box": [[ln["rect"][0], ln["rect"][1]],
                            [ln["rect"][2], ln["rect"][1]],
                            [ln["rect"][2], ln["rect"][3]],
                            [ln["rect"][0], ln["rect"][3]]]}
                   for ln in lines])
        if table.get("found"):
            sw = (stage_size[0] / gray_small.shape[1]
                  if hasattr(gray_small, "shape") else 1.0)
            sh = (stage_size[1] / gray_small.shape[0]
                  if hasattr(gray_small, "shape") else 1.0)
            tr = table["rect"]
            regions.append({"kind": "NUTRITION",
                            "rect": [round(tr[0] * sw, 1),
                                     round(tr[1] * sh, 1),
                                     round(tr[2] * sw, 1),
                                     round(tr[3] * sh, 1)],
                            "confidence": table["confidence"],
                            "reason": "table structure + numeric density"})
            notes.append("table evidence supports a nutrition panel")
    notes.append(f"{len(regions)} layout panels from {len(lines)} lines")
    return {"regions": regions, "notes": notes}


def choose_variant(purpose: str, quality: dict[str, Any] | None
                   ) -> list[str]:
    """Quality-driven targeted-variant order (Part H).

    Always a permutation drawn from ("orig", "up", "adapt"); the caller
    still runs at most INGREDIENT_VARIANT_BUDGET with early abort, so
    the call budget never grows — only the ORDER adapts to the image.
    "adapt" needs cv2; without it the order silently drops to ("orig",
    "up") via the service's prep fallback.
    """
    quality = quality or {}
    sharp = (quality.get("sharpness") or {}).get("grade", "FAIR") \
        if isinstance(quality.get("sharpness"), dict) \
        else quality.get("sharpness", "FAIR")
    bright = (quality.get("brightness") or {}).get("grade", "FAIR") \
        if isinstance(quality.get("brightness"), dict) \
        else quality.get("brightness", "FAIR")
    order = ["orig", "up", "adapt"]
    if sharp == "POOR":
        order = ["up", "adapt", "orig"]  # upscale+sharpen leads on blur
    elif bright == "POOR":
        order = ["adapt", "up", "orig"]  # thresholding leads on bad light
    if not cv2_available():
        order = [v for v in order if v != "adapt"]
    if purpose != "ingredients" and "adapt" in order:
        # Small-print regions keep their single proven prep first;
        # "adapt" stays available as the bounded second look.
        order = ["smallprint", *[v for v in order if v != "smallprint"]]
    # De-duplicate, preserve order.
    seen: list[str] = []
    for v in order:
        if v not in seen:
            seen.append(v)
    return seen


def prepare_variant(crop_pil: Any, variant: str) -> Any:
    """Render one targeted variant as an RGB numpy array.

    "orig"/"up" delegate to the legacy PIL pipeline (byte-identical
    behaviour); "adapt" is the new OpenCV adaptive-threshold path for
    unevenly lit small print, with a legacy-style fallback when cv2 is
    missing. Raises OcrError for unknown variants (same contract).
    """
    from app.services.ocr.base import OcrError
    from app.services.ocr.service import _prep_ingredient_variant

    if variant in ("orig", "up", "thresh", "sharp"):
        return _prep_ingredient_variant(crop_pil, variant)
    if variant == "smallprint":
        from app.services.ocr.service import _prep_region

        return _prep_region(crop_pil, "smallprint")
    if variant == "adapt":
        arr = _adaptive_variant(crop_pil)
        if arr is not None:
            return arr
        # Graceful fallback: legacy contrast path, same contract.
        return _prep_ingredient_variant(crop_pil, "up")
    raise OcrError(f"unknown ingredient variant: {variant}")


def _adaptive_variant(crop_pil: Any) -> Any | None:
    """Adaptive-Gaussian-threshold variant for uneven lighting."""
    cv2 = _cv2()
    if cv2 is None:
        return None
    try:
        import numpy as np

        from PIL import Image as _Image

        gray = np.asarray(crop_pil.convert("L"))
        h, w = gray.shape[:2]
        if min(h, w) < 12:
            return None
        block = max(11, (min(h, w) // 12) | 1)
        if block % 2 == 0:
            block += 1
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, block, 4)
        # Ink-fraction sanity: a usable text mask is neither ~all white
        # nor ~all black; otherwise return None (caller falls back).
        ink = float((thresh < 128).mean())
        if not 0.01 <= ink <= 0.6:
            return None
        return np.asarray(_Image.fromarray(thresh).convert("RGB"))
    except Exception:
        return None
