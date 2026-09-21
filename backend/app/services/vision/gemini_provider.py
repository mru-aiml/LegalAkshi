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
import urllib.request
from typing import Any

from app.services.vision.base import VisionError
from app.services.vision.schemas import validate_vision_candidate

log = logging.getLogger("legalakshi.vision.gemini")

_FORBIDDEN_PHRASES = ("probably", "looks like", "likely ",
                      "maybe ", "possibly ")


class GeminiVisionProvider:
    name = "gemini"

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self._api_key = api_key or ""
        self._model = model or "gemini-2.0-flash"

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
        if image is None:
            raise VisionError("gemini provider received no image")
        prompt = self._prompt(requested_fields, ocr_candidates,
                              layout_context)
        try:
            image_b64 = _image_to_jpeg_b64(image)
        except Exception as exc:
            raise VisionError(f"cannot encode image: {exc}")
        body = {"contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg",
                             "data": image_b64}},
        ]}], "generationConfig": {"temperature": 0.0,
                                  "responseMimeType": "application/json"}}
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
        except Exception as exc:
            raise VisionError(f"gemini request failed: {type(exc).__name__}")
        ms = round((time.perf_counter() - t0) * 1000, 1)
        log.debug("gemini vision call fields=%d in %sms (key never logged)",
                  len(requested_fields or []), ms)
        return self._parse(payload, requested_fields)

    def _prompt(self, requested_fields: list[str],
                ocr_candidates: list[dict[str, Any]] | None,
                layout_context: dict[str, Any] | None) -> str:
        ocr_hint = ""
        if ocr_candidates:
            bits = [f"{c.get('field')}={c.get('value')}"
                    for c in ocr_candidates[:20]]
            ocr_hint = "OCR candidates (may be wrong): " + "; ".join(bits)
        layout_hint = ""
        if layout_context:
            layout_hint = "Layout context: " + json.dumps(layout_context)[:800]
        fields = ", ".join(requested_fields or [])
        return (
            "You are an extraction component. Use the package image as "
            "the primary visual evidence and OCR text as supporting "
            "evidence. Extract only explicitly visible declarations. "
            "Never infer or guess missing values. "
            "Return ONLY a JSON array; each item must have keys "
            "field, value (string or null), unit (string or null), "
            "status (one of DETECTED, NEEDS_REVIEW, NOT_DETECTED), "
            "confidence (0-1), evidence_text (exact visible text or null). "
            "Rules: return DETECTED only when the value is clearly readable "
            "with supporting visible text; otherwise NEEDS_REVIEW or "
            "NOT_DETECTED with value null. Never guess, never use hedged "
            f"language. Requested fields: {fields}. {ocr_hint} {layout_hint}"
        )

    def _parse(self, payload: dict[str, Any],
               requested_fields: list[str]) -> list[dict[str, Any]]:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
            items = json.loads(text)
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
            if any(p in str(raw.get("value", "")).lower()
                   for p in _FORBIDDEN_PHRASES):
                raw = {**raw, "status": "NEEDS_REVIEW"}
            cand = validate_vision_candidate(raw)
            if cand is not None:
                out.append(cand)
        return out


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


def _image_to_jpeg_b64(image: Any) -> str:
    from PIL import Image

    if hasattr(image, "tobytes") and hasattr(image, "shape"):
        img = Image.fromarray(image)
    elif isinstance(image, bytes):
        img = Image.open(io.BytesIO(image)).convert("RGB")
    else:
        img = image.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()
