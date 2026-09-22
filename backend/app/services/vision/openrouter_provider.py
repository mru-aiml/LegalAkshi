"""OpenRouter vision provider (OpenAI-compatible, server-side only).

Uses POST {base_url}/chat/completions on the FREE Qwen vision model
with the actual package images as base64 image_url content parts.
Structured JSON is requested via response_format + a strict prompt and
every candidate is validated through the shared vision schema —
unparseable output becomes NEEDS_REVIEW, never a detection.

No third-party SDK (stdlib + urllib). The key comes from
OPENROUTER_API_KEY and is never logged, stored, or exposed.
Only the exact free model is ever used (see provider free-only guard).
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
from app.services.vision.gemini_provider import (
    _normalize_item,
    _strip_code_fences,
)
from app.services.vision.schemas import validate_vision_candidate

log = logging.getLogger("legalakshi.vision.openrouter")

DEFAULT_MODEL = "google/gemma-4-31b-it:free"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_FORBIDDEN_PHRASES = ("probably", "looks like", "likely ",
                       "maybe ", "possibly ")


class OpenRouterVisionProvider:
    name = "openrouter"

    def __init__(self, api_key: str = "", model: str = "",
                 base_url: str = "") -> None:
        self._api_key = api_key or ""
        self._model = model or DEFAULT_MODEL
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    def available(self) -> bool:
        return bool(self._api_key and self._model)

    def health_check(self, timeout_s: float = 10.0) -> dict[str, Any]:
        """ONE minimal text-only liveness probe (no package images).

        Short timeout; any transport, auth, or shape failure returns
        ok=False with a sanitised reason (never key material).
        """
        import time as _time

        if not self.available():
            return {"ok": False,
                    "reason": "openrouter provider not configured "
                    "(key/model)"}
        body = {"model": self._model,
                "messages": [{"role": "user",
                              "content": "Reply with exactly: OK"}],
                "max_tokens": 8}
        req = urllib.request.Request(
            self._base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST")
        t0 = _time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode())
        except Exception as exc:
            return {"ok": False, "reason": _sanitize_error(exc)}
        _ = round((_time.perf_counter() - t0) * 1000, 1)
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except Exception:
            return {"ok": False,
                    "reason": "malformed health response (no text)"}
        if "OK" in str(text).upper():
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
            raise VisionError("openrouter provider not configured "
                              "(key/model)")
        panels = list(image) if isinstance(image, (list, tuple)) else [image]
        panels = [p for p in panels if p is not None][:8]
        if not panels:
            raise VisionError("openrouter provider received no image")
        prompt = self._prompt(requested_fields, ocr_candidates,
                              layout_context, n_panels=len(panels))
        try:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": prompt}]
            for panel in panels:
                # Original colour photos, ≤1600px working copies —
                # never thresholded OCR variants.
                image_b64 = _image_to_jpeg_b64(panel, max_dim=1600)
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + image_b64}})
        except Exception as exc:
            raise VisionError(f"cannot encode image: {exc}")
        body = {"model": self._model,
                "messages": [{"role": "user", "content": content}],
                "response_format": {"type": "json_object"},
                "max_tokens": 2048}
        url = self._base_url + "/chat/completions"
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers=self._headers(),
            method="POST")
        t0 = time.perf_counter()
        http_status: int | None = None
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                http_status = getattr(resp, "status", None)
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode(errors="replace")[:400]
            except Exception:
                detail = ""
            raise VisionError(
                f"openrouter HTTP {exc.code}: {detail or exc.msg}")
        except Exception as exc:
            raise VisionError(
                f"openrouter request failed: {type(exc).__name__}")
        ms = round((time.perf_counter() - t0) * 1000, 1)
        log.debug("openrouter vision call model=%s fields=%d in %sms "
                  "(key never logged)", self._model,
                  len(requested_fields or []), ms)
        self._last_http_status = http_status
        try:
            return self._parse(payload, requested_fields)
        finally:
            self._last_http_status = None

    def _headers(self) -> dict[str, str]:
        """Request headers (key travels here; never logged)."""
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}"}

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
                "may appear on only one panel: report which image holds "
                "it in source_image/evidence_location and never merge "
                "text across panels into one value. ")
        targeted = ""
        if layout_context and layout_context.get("pass") == \
                "targeted-second":
            targeted = (
                "TARGETED SECOND PASS: these crops centre on the "
                "declaration/ingredient areas. Ignore values from "
                "unrelated package regions unless they establish the "
                "identity of the declaration block. Search the entire "
                "supplied image set before returning NOT_VISIBLE. ")
        return (
            "You are performing AI-assisted OCR and visual text "
            "extraction on an Indian packaged food product. PRIMARY "
            "OBJECTIVE: extract information that is VISIBLY PRESENT on "
            "the supplied package images. This is NOT a legal "
            "compliance decision. Do NOT determine whether the product "
            "is compliant. Do NOT invent, infer, normalize, "
            "autocomplete, or guess values. "
            "For every requested field: "
            "1. Inspect ALL supplied package images. "
            "2. Search the entire package before declaring a field "
            "missing. "
            "3. Search specifically for the field's declaration area. "
            "4. Read printed, stamped, embossed, handwritten, and "
            "overprinted text. "
            "5. Preserve the exact visible characters. "
            "6. Preserve capitalization where visible. "
            "7. Preserve punctuation where visible. "
            "8. Preserve decimal points and separators. "
            "9. Never substitute similar-looking numbers from another "
            "area. "
            "10. Never copy a value from another field. "
            "11. If text cannot be read reliably, return UNREADABLE. "
            "12. If the field is not visible anywhere, return "
            "NOT_VISIBLE. "
            "13. If multiple conflicting values are visible, return "
            "AMBIGUOUS and provide evidence for each. "
            "14. Never manufacture a value to complete a field. "
            "HIGH PRIORITY DECLARATION BLOCK. Search especially for: "
            "MRP (MRP, Maximum Retail Price, rupee sign, Rs., Rs, "
            "M.R.P., MRP inclusive of all taxes, Retail sale price); BATCH / LOT (Batch "
            "No., Batch Number, Batch, Lot No., LOT, Lot Number); "
            "MANUFACTURING (MFD, MFG, Mfg., Manufactured, Manufactured "
            "On, Date of "
            "Manufacture, Manufacturing Date); PACKING (PKD, Packed, "
            "Packed On, Packed Date, Date of Packing, Packing Date); "
            "EXPIRY (EXP, Exp., Expiry, Expires, Expiry Date, Date of "
            "Expiry); BEST BEFORE (Best Before, Best Before Use, Use "
            "Before). Treat these labels as anchors. The characters "
            "immediately associated with these labels are much more "
            "important than unrelated numbers elsewhere on the package. "
            "IMPORTANT ANTI-CONFUSION RULES. NEVER treat as "
            "MRP: FSSAI licence number, customer care number, "
            "telephone number, barcode digits, nutrition values, "
            "serving size, quantity, product code, SKU, batch number. "
            "NEVER treat as batch/lot: FSSAI licence number, phone "
            "number, customer care number, barcode, product code, "
            "manufacturing licence, random numeric text. NEVER treat "
            "as dates: nutrition values, phone numbers, licence "
            "numbers, batch numbers, barcode digits, unrelated "
            "addresses. NEVER use FSSAI number, phone number, PIN "
            "code, barcode, nutrition value, serving size, GST number "
            "or address number as MRP or batch unless the declaration "
            "label explicitly indicates that relationship. "
            "IMPORTANT DATE RULE: keep date_of_manufacture, "
            "date_of_packing, expiry_date and best_before separate. Do "
            "NOT convert one into another. Do NOT calculate expiry "
            "from manufacturing date. Do NOT calculate manufacturing "
            "date from packing date. Do NOT convert \"best before 12 "
            "months\" into a calendar expiry date. Only return an "
            "expiry_date when an actual expiry date is visibly "
            "present. Only return date_of_packing when an actual "
            "packing/PKD date is visibly present. Only return "
            "date_of_manufacture when an actual MFD/MFG/manufacturing "
            "date is visibly present. INGREDIENTS: extract the visible "
            "ingredients declaration VERBATIM. Do not summarize it. Do "
            "not rewrite it. Do not improve grammar. Do not turn OCR "
            "fragments into plausible words. Do not add ingredients "
            "that are not visible. Preserve the visible ordering. If "
            "part of the ingredients declaration is unreadable, mark "
            "that portion as uncertain rather than inventing text. Do "
            "not mix ingredients with directions for use, dosage, "
            "nutrition information, marketing claims, warnings, "
            "storage instructions, manufacturer information, "
            "customer-care text, or certification statements. "
            "HANDWRITING/STAMPS: handwritten and stamped declarations "
            "are HIGH PRIORITY. Read them exactly as visible. If a "
            "handwritten MRP appears to be \"460\", return \"460\". If "
            "it appears to be \"460\" but the last digit cannot "
            "reliably be distinguished, return the visible uncertain "
            "value with low confidence or UNREADABLE. Never use a "
            "different printed number elsewhere on the package as a "
            "replacement. If the image appears to show 08/26, do not "
            "change it to 06/26 unless the actual visible character is "
            "clearly 6. If a character cannot be reliably read, return "
            "the visible portion and mark the field AMBIGUOUS rather "
            "than inventing the missing character. EVIDENCE: every "
            "extracted field must include "
            "value, status, confidence, evidence_location and "
            "source_image. Confidence must describe READABILITY ONLY, "
            "never legal correctness. Do not give 0.95 or 0.99 "
            "confidence merely because a value looks plausible. High "
            "confidence is allowed only when the characters are "
            "clearly visible. If handwriting is difficult, use low "
            "confidence. If text cannot be read, return UNREADABLE. If "
            "the field is not present, return NOT_VISIBLE. "
            "MULTI-IMAGE RECONCILIATION: all supplied images belong to "
            "the SAME package. Use all images together. If front and "
            "back show different fields, combine them. If the same "
            "field appears in multiple images, compare the readings "
            "and prefer the clearest image. Do not merge unrelated "
            "text. If two images genuinely conflict, return the value "
            "as AMBIGUOUS and include both visible readings in "
            "evidence. SOURCE: AI_PRINTED for clearly "
            "printed text, AI_HANDWRITTEN for handwritten/stamped "
            "text, AI when the modality cannot be distinguished. "
            "Return JSON only, with a \"fields\" object holding one "
            "entry per requested field name plus vegetarian_symbol. "
            "Each entry has value, confidence, readability_status "
            "(CLEAR, AMBIGUOUS, UNREADABLE or NOT_VISIBLE), "
            "evidence_location and source_image. No prose. "
            f"{targeted}{panels_hint}"
            f"Requested fields: {fields}. {ocr_hint} {layout_hint}"
        )

    def _parse(self, payload: dict[str, Any],
               requested_fields: list[str]) -> list[dict[str, Any]]:
        shape_hint = "?"
        try:
            choices = payload["choices"]
            content = choices[0]["message"]["content"] or ""
            if isinstance(content, list):
                # Some models return content blocks instead of a string.
                content = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict))
            text = content
            shape_hint = (f"{type(content).__name__}:"
                          f"{len(content) if isinstance(content, str) else 0}")
            items = _strip_code_fences(text)
            items = json.loads(items)
        except Exception:
            # Unparseable model output: every requested field NEEDS_REVIEW.
            return [{"field": f, "value": None, "unit": None,
                     "status": "NEEDS_REVIEW", "confidence": 0.0,
                     "evidence_text": "unparseable model output",
                     "bbox": None, "image_id": None, "source": "vision"}
                    for f in requested_fields or []]
        # Object shape {fields: {...}, vegetarian_symbol: {...}} or a
        # bare array — both reduce to per-field items below. A flat
        # field map ({"mrp": {...}, ...}) is also accepted: any dict
        # value carrying value/status keys is one field object.
        if isinstance(items, dict) and not any(
                k in ("field", "value", "status") for k in items):
            merged: list[dict[str, Any]] = []
            fields = items.get("fields")
            if isinstance(fields, dict):
                for name, entry in fields.items():
                    if isinstance(entry, dict):
                        merged.append({"field": name, **entry})
            elif isinstance(fields, list):
                merged.extend(
                    [e for e in fields if isinstance(e, dict)])
            for key in ("vegetarian_symbol", "symbol", "evidence"):
                entry = items.get(key)
                if isinstance(entry, dict) and "field" not in entry:
                    entry = {"field": "vegetarian_symbol", **entry}
                    merged.append(entry)
                elif isinstance(entry, list):
                    merged.extend(
                        [e for e in entry if isinstance(e, dict)])
            if not merged:
                for name, entry in items.items():
                    if isinstance(entry, dict) and any(
                            k in entry for k in
                            ("value", "status", "confidence")):
                        merged.append({"field": name, **entry})
            items = merged
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
        if not out:
            # Observability for empty/unparseable parses (§12): HTTP
            # status, model, response type, content length and a short
            # sanitised content head. Never key material, never image
            # bytes. Debug level so routine traffic stays quiet.
            try:
                head = ""
                if isinstance(shape_hint, str) and \
                        shape_hint.startswith("str:"):
                    head = text[:200].replace("\n", " ")
                log.debug("openrouter parse model=%s http=%s fields=%d "
                          "payload_keys=%s content=%s head=%r",
                          self._model,
                          getattr(self, "_last_http_status", None),
                          len(requested_fields or []),
                          sorted(payload.keys()) if isinstance(
                              payload, dict) else type(payload).__name__,
                          shape_hint, head)
            except Exception:
                pass
        return out


def _sanitize_error(exc: BaseException) -> str:
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
