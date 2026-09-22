"""Gemini vision provider (optional, server-side only).

Uses the Gemini REST generateContent endpoint with a strict JSON-only
instruction and validates every candidate through schemas.py. Any
unparseable / non-conforming output becomes NEEDS_REVIEW, never a
detection. No third-party SDK required (stdlib + urllib); API key comes
from LEGALAKSHI_VISION_API_KEY and is never logged or exposed.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from app.services.vision.base import VisionError
from app.services.vision.schemas import validate_vision_candidate

log = logging.getLogger("legalakshi.vision.gemini")

# Default model. Configurable via LEGALAKSHI_VISION_MODEL — never rely
# on a hard-coded model elsewhere; the provider always reports the
# effective model it was constructed with.
DEFAULT_MODEL = "gemini-3.8-flash"

_FORBIDDEN_PHRASES = ("probably", "looks like", "likely ",
                      "maybe ", "possibly ")


class GeminiVisionProvider:
    name = "gemini"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or ""
        self._model = model or DEFAULT_MODEL

    @property
    def model(self) -> str:
        return self._model

    def available(self) -> bool:
        return bool(self._api_key and self._model)

    def health_check(self, timeout_s: float = 10.0) -> dict[str, Any]:
        """ONE minimal text-only liveness probe (Stage 2C §2).

        No package images are sent. Short timeout; any transport,
        auth, or shape failure returns ok=False with a sanitised
        reason (never key material, headers, or payloads).
        """
        import time as _time

        if not self.available():
            return {"ok": False,
                    "reason": "gemini provider not configured "
                    "(key/model)"}
        body = {"contents": [{"parts": [
            {"text": "Reply with exactly: OK"}]}],
            "generationConfig": {"temperature": 0.0,
                                 "maxOutputTokens": 8}}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self._model}:generateContent")
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self._api_key},
            method="POST")
        t0 = _time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode())
        except Exception as exc:
            return {"ok": False, "reason": _sanitize_health_error(exc)}
        _ = round((_time.perf_counter() - t0) * 1000, 1)
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
        except Exception:
            return {"ok": False,
                    "reason": "malformed health response (no text)"}
        if "OK" in text.upper():
            return {"ok": True, "reason": "model replied OK"}
        return {"ok": False,
                "reason": "unexpected health response shape"}

    def extract_package_fields(
        self,
        image: Any,
        requested_fields: list[str],
        ocr_candidates: list[dict[str, Any]] | None = None,
        layout_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.available():
            raise VisionError("gemini provider not configured (key/model)")
        # Single panel or several panels of the SAME package (front+back):
        # every entry is an original colour photo, never a processed
        # variant. Capped so one call cannot balloon into N uploads.
        panels = list(image) if isinstance(image, (list, tuple)) else [image]
        panels = [p for p in panels if p is not None][:8]
        if not panels:
            raise VisionError("gemini provider received no image")
        prompt = self._prompt(requested_fields, ocr_candidates,
                              layout_context, n_panels=len(panels))
        try:
            # Downscaled working copies (long edge <= 1600px): small
            # MRP/batch/date stamps stay legible while upload bytes and
            # model latency stay bounded. The ORIGINAL colour photographs
            # are encoded — never thresholded/grayscale OCR variants —
            # because logos, colours, the veg symbol and handwriting
            # must stay visible.
            parts: list[dict[str, Any]] = [{"text": prompt}]
            for panel in panels:
                image_b64 = _image_to_jpeg_b64(panel, max_dim=1600)
                parts.append({"inline_data": {
                    "mime_type": "image/jpeg", "data": image_b64}})
        except Exception as exc:
            raise VisionError(f"cannot encode image: {exc}")
        body = {"contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.0,
                                     "responseMimeType": "application/json",
                                     # Bounded output: one compact object
                                     # per requested field; caps latency.
                                     "maxOutputTokens": 2048}}
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self._model}:generateContent")
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self._api_key},
            method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # Surface the REAL provider failure (status + body snippet)
            # so timeouts/auth/model issues are diagnosable instead of
            # collapsing into a bare fallback. Key material never
            # appears here (key travels in the request header only).
            try:
                detail = exc.read().decode(errors="replace")[:400]
            except Exception:
                detail = ""
            raise VisionError(
                f"gemini HTTP {exc.code}: {detail or exc.msg}")
        except Exception as exc:
            raise VisionError(f"gemini request failed: {type(exc).__name__}")
        ms = round((time.perf_counter() - t0) * 1000, 1)
        log.debug("gemini vision call fields=%d in %sms (key never logged)",
                  len(requested_fields or []), ms)
        return self._parse(payload, requested_fields)

    def _prompt(self, requested_fields: list[str],
                ocr_candidates: list[dict[str, Any]] | None,
                layout_context: dict[str, Any] | None,
                n_panels: int = 1) -> str:
        ocr_hint = ""
        if ocr_candidates:
            bits = [f"{c.get('field')}={c.get('value')}"
                    for c in ocr_candidates[:20]]
            ocr_hint = "OCR candidates (may be wrong): " + "; ".join(bits)
        layout_hint = ""
        if layout_context:
            layout_hint = "Layout context: " + json.dumps(layout_context)[:800]
        fields = ", ".join(requested_fields or [])
        panels_hint = ""
        if n_panels > 1:
            panels_hint = (
                f"The {n_panels} attached images show different panels "
                "of the SAME package (e.g. front, then back). A field "
                "may appear on only one panel: report the panel in "
                "evidence_location and never merge text across panels "
                "into one value. ")
        return (
            "You are a visual extraction component for food-package "
            "inspection. Use the package images as the primary visual "
            "evidence and OCR text as supporting evidence only. Extract "
            "ONLY information explicitly visible in the images. Never "
            "infer, guess, calculate, or fill in missing values. If a "
            "field is not visible or cannot be read confidently, return "
            "value null with status NOT_VISIBLE or UNREADABLE — never "
            "hallucinate. You NEVER judge legal compliance — only read "
            "what is printed, stamped, or hand-written on the pack. "
            "Return ONLY a JSON array with one object per requested "
            "field. Each object must have exactly these keys: "
            "field, value (string or null), status (one of FOUND, "
            "NOT_VISIBLE, UNREADABLE, AMBIGUOUS), confidence (0-100), "
            "evidence_location (where on the pack: front, back, label, "
            "bottom, other — or null), reason (short explanation), "
            "handwritten (true when the value is hand-written, "
            "hand-stamped, or inkjet-printed, else false). "
            "Small declaration block (read carefully, character by "
            "character): MRP is the numeric price only — look for "
            "\"MRP\", \"Maximum Retail Price\", \"Max Retail Price\", "
            "\"Rs.\", \"Rs\": \"MRP Rs.: 460/-\" gives value \"460\". "
            "Batch looks for \"Batch\", \"Batch No\", \"Lot\", \"Lot "
            "No\". Dates look for \"MFD\", \"Mfg\", \"Manufactured\", "
            "\"Date of Manufacturing\", \"Date of Packing\", \"PKD\" "
            "(packing date), \"Expiry\", \"EXP\", \"Use By\", \"Best "
            "Before\" (expiry/best-before). Preserve dates exactly as "
            "printed. Keep batch number, packing/manufacturing date and "
            "best-before/expiry strictly separate: a packing date is "
            "NOT a manufacturing date and a \"Best Before 6 months\" "
            "statement is NOT an expiry date (return the statement "
            "text). Never take nutrition-table numbers, the FSSAI "
            "licence number, barcodes, or customer-care/phone numbers "
            "as MRP, batch, or date values. "
            "Ingredients: locate the complete INGREDIENTS / INGREDIENTS "
            "LIST section and transcribe its exact wording — never "
            "summarise, never complete it from product knowledge, never "
            "treat nutrition values as ingredients. "
            "vegetarian_symbol value is one of VEGETARIAN, "
            "NON_VEGETARIAN, NOT_DETECTED, AMBIGUOUS (green "
            "square+circle vs brown/red mark). "
            f"{panels_hint}"
            f"Requested fields: {fields}. {ocr_hint} {layout_hint}"
        )

    def _parse(self, payload: dict[str, Any],
               requested_fields: list[str]) -> list[dict[str, Any]]:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            items = _strip_code_fences(text)
            items = json.loads(items)
        except Exception:
            # Unparseable model output: every requested field NEEDS_REVIEW.
            return [{"field": f, "value": None, "unit": None,
                     "status": "NEEDS_REVIEW", "confidence": 0.0,
                     "evidence_text": "unparseable model output",
                     "bbox": None, "image_id": None, "source": "vision"}
                    for f in requested_fields or []]
        if isinstance(items, dict):
            items = items.get("candidates", items.get("fields", []))
        out: list[dict[str, Any]] = []
        for raw in items if isinstance(items, list) else []:
            if not isinstance(raw, dict):
                continue
            raw = _normalize_item(raw)
            if any(p in str(raw.get("value", "")).lower()
                   for p in _FORBIDDEN_PHRASES):
                raw = {**raw, "status": "NEEDS_REVIEW"}
            cand = validate_vision_candidate(raw)
            if cand is not None:
                out.append(cand)
        return out


# Prototype response shape (STRICT JSON per field) -> internal schema.
# Legacy shapes (status DETECTED/..., confidence 0-1, detail_status)
# keep working: anything unrecognised falls through untouched and the
# strict validator decides.
_NEW_STATUS_TO_INTERNAL = {
    "FOUND": "DETECTED",
    "NOT_VISIBLE": "NOT_DETECTED",
    "UNREADABLE": "NEEDS_REVIEW",
    "AMBIGUOUS": "NEEDS_REVIEW",
}

# Spec field aliases -> internal candidate field names.
_FIELD_ALIASES = {
    "vegetarian_symbol": "veg_nonveg",
    "veg_symbol": "veg_nonveg",
}


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise one model item to the internal candidate schema."""
    item = dict(raw)
    field = str(item.get("field", "") or "").strip()
    if field in _FIELD_ALIASES:
        item["field"] = _FIELD_ALIASES[field]
    status = str(item.get("status", "") or "").strip().upper()
    if status in _NEW_STATUS_TO_INTERNAL:
        item["status"] = _NEW_STATUS_TO_INTERNAL[status]
        if status == "NOT_VISIBLE":
            item["value"] = None
    # Confidence may arrive 0-100 (spec) or 0-1 (legacy): normalise.
    try:
        conf = float(item.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf > 1.0:
        conf = conf / 100.0
    item["confidence"] = max(0.0, min(1.0, conf))
    # reason doubles as visible-text evidence when evidence_text absent.
    if item.get("evidence_text") in (None, "") and item.get("reason"):
        item["evidence_text"] = str(item["reason"])[:300]
    if isinstance(item.get("handwritten"), str):
        item["handwritten"] = item["handwritten"].strip().lower() in (
            "true", "1", "yes", "handwritten")
    return item


def _strip_code_fences(text: str) -> str:
    """Remove a single markdown code fence; unparseable stays unparseable.

    Exactly one safeparse attempt is allowed (spec §14): fence-strip,
    then json.loads. Anything else falls back to OCR via the caller.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped


def _sanitize_health_error(exc: BaseException) -> str:
    """Sanitised transport/auth failure (no key, headers, or payloads)."""
    name = type(exc).__name__
    try:
        import urllib.error as _urlerr

        if isinstance(exc, _urlerr.HTTPError):
            return f"provider HTTP {exc.code} ({name})"
    except Exception:
        pass
    return f"provider request failed ({name})"


def _image_to_jpeg_b64(image: Any, max_dim: int = 1600) -> str:
    from PIL import Image

    if hasattr(image, "tobytes") and hasattr(image, "shape"):
        img = Image.fromarray(image)
    elif isinstance(image, bytes):
        img = Image.open(io.BytesIO(image)).convert("RGB")
    else:
        img = image.convert("RGB")
    # Downscale-only: huge camera photos shrink (faster upload +
    # inference); small label crops are never upscaled.
    w, h = img.size
    if max(w, h) > max_dim > 0:
        scale = max_dim / max(w, h)
        img = img.resize((max(1, int(w * scale)),
                          max(1, int(h * scale))))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()
