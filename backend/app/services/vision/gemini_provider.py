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
            "generationConfig": {"maxOutputTokens": 8}}
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
                # generateContent parameters that the live API accepts:
                # capped structured JSON output. NOTE (live-probed):
                # `thinking_level` is REJECTED by generateContent
                # ("Unknown name at 'generation_config'") and must NOT
                # be sent; low-reasoning behavior comes from the prompt
                # (verbatim extraction, no inference). Deprecated knobs
                # (temperature, top_p, top_k, candidate_count,
                # thinking_budget) are never sent either.
                "generationConfig": {"responseMimeType": "application/json",
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
        declaration_focus = ""
        if layout_context and layout_context.get("pass") == \
                "targeted-second":
            declaration_focus = (
                "DECLARATION SECOND PASS: ignore values from unrelated "
                "package regions unless they are needed to establish "
                "the identity of the declaration block. Search the "
                "entire supplied image set before returning "
                "NOT_VISIBLE. Pay special attention to small printed, "
                "stamped, embossed, and handwritten declaration text. "
                "Never use FSSAI licence numbers, telephone numbers, "
                "barcodes, nutrition values, serving sizes, or "
                "addresses as substitutes for declaration values. ")
        return (
            "You are performing AI-assisted OCR on an Indian packaged "
            "food product. Your primary task is VERBATIM VISUAL TEXT "
            "EXTRACTION. You are NOT performing legal compliance "
            "analysis. You are NOT deciding whether the package is "
            "legally compliant. You are NOT allowed to invent, infer, "
            "reconstruct, autocomplete, normalize, or guess text that "
            "is not visibly supported by the supplied images. "
            "For every requested field: "
            "1. Inspect ALL supplied package images. "
            "2. Search every image for the field. "
            "3. Prioritize the package's declaration/information block. "
            "4. Read printed, stamped, embossed, and handwritten text. "
            "5. Preserve the exact visible characters. "
            "6. Preserve the visible number/date formatting when "
            "readable. "
            "7. Never copy a value from an unrelated part of the "
            "package. "
            "8. Never substitute a similar-looking number from another "
            "field. "
            "9. Never infer a missing digit. "
            "10. Never use general knowledge to complete a partially "
            "visible value. "
            "11. If the field is visible but unclear, return "
            "UNREADABLE. "
            "12. If multiple conflicting values are visible, return "
            "AMBIGUOUS and report all relevant evidence. "
            "13. If the field cannot be found, return NOT_VISIBLE. "
            "14. Every extracted value must include evidence_location. "
            "15. Every extracted value must include confidence. "
            "16. Handwritten/stamped values must be explicitly marked "
            "handwritten=true when applicable. "
            "CRITICAL RULE: a wrong value is worse than a missing "
            "value. If you cannot clearly read a character, DO NOT "
            "GUESS IT. "
            "DECLARATION BLOCK — HIGHEST PRIORITY. Search specifically "
            "for the declaration/price/date block. Look for MRP (MRP, "
            "Maximum Retail Price, Max Retail Price, Rs., Rs, M.R.P., "
            "Retail Price, MRP inclusive), BATCH (Batch, Batch No., "
            "Batch Number, Batch/Lot, Lot, Lot No., Lot Number, LOT), "
            "MANUFACTURING / PACKING "
            "(MFD, MFG, Mfg., Manufactured, Manufacturing Date, Date "
            "of Manufacture, PKD, Pkd., Packed, Packed On, Packing "
            "Date, Date of Packing), EXPIRY (EXP, Exp., Expiry, Expires, "
            "Expiry Date, Date of Expiry), BEST BEFORE (Best Before, "
            "Best Before X Months/Days/Years, Use Before, BB). The "
            "label and the value are separate: return only the value "
            "belonging to the label, never the label words themselves "
            "(a lone \"Number\" is not a batch number). Do NOT confuse: FSSAI "
            "licence number with batch number; customer care or phone "
            "numbers with MRP; phone number with batch number; "
            "nutrition values with MRP; serving size with quantity; net "
            "quantity with serving size; best-before duration with "
            "expiry date; manufacturing address with manufacturer name; "
            "product code with batch number; barcode digits with batch "
            "number; GST/tax numbers with batch number; licence numbers "
            "with batch number; nutritional table numbers with MRP; "
            "dates in unrelated promotional text with "
            "manufacturing/expiry dates. "
            "MRP EXTRACTION RULE: MRP must come from the price "
            "declaration (MRP, Rs, Maximum Retail Price). Do not "
            "interpret nutritional values, serving size, FSSAI number, "
            "telephone number, barcode, or product code as MRP. If MRP "
            "is handwritten or stamped, preserve it exactly as visible, "
            "mark handwritten=true, return evidence_location and "
            "confidence, and do not convert uncertain characters into "
            "a different number. If it visibly appears to be Rs.46O "
            "where the final character cannot confidently be "
            "distinguished between 0 and O, return the visible "
            "representation and mark the field UNREADABLE or AMBIGUOUS "
            "rather than silently changing it. "
            "BATCH / LOT EXTRACTION RULE: search specifically for "
            "Batch, Batch No., Batch Number, Lot, Lot No. Do not use "
            "FSSAI licence, phone number, barcode, product code, or "
            "nutrition values as batch number. Batch may be printed, "
            "stamped, embossed, or handwritten — preserve exact visible "
            "characters. "
            "PACKING / MANUFACTURING DATE: search specifically for MFD, "
            "MFG, PKD, Manufactured, Packed, Packed On, Date of "
            "Manufacture, Date of Packing. If a date is visible but its "
            "label cannot be established, do not automatically classify "
            "it as MFD/PKD — return AMBIGUOUS if the evidence does not "
            "establish the field. Preserve the visible date. "
            "EXPIRY DATE: search specifically for EXP, Expiry, Expires, "
            "Date of Expiry. Do NOT convert a \"Best Before 6 Months\" "
            "statement into an expiry date unless an explicit expiry "
            "date is visibly printed. If only \"Best Before 6 Months\" "
            "is visible: best_before holds that statement and "
            "expiry_date is NOT_VISIBLE. Do not calculate an expiry "
            "date. "
            "INGREDIENTS (HIGH PRIORITY): find the section explicitly "
            "labelled INGREDIENTS or equivalent declaration. Extract "
            "the visible ingredient list VERBATIM. Do NOT summarize, "
            "paraphrase, clean away meaningful text, invent missing "
            "ingredients, infer ingredients from product type, use "
            "general knowledge, or replace visible terms with "
            "standardized names. Preserve ingredient names, "
            "percentages, parentheses, additives, INS numbers, colours, "
            "flavour names, separators, and visible qualifiers. If part "
            "of the ingredients block is unreadable, return only the "
            "clearly visible text and mark the extraction partial or "
            "UNREADABLE. Do not fabricate the missing portion. Do not "
            "mix ingredients with directions for use, dosage, "
            "nutrition information, marketing claims, warnings, "
            "storage instructions, or manufacturer information. "
            "VEGETARIAN / NON-VEGETARIAN SYMBOL: inspect the actual "
            "package symbol. If clearly visible, return VEGETARIAN or "
            "NON_VEGETARIAN with confidence and evidence_location. If "
            "not clearly visible, return NOT_VISIBLE or AMBIGUOUS. "
            "Never infer vegetarian status from product name, "
            "ingredients, category, package colour, or assumptions "
            "about the product. "
            "EVIDENCE: for EVERY extracted field provide value, status "
            "(FOUND, NOT_VISIBLE, UNREADABLE, AMBIGUOUS), confidence "
            "(0-100), evidence_location (where the value appears, e.g. "
            "\"back image, lower-right declaration block, next to "
            "MRP\"), handwritten (true/false), and source (AI, "
            "AI_HANDWRITTEN, or AI_PRINTED). Confidence must represent "
            "visual readability only — never raise it because a value "
            "seems plausible, the product is known, the field is "
            "expected, or another field holds similar digits. "
            "OUTPUT: return ONLY valid structured JSON matching the "
            "requested schema. No markdown. No explanation. No legal "
            "compliance judgment. No recommendations. No inferred "
            "values. "
            f"{declaration_focus}{panels_hint}"
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

# Spec field aliases -> internal candidate field names. Field names
# are lowercased with spaces/hyphens folded to underscores first.
_FIELD_ALIASES = {
    "vegetarian_symbol": "veg_nonveg",
    "veg_symbol": "veg_nonveg",
    "veg_non_veg_symbol": "veg_nonveg",
    "batch_number": "batch_lot",
    "batch_no": "batch_lot",
    "lot_no": "batch_lot",
    "date_of_manufacture": "manufacturing_date",
    "manufacturing_date": "manufacturing_date",
    "mfd": "manufacturing_date",
    "date_of_packing": "date_of_packing",
    "packing_date": "date_of_packing",
    "pkd": "date_of_packing",
    "expiry_date": "expiry_date",
    "date_of_expiry": "expiry_date",
    "exp": "expiry_date",
    "best_before": "best_before",
    "bestbefore": "best_before",
    "fssai_licence": "fssai_license",
    "fssai_license_no": "fssai_license",
    "fssai_license_number": "fssai_license",
    "consumer_care": "consumer_care",
    "customer_care": "consumer_care",
    "mrp": "mrp",
    "maximum_retail_price": "mrp",
    "max_retail_price": "mrp",
}

_MODALITIES = ("AI", "AI_HANDWRITTEN", "AI_PRINTED")


def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise one model item to the internal candidate schema."""
    item = dict(raw)
    field = str(item.get("field", "") or "").strip().lower()
    field = field.replace(" ", "_").replace("-", "_")
    field = _FIELD_ALIASES.get(field, field)
    item["field"] = field
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
    # Source modality (AI / AI_HANDWRITTEN / AI_PRINTED): accepted when
    # valid, otherwise derived from the handwritten flag. Stored as
    # `modality` so the internal `source: vision` contract is untouched.
    modality = str(item.get("source", "") or "").strip().upper()
    if modality not in _MODALITIES:
        modality = "AI_HANDWRITTEN" if item.get("handwritten") \
            else "AI_PRINTED"
    item["modality"] = modality
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
