"""Vision stage: connect the Vision provider to the REAL OCR pipeline.

Stage 2B integration point (called from the POST /ocr/extract production
path AFTER the existing RapidOCR + Tesseract flow completes):

  OCR result (fields, boxes, diagnostics, ingredient/nutrition evidence)
    + analysis requirements (required fields for this context)
    -> grouped, image-selected, OCR-contextualised vision calls (<=6)
    -> validated vision candidates
    -> deterministic reconciliation overlay (NEW keys only)
    -> readiness + diagnostics

Guarantees:
- The existing OCR pipeline is never bypassed or modified; legacy
  ``fields`` / ``fields_detailed`` keys are byte-identical afterwards.
  All vision output lands under NEW top-level ``vision`` and
  ``reconciliation`` keys.
- Vision DISABLED/unavailable -> OCR result returned with a
  ``vision_status=unavailable`` block only; extraction identical.
- Any vision exception -> OCR-only fallback; inspection never fails.
- OCR is never silently overridden: conflicts stay NEEDS_REVIEW with
  both candidates preserved; only validated vision candidates
  participate; OCR legs are trusted (they already passed the OCR
  anchor gates) and are never demoted by validator technicalities.
"""
from __future__ import annotations

import io
import logging
import time
from typing import Any

log = logging.getLogger("legalakshi.vision_stage")

# Stage 2B §3 grouped extraction plan (max 6 calls/inspection, target 2-4).
STAGE2B_GROUPS: dict[str, list[str]] = {
    "A_product": ["product_name", "brand_name", "common_generic_name",
                  "category", "quantity", "unit"],
    "B_declarations": ["mrp", "manufacturing_date", "best_before",
                       "batch_lot", "date_of_packing", "expiry_date"],
    "C_business": ["manufacturer", "manufacturer_address",
                   "fssai_license", "consumer_care"],
    "D_ingredients": ["ingredients", "allergens"],
    "E_nutrition": ["energy", "protein", "carbohydrate", "total_sugars",
                    "added_sugars", "total_fat", "saturated_fat",
                    "trans_fat", "sodium", "serving_size",
                    "nutrition_basis"],
    "F_symbols": ["veg_nonveg", "country_of_origin"],
}

# Prototype second-pass coverage (internal vision field names): the
# declarations Gemini should attempt whenever OCR has not DETECTED
# them. "Only unresolved" still holds — DETECTED fields never cost a
# call. nutrition_information rides the bounded F_other tail.
SECOND_PASS_PROTOTYPE_FIELDS: tuple[str, ...] = (
    "product_name", "category", "manufacturer", "quantity", "unit",
    "manufacturing_date", "mrp", "batch_lot", "best_before",
    "date_of_packing", "expiry_date",
    "fssai_license", "consumer_care", "country_of_origin",
    "ingredients", "veg_nonveg", "nutrition_information",
)

# Fields eligible for the ONE targeted second call: the small
# declaration block plus ingredients. Only fields still without a
# usable value are re-requested, on the same images.
TARGETED_SECOND_CALL_FIELDS: tuple[str, ...] = (
    "mrp", "batch_lot", "manufacturing_date", "date_of_packing",
    "expiry_date", "best_before", "ingredients",
)


def _with_prototype_fields(wanted: list[str],
                           ocr_statuses: dict[str, str],
                           ) -> list[str]:
    """Union the wanted list with uncovered prototype fields.

    Keeps the single-call budget identical (one consolidated request
    regardless of field count) while guaranteeing the prototype
    declarations are attempted whenever OCR missed them.
    """
    out = list(wanted or [])
    for vision_field in SECOND_PASS_PROTOTYPE_FIELDS:
        if vision_field in out:
            continue
        ocr_key = VISION_TO_OCR.get(vision_field, vision_field)
        if (ocr_statuses or {}).get(ocr_key, "NOT_DETECTED") == \
                "DETECTED":
            continue
        out.append(vision_field)
    return out

# Registry/OCR names -> vision field names for the "other applicable
# declarations" tail (entries without an OCR key still get a bounded
# group instead of per-field calls).
REGISTRY_TO_VISION: dict[str, list[str]] = {
    "net_quantity": ["quantity", "unit"],
    "mfg_month_year": ["manufacturing_date"],
    "best_before": ["best_before"],
    "mrp": ["mrp"],
    "manufacturer": ["manufacturer"],
    "consumer_care": ["consumer_care"],
    "country_of_origin": ["country_of_origin"],
    "unit_sale_price": ["unit_sale_price"],
    "common_generic_name": ["product_name", "common_generic_name",
                            "brand_name"],
    "veg_nonveg_dot": ["veg_nonveg"],
}

# Vision field -> OCR fields_detailed key for reconciliation.
VISION_TO_OCR: dict[str, str] = {
    "quantity": "quantity", "unit": "quantity", "mrp": "mrp",
    "manufacturing_date": "manufacturing_date",
    "date_of_packing": "date_of_packing",
    "expiry_date": "expiry_date",
    "best_before": "best_before", "batch_lot": "batch_lot",
    "manufacturer": "manufacturer",
    "manufacturer_address": "manufacturer_address",
    "fssai_license": "fssai_license", "consumer_care": "consumer_care",
    "product_name": "product_name", "brand_name": "product_name",
    "common_generic_name": "product_name",
    "category": "category",
    "country_of_origin": "country_of_origin",
    "veg_nonveg": "veg_nonveg", "ingredients": "ingredients",
    "allergens": "allergens", "unit_sale_price": "unit_sale_price",
}

# Fields worth a vision call even outside strict requirements.
HIGH_VALUE = frozenset({"mrp", "quantity", "manufacturing_date",
                        "fssai_license", "product_name"})

MAX_VISION_CALLS = 6
DEFAULT_PER_CALL_TIMEOUT_S = 30.0
DEFAULT_OVERALL_TIMEOUT_S = 100.0
# Vision-only DETECTED bar: strong evidence + validation + confidence.
VISION_STRONG_CONF = 0.85
# Values that count as "no usable vision value" for verdict purposes
# (UNKNOWN is information about uncertainty, never a value).
_VISION_MISSING_TOKENS = frozenset({"UNKNOWN", "UNCLEAR", ""})


def _consolidated_enabled() -> bool:
    """One-pass extraction first (config flag, default on)."""
    try:
        from app.core.config import get_settings

        return bool(getattr(get_settings(),
                            "LEGALAKSHI_VISION_CONSOLIDATED", True))
    except Exception:
        return True


def _payload_hash(payload: Any) -> str | None:
    """Stable bytes hash for send-dedupe (never logs contents)."""
    try:
        import hashlib as _hl

        if isinstance(payload, (bytes, bytearray)):
            return _hl.sha256(bytes(payload)).hexdigest()[:16]
        if hasattr(payload, "tobytes"):
            return _hl.sha256(payload.tobytes()).hexdigest()[:16]
    except Exception:
        pass
    return None


def _declaration_crops(
    images: list[tuple[Any, str]],
    ocr_result: dict[str, Any],
    max_crops: int = 4,
) -> list[tuple[Any, str]]:
    """High-resolution declaration/ingredient crops (call-2 payload).

    Builds contextual colour crops (label + value + neighbours) from
    the OCR region proposals for the declaration and ingredient
    groups, across the inspection images. Empty when no proposal
    exists (caller falls back to the full originals). Never raises;
    never logs image contents.
    """
    from app.services.package_intelligence import regions as regions_mod

    crops: list[tuple[Any, str]] = []
    try:
        for raw, label in images:
            if raw is None or len(crops) >= max_crops:
                break
            try:
                proposals = regions_mod.propose_regions(ocr_result,
                                                        label)
            except Exception:
                continue
            for group in ("B_declarations", "D_ingredients"):
                if len(crops) >= max_crops:
                    break
                try:
                    region_name, region = regions_mod.region_for_group(
                        group, proposals)
                except Exception:
                    continue
                if not region:
                    continue
                try:
                    stage_size = _stage_size_for(ocr_result, label)
                    crop = regions_mod.crop_region_highres(
                        raw, region.get("rect") or [], stage_size)
                except Exception:
                    crop = None
                if crop is not None:
                    crops.append(
                        (crop, f"{label}#{region_name}"))
                    log.info("vision declaration crop image=%s region=%s "
                             "bytes=%d", label, region_name, len(crop))
    except Exception:
        pass
    return crops


def _representative_original(
    images: list[tuple[Any, str]],
    ocr_result: dict[str, Any],
) -> tuple[Any, str]:
    """One representative ORIGINAL colour photo for the single pass.

    Front panel preferred (role-aware when roles are known); the bytes
    are the untouched upload — never a thresholded/grayscale OCR
    variant — because logos, colours, the veg symbol and handwriting
    must stay visible. Never raises.
    """
    panels = _representative_originals(images, ocr_result, max_panels=1)
    if panels:
        return panels[0]
    labels = [label for _, label in images]
    if not labels:
        return None, "front"
    return images[0]


def _representative_originals(
    images: list[tuple[Any, str]],
    ocr_result: dict[str, Any],
    max_panels: int = 2,
) -> list[tuple[Any, str]]:
    """Up to ``max_panels`` distinct ORIGINAL photos (front, then back).

    Duplicate image bytes are never selected twice: identical uploads
    cost one panel slot, never two sends. Never raises.
    """
    labels = [label for _, label in images]
    if not labels:
        return []
    try:
        from app.services.ocr import image_roles as _roles

        _timings = (ocr_result.get("timings") or {})
        _role_map = {lab: (info.get("role") if isinstance(info, dict)
                           else None)
                     for lab, info in
                     (_timings.get("images") or {}).items()}
        ordered = _roles.ordered_labels_for_group(
            "A_product", labels,
            {lab: (_role_map.get(lab) or "UNKNOWN") for lab in labels})
    except Exception:
        ordered = list(labels)
    preferred = ["front", *[lab for lab in ordered if lab != "front"],
                 "back"]
    picked: list[tuple[Any, str]] = []
    seen_hashes: set[str] = set()
    seen_labels: set[str] = set()
    for lab in preferred:
        if len(picked) >= max(1, max_panels) or lab in seen_labels:
            continue
        for payload, label in images:
            if label != lab or payload is None:
                continue
            digest = _payload_hash(payload)
            if digest and digest in seen_hashes:
                continue
            if digest:
                seen_hashes.add(digest)
            seen_labels.add(label)
            picked.append((payload, label))
            break
    if not picked:
        first = next(((p, lab) for p, lab in images if p is not None),
                     (None, "front"))
        if first[0] is not None:
            picked.append(first)
    return picked[:max(1, max_panels)]


def wanted_vision_fields(
    requirements: dict[str, Any] | None,
    ocr_statuses: dict[str, str],
) -> list[str]:
    """Required-but-unresolved fields (+ high-value unresolved).

    Only groups containing these are requested — resolved fields never
    cost a vision call.
    """
    required: set[str] = set()
    for entry in (requirements or {}).get("required", []):
        if not isinstance(entry, dict):
            continue
        for name in (entry.get("ocr_key"), entry.get("field")):
            if not name:
                continue
            mapped = REGISTRY_TO_VISION.get(str(name), [str(name)])
            required.update(mapped)
    wanted: list[str] = []
    for group_members in STAGE2B_GROUPS.values():
        for member in group_members:
            if member in wanted:
                continue
            status = (ocr_statuses or {}).get(
                VISION_TO_OCR.get(member, member), "NOT_DETECTED")
            if status == "DETECTED":
                continue
            if member in required or member in HIGH_VALUE:
                wanted.append(member)
    # Applicable-but-unmapped registry fields (ocr_key None): bounded
    # "other" tail, only when required and unresolved.
    for entry in (requirements or {}).get("required", []):
        if not isinstance(entry, dict):
            continue
        field = str(entry.get("field") or "")
        if field and entry.get("ocr_key") is None \
                and field not in REGISTRY_TO_VISION \
                and field not in wanted:
            wanted.append(field)
    return wanted


def plan_groups(wanted: list[str]) -> dict[str, list[str]]:
    """Wanted fields -> Stage 2B groups (one call each, <= MAX)."""
    plan: dict[str, list[str]] = {}
    for group, members in STAGE2B_GROUPS.items():
        hit = [m for m in members if m in wanted]
        if hit:
            plan[group] = hit
    rest = [f for f in wanted
            if not any(f in m for m in STAGE2B_GROUPS.values())]
    if rest:
        plan["F_other"] = rest
    # Hard cap: drop lowest-priority tail groups first.
    order = ["A_product", "B_declarations", "C_business", "D_ingredients",
             "E_nutrition", "F_symbols", "F_other"]
    plan = {g: plan[g] for g in order if g in plan}
    while len(plan) > MAX_VISION_CALLS:
        plan.popitem()
    return plan


def select_group_image(
    group: str,
    images: list[tuple[Any, str]],
    ocr_result: dict[str, Any],
) -> tuple[Any, str]:
    """Most-evident image for a group (Stage 2B §9), budget-safe.

    Falls back gracefully (front -> back -> first) when evidence is
    uncertain; never raises.
    """
    labels = [label for _, label in images]
    if not labels:
        return None, "front"

    # Stage 3 §2: role-aware ordering (FRONT/BACK/SIDE/... from the OCR
    # timings, falling back to upload order). Explicit anchor evidence
    # below still beats role preference; roles only order the search.
    try:
        from app.services.ocr import image_roles as _roles

        _timings = (ocr_result.get("timings") or {})
        _role_map = {lab: (info.get("role") if isinstance(info, dict)
                           else None)
                     for lab, info in
                     (_timings.get("images") or {}).items()}
        _role_order = _roles.ordered_labels_for_group(
            group, labels,
            {lab: (_role_map.get(lab) or "UNKNOWN") for lab in labels})
    except Exception:
        _role_order = list(labels)

    def _pick(*prefs: str) -> tuple[Any, str]:
        for pref in prefs:
            for payload, label in images:
                if label == pref:
                    return payload, label
        for lab in _role_order:
            for payload, label in images:
                if label == lab:
                    return payload, label
        return images[0]

    try:
        diag_images = ((ocr_result.get("diagnostics") or {}).get("images")
                       or {})
    except Exception:
        diag_images = {}
    def _diag_in_role_order() -> list:
        items = list(diag_images.items())
        order = {lab: i for i, lab in enumerate(_role_order)}
        return sorted(items,
                      key=lambda kv: order.get(kv[0], len(order)))

    if group == "A_product":
        return _pick("front", labels[0])
    if group == "F_symbols":
        return _pick("front", labels[0])
    if group == "D_ingredients":
        for label, info in _diag_in_role_order():
            if isinstance(info, dict) and info.get(
                    "ingredient_heading_box"):
                for payload, lab in images:
                    if lab == label:
                        return payload, lab
        return _pick("back", labels[0])
    if group == "E_nutrition":
        for label, info in _diag_in_role_order():
            try:
                boxes = (info.get("candidate_boxes") or {}).get(
                    "nutrition") or []
            except Exception:
                boxes = []
            if boxes:
                for payload, lab in images:
                    if lab == label:
                        return payload, lab
        return select_group_image("D_ingredients", images, ocr_result)
    if group == "C_business":
        for label, info in _diag_in_role_order():
            try:
                boxes = info.get("candidate_boxes") or {}
                has = bool(boxes.get("fssai") or boxes.get("care"))
            except Exception:
                has = False
            if has:
                for payload, lab in images:
                    if lab == label:
                        return payload, lab
        return _pick("back", labels[0])
    # B_declarations: MRP/date evidence image wins.
    for label, info in _diag_in_role_order():
        try:
            boxes = info.get("candidate_boxes") or {}
            has = bool(boxes.get("mrp") or boxes.get("date"))
        except Exception:
            has = False
        if has:
            for payload, lab in images:
                if lab == label:
                    return payload, lab
    return _pick("back", labels[0])


def build_ocr_context(
    group_fields: list[str],
    ocr_result: dict[str, Any],
    image_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """OCR candidates + layout context for one vision call (Stage 2B §4).

    Truncated hard (prompt budget): a few candidates, a text slice, box
    count — never the whole package dump.
    """
    detailed = ocr_result.get("fields_detailed") or {}
    ocr_candidates: list[dict[str, Any]] = []
    for key, hit in detailed.items():
        if not isinstance(hit, dict) or hit.get("value") in (None, ""):
            continue
        ocr_candidates.append({
            "field": key, "value": str(hit.get("value")),
            "confidence": hit.get("confidence"),
            "status": hit.get("status"),
            "image_id": hit.get("image") or hit.get("image_id"),
        })
    ocr_candidates = ocr_candidates[:24]
    image_text = ""
    n_boxes = 0
    quality: dict[str, Any] = {}
    ingredient_region = None
    nutrition_rect = None
    try:
        for row in ocr_result.get("images", []) or []:
            if isinstance(row, dict) and row.get("image") == image_id:
                lines = row.get("lines", []) or []
                image_text = "\n".join(
                    str(l.get("text", "")) for l in lines[:60])[:3000]
                n_boxes = len(lines)
                break
        timings = ocr_result.get("timings") or {}
        per_image = (timings.get("images") or {}).get(image_id) or {}
        quality = {k: per_image.get(k) for k in
                   ("image_quality", "orientation") if k in per_image}
        diag_images = ((ocr_result.get("diagnostics") or {}).get("images")
                       or {})
        info = diag_images.get(image_id) or {}
        if isinstance(info, dict):
            ingredient_region = info.get("ingredient_heading_box")
    except Exception:
        pass
    layout_context = {
        "image_id": image_id,
        "image_quality": quality,
        "ocr_text": image_text,
        "ocr_boxes": n_boxes,
        "ingredient_region_present": bool(ingredient_region),
        "requested_fields": list(group_fields),
        "instruction": "You are an extraction component. Use the package "
        "image as the primary visual evidence and OCR text as supporting "
        "evidence. Extract only explicitly visible declarations. Never "
        "infer or guess missing values.",
    }
    return ocr_candidates, layout_context


def ocr_evidence_text(ocr_result: dict[str, Any],
                      ocr_key: str) -> str:
    """Source-line text behind an OCR field hit (validator context)."""
    try:
        detailed = ocr_result.get("fields_detailed") or {}
        hit = detailed.get(ocr_key) or {}
        label = hit.get("image") or hit.get("image_id")
        box = hit.get("box") or hit.get("bbox")
        value = str(hit.get("value") or "")
        diag_images = ((ocr_result.get("diagnostics") or {}).get("images")
                       or {})
        boxes = ((diag_images.get(label) or {}).get("ocr_boxes") or [])
        for ln in boxes:
            if box and ln.get("box") == box:
                return str(ln.get("text", ""))
        for ln in boxes:
            text = str(ln.get("text", ""))
            if value and value in text:
                return text
    except Exception:
        pass
    return ""


def _norm_compare(field: str, value: Any, unit: Any = None) -> Any:
    import re as _re

    text = str(value or "").strip().replace(",", "")
    if field == "mrp":
        try:
            return round(float(text), 2)
        except ValueError:
            return text.lower()
    if field == "quantity":
        try:
            return (float(text.split()[0]), str(unit or "").lower())
        except ValueError:
            return (text.lower(), str(unit or "").lower())
    if field == "veg_nonveg":
        return text.upper().replace("-", "_").replace(" ", "_")
    if field == "consumer_care":
        # Stage 2C §11: formatting normalised, digits preserved —
        # "1800 103 1947" agrees with "18001031947".
        from app.services.package_intelligence.validators import (
            normalize_care_number,
        )

        norm = normalize_care_number(text)
        if norm and "@" not in norm and "http" not in norm:
            return _re.sub(r"\D", "", norm)
        return (norm or text).lower()
    return text.lower()


def build_symbol_verification(
    ocr_result: dict[str, Any],
    vision_candidates: list[dict[str, Any]] | None = None,
    ai_available: bool = False,
) -> dict[str, Any]:
    """OpenCV x Gemini vegetarian-symbol matrix (Task 5).

    The existing OpenCV verdict is never overridden. Gemini acts as a
    second visual verifier; any CV/Gemini disagreement stays in manual
    review — never auto-resolved. NOT_DETECTED remains safe (never a
    non-compliance signal).
    """
    cv = (ocr_result.get("veg_nonveg_symbol") or {})
    cv_cls = str(cv.get("classification") or "UNKNOWN").strip().upper()
    if cv_cls not in ("VEGETARIAN", "NON_VEGETARIAN"):
        cv_cls = "UNKNOWN"
    cv_conf = cv.get("confidence")
    gem_value: str | None = None
    gem_conf: float | None = None
    for cand in vision_candidates or []:
        if not isinstance(cand, dict) or cand.get("field") != "veg_nonveg":
            continue
        raw = str(cand.get("value") or "").strip().upper()
        if raw in ("VEGETARIAN", "NON_VEGETARIAN"):
            try:
                conf = float(cand.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if gem_conf is None or conf > gem_conf:
                gem_value, gem_conf = raw, round(conf, 3)
    cv_hit = cv_cls in ("VEGETARIAN", "NON_VEGETARIAN")
    if not ai_available:
        combined = "CV_ONLY" if cv_hit else "NOT_DETECTED"
        note = ("AI verification unavailable — OpenCV result stands; "
                "inspector verifies on the package.")
    elif cv_hit and gem_value == cv_cls:
        combined = "AI_CV_VERIFIED"
        note = "OpenCV and Gemini agree — still inspector-confirmed."
    elif not cv_hit and gem_value:
        combined = "AI_EXTRACTED_REVIEW"
        note = "OpenCV found no mark; Gemini reports one — officer " \
            "review required."
    elif cv_hit and not gem_value:
        combined = "CV_ONLY"
        note = "Gemini could not confirm the mark — OpenCV result " \
            "stands for review."
    elif cv_hit and gem_value and gem_value != cv_cls:
        combined = "CONFLICT_REVIEW"
        note = "OpenCV and Gemini disagree — manual review required; " \
            "never auto-resolved."
    else:
        combined = "NOT_DETECTED"
        note = "no mark found by either visual layer; not a finding " \
            "of non-compliance."
    return {"opencv": {"classification": cv_cls, "confidence": cv_conf,
                       "status": cv.get("status")},
            "gemini": {"classification": gem_value or "NOT_DETECTED",
                       "confidence": gem_conf},
            "combined": combined, "note": note,
            "ai_available": ai_available}


def overlay_reconciliation(
    ocr_result: dict[str, Any],
    vision_candidates: list[dict[str, Any]],
    requirements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic OCR + vision overlay (Stage 2B §6).

    Returns the ``reconciliation`` block (FINAL candidates + readiness).
    OCR legs are trusted; only validated vision candidates participate;
    conflicts stay NEEDS_REVIEW with both sides preserved.
    """
    from app.services.package_intelligence import service as pi_mod
    from app.services.package_intelligence import validators as valid_mod

    t0 = time.perf_counter()
    detailed = ocr_result.get("fields_detailed") or {}
    by_field: dict[str, list[dict[str, Any]]] = {}
    for cand in vision_candidates or []:
        if not isinstance(cand, dict) or not cand.get("field"):
            continue
        by_field.setdefault(str(cand["field"]), []).append(cand)
    fields: dict[str, dict[str, Any]] = {}
    dropped: list[dict[str, Any]] = []
    for vfield, cands in by_field.items():
        ocr_key = VISION_TO_OCR.get(vfield, vfield)
        hit = detailed.get(ocr_key) or {}
        ocr_value = hit.get("value")
        ocr_conf = hit.get("confidence")
        ocr_status = hit.get("status", "NOT_DETECTED")
        ocr_image = hit.get("image") or hit.get("image_id")
        ocr_box = hit.get("box") or hit.get("bbox")
        ocr_evidence = ocr_evidence_text(ocr_result, ocr_key)
        for cand in cands:
            value = cand.get("value")
            unit = cand.get("unit")
            evidence = cand.get("evidence_text") or ""
            if vfield == "veg_nonveg" and (value is None or str(
                    value).strip().upper() in ("UNKNOWN", "UNCLEAR", "")):
                fields[vfield] = {
                    "field": vfield, "final_value": None, "unit": None,
                    "status": "NOT_DETECTED", "confidence": 0.0,
                    "sources": ["vision"], "candidates": [cand],
                    "agreement": "NO_EVIDENCE",
                    "evidence": [evidence] if evidence else [],
                    "needs_review_reason": "symbol UNKNOWN — never "
                    "inferred from ingredients",
                    "image_id": cand.get("image_id"),
                    "bbox": cand.get("bbox"),
                    "provider": cand.get("provider"),
                    "model": cand.get("model")}
                continue
            ok, reason = valid_mod.validate_field(
                vfield if vfield in valid_mod.VALIDATORS
                else ocr_key,
                None if value is None else str(value),
                unit=unit, evidence=evidence or None)
            if not ok:
                # Audit first (unchanged shape). Retain for review only
                # when the model SAW something unusable (a relative
                # best-before statement, an illegible read): final stays
                # None so it is never usable, never a verdict. Pure
                # NOT_DETECTED (nothing on the visible panels) stays
                # dropped — absence is not review evidence.
                from app.services.package_intelligence import (
                    reconciliation as _recon_mod,
                )

                dropped.append({"field": vfield, "value": value,
                                "reason": reason,
                                "source": "vision"})
                if str(cand.get("status") or "").upper() == \
                        "NOT_DETECTED":
                    continue
                _ocr_usable = ocr_value not in (None, "")
                fields[vfield] = {
                    "field": vfield, "unit": unit,
                    "image_id": cand.get("image_id"),
                    "bbox": cand.get("bbox"),
                    "provider": cand.get("provider"),
                    "model": cand.get("model"),
                    "evidence": [e for e in
                                 [ocr_evidence, evidence] if e],
                    "final_value": None,
                    "status": "NEEDS_REVIEW", "confidence": 0.0,
                    "sources": ["vision"],
                    "candidates": [cand],
                    "agreement": "SINGLE_SOURCE",
                    "verdict": _recon_mod.reconciliation_verdict(
                        ocr_value if _ocr_usable else None, None,
                        "SINGLE_SOURCE", "NEEDS_REVIEW"),
                    "needs_review_reason": "vision value not usable "
                    f"as declared ({reason}); retained for review only",
                }
                continue
            entry = {
                "field": vfield, "unit": unit,
                "image_id": cand.get("image_id"),
                "bbox": cand.get("bbox"),
                "provider": cand.get("provider"),
                "model": cand.get("model"),
                "evidence": [e for e in
                             [ocr_evidence, evidence] if e],
            }
            if ocr_value in (None, ""):
                # Stage 2B §6c: vision-only DETECTED only with strong,
                # grounded evidence (confident AND visibly anchored);
                # otherwise the candidate is preserved for review.
                strong = (cand.get("confidence") or 0) >= \
                    VISION_STRONG_CONF and bool(evidence)
                if strong:
                    fields[vfield] = {
                        **entry, "final_value": str(value),
                        "status": "DETECTED",
                        "confidence": cand.get("confidence") or 0.0,
                        "sources": ["vision"],
                        "candidates": [cand],
                        "agreement": "SINGLE_SOURCE",
                        "needs_review_reason": ""}
                else:
                    fields[vfield] = {
                        **entry, "final_value": None,
                        "status": "NEEDS_REVIEW",
                        "confidence": cand.get("confidence") or 0.0,
                        "sources": ["vision"],
                        "candidates": [cand],
                        "agreement": "SINGLE_SOURCE",
                        "needs_review_reason": "vision-only candidate "
                        "without strong evidence"}
                continue
            # OCR present: compare normalised forms.
            unit_cmp = unit
            if ocr_key == "quantity" and unit is None:
                unit_cmp = (detailed.get("unit") or {}).get("value")
            if _norm_compare(ocr_key, ocr_value,
                             (detailed.get("unit") or {}).get("value")
                             if ocr_key == "quantity" else None) == \
                    _norm_compare(ocr_key if ocr_key != "quantity"
                                  else "quantity", value, unit_cmp):
                conf = min(float(ocr_conf or 0),
                           float(cand.get("confidence") or 0))
                status = "DETECTED" if conf >= 0.6 else "NEEDS_REVIEW"
                fields[vfield] = {
                    **entry, "final_value": str(ocr_value),
                    "status": status, "confidence": round(conf, 3),
                    "sources": ["rapidocr", "vision"],
                    "candidates": [
                        {"source": "rapidocr", "value": str(ocr_value),
                         "confidence": ocr_conf, "image_id": ocr_image,
                         "bbox": ocr_box},
                        {"source": "vision", "value": str(value),
                         "confidence": cand.get("confidence"),
                         "image_id": cand.get("image_id"),
                         "bbox": cand.get("bbox"),
                         # §10: OCR fallback is never relabelled as AI —
                         # the vision leg keeps its own modality flags.
                         "handwritten": cand.get("handwritten", False),
                         "modality": cand.get("modality"),
                         "evidence_location": cand.get(
                             "evidence_location"),
                         "provider": cand.get("provider"),
                         "model": cand.get("model")}],
                    "agreement": "AGREE",
                    "needs_review_reason": "" if status == "DETECTED"
                    else "low agreement confidence"}
            else:
                fields[vfield] = {
                    **entry, "final_value": None, "unit": None,
                    "status": "NEEDS_REVIEW",
                    "confidence": max(float(ocr_conf or 0),
                                      float(cand.get("confidence") or 0)),
                    "sources": ["rapidocr", "vision"],
                    "candidates": [
                        {"source": "rapidocr", "value": str(ocr_value),
                         "confidence": ocr_conf, "image_id": ocr_image,
                         "bbox": ocr_box},
                        {"source": "vision", "value": str(value),
                         "confidence": cand.get("confidence"),
                         "image_id": cand.get("image_id"),
                         "bbox": cand.get("bbox"),
                         "handwritten": cand.get("handwritten", False),
                         "modality": cand.get("modality"),
                         "evidence_location": cand.get(
                             "evidence_location"),
                         "provider": cand.get("provider"),
                         "model": cand.get("model")}],
                    "agreement": "CONFLICT",
                    "needs_review_reason": "conflicting candidates: "
                    f"rapidocr={ocr_value}, vision={value}"}
    rec_ms = round((time.perf_counter() - t0) * 1000, 1)
    # Stage 2C §16 final gate (vision-influenced DETECTED only).
    fields = valid_mod.cross_validate_final(fields)
    # Second-pass verdicts (OCR x Gemini relationship per field).
    # Additive only: status/agreement/candidates are never renamed.
    from app.services.package_intelligence.reconciliation import (
        reconciliation_verdict as _verdict_fn,
    )

    def _usable(value: Any) -> Any | None:
        text = str(value or "").strip()
        if not text or text.upper() in ("UNKNOWN", "UNCLEAR"):
            return None
        return value

    for _entry in fields.values():
        if not isinstance(_entry, dict):
            continue
        _cands = _entry.get("candidates") or []
        _ocr_v = next(
            (c.get("value") for c in _cands
             if isinstance(c, dict)
             and c.get("source") in ("rapidocr", "tesseract")), None)
        _vis_v = next(
            (c.get("value") for c in _cands
             if isinstance(c, dict) and c.get("source") == "vision"),
            None)
        _entry["verdict"] = _verdict_fn(
            _usable(_ocr_v), _usable(_vis_v),
            str(_entry.get("agreement") or ""),
            str(_entry.get("status") or ""))
    readiness = pi_mod.readiness(
        {k: {"status": v.get("status")} for k, v in fields.items()},
        requirements)
    counts = {"detected": 0, "needs_review": 0, "not_detected": 0}
    for v in fields.values():
        if v.get("status") == "DETECTED":
            counts["detected"] += 1
        elif v.get("status") == "NEEDS_REVIEW":
            counts["needs_review"] += 1
        else:
            counts["not_detected"] += 1
    return {"fields": fields, "readiness": readiness,
            "field_counts": counts, "dropped_vision": dropped,
            "reconciliation_ms": rec_ms}


def _stage_size_for(ocr_result: dict[str, Any],
                    image_id: str) -> list:
    """Stage dimensions for an image label (crop mapping)."""
    try:
        info = ((ocr_result.get("diagnostics") or {}).get("images")
                or {}).get(image_id) or {}
        size = info.get("stage_dimensions") or [0, 0]
        return [float(size[0]), float(size[1])]
    except Exception:
        return [0, 0]


def _sanitize_error(exc: BaseException) -> str:
    """Sanitised failure reason — never leaks keys/tokens/headers."""
    text = f"{type(exc).__name__}: {exc}"
    try:
        from app.core.config import get_settings

        for attr in ("LEGALAKSHI_VISION_API_KEY", "GEMINI_API_KEY"):
            key = str(getattr(get_settings(), attr, "") or "")
            if key and key in text:
                text = text.replace(key, "[redacted]")
    except Exception:
        pass
    for token in ("x-goog-api-key", "Authorization", "Bearer"):
        if token.lower() in text.lower():
            text = "[auth material redacted]"
            break
    return text[:300]


def enhance_ocr_with_vision(
    ocr_result: dict[str, Any],
    raw_images: list[tuple[bytes | None, str]],
    requirements: dict[str, Any] | None = None,
    inspection_id: str | None = None,
    provider: Any | None = None,
    per_call_timeout_s: float = DEFAULT_PER_CALL_TIMEOUT_S,
    overall_timeout_s: float = DEFAULT_OVERALL_TIMEOUT_S,
) -> dict[str, Any]:
    """Route-level vision enhancement (mutates + returns ocr_result).

    Resolves the provider (None when disabled/unconfigured -> OCR-only
    block), plans bounded grouped calls with per-group image selection
    and OCR context, reconciles, and attaches ``vision`` +
    ``reconciliation`` keys. Never raises: every failure path attaches
    an unavailable block and preserves the OCR result.
    """
    from app.services.vision.provider import get_vision_provider

    t_start = time.perf_counter()
    images = [(raw, label) for raw, label in (raw_images or []) if raw]
    ocr_timings = (ocr_result.get("timings") or {})
    ocr_calls = (ocr_timings.get("provider_calls") or 0)
    ocr_ms = (ocr_timings.get("total_ms"))

    def _disabled(reason: str) -> dict[str, Any]:
        ocr_result["vision"] = {
            "vision_enabled": False, "vision_provider": None,
            "vision_model": None, "vision_status": "unavailable",
            "vision_status_detail": "unavailable",
            "vision_error": reason, "vision_calls": 0,
            "vision_latency_ms": 0.0, "ocr_calls": ocr_calls,
            "ocr_ms": ocr_ms}
        ocr_result["symbol_verification"] = build_symbol_verification(
            ocr_result, [], ai_available=False)
        log.info("vision disabled: %s (images=%d)", reason,
                 len(images))
        return ocr_result

    resolved = provider if provider is not None else get_vision_provider()
    if resolved is None:
        return _disabled("vision provider unavailable (disabled or "
                         "unconfigured); OCR-only flow")
    try:
        detailed = ocr_result.get("fields_detailed") or {}
        ocr_statuses = {k: (v.get("status") if isinstance(v, dict)
                            else None) or "NOT_DETECTED"
                        for k, v in detailed.items()}
        wanted = wanted_vision_fields(requirements, ocr_statuses)
        wanted = _with_prototype_fields(wanted, ocr_statuses)
        log.info("vision plan: images=%d wanted_fields=%d provider=%s",
                 len(images), len(wanted),
                 getattr(resolved, "name", None))
        if not wanted:
            ocr_result["vision"] = {
                "vision_enabled": True,
                "vision_provider": getattr(resolved, "name", None),
                "vision_model": getattr(resolved, "model", None),
                "vision_status": "ok", "vision_error": None,
                "vision_calls": 0, "vision_latency_ms": 0.0,
                "ocr_calls": ocr_calls, "ocr_ms": ocr_ms,
                "fields_requested": [], "fields_returned": [],
                "note": "no unresolved required/high-value fields; "
                "no vision calls needed"}
            ocr_result["reconciliation"] = overlay_reconciliation(
                ocr_result, [], requirements)
            ocr_result["symbol_verification"] = build_symbol_verification(
                ocr_result, [], ai_available=True)
            return ocr_result
        plan = plan_groups(wanted)
        from app.services.package_intelligence import regions as regions_mod
        from app.services.vision.service import extract_with_vision

        all_candidates: list[dict[str, Any]] = []
        total_calls = 0
        total_cached = 0
        group_reports: dict[str, Any] = {}
        calls_log: list[dict[str, Any]] = []
        # Payload hashes that already failed/timed-out: never resend the
        # same bytes to another group (duplicate-image waste).
        failed_hashes: set[str] = set()
        consolidated_fields: list[str] = []
        # Free-tier guard: a hard provider failure (429/503/timeout)
        # with zero candidates halts ALL further AI calls for this
        # inspection — no retry loops, no budget burn. OCR-only
        # continues with the recorded error.
        halt_ai = False
        halt_reason = ""
        # Consolidated single pass FIRST: one call, all wanted fields,
        # ALL selected original photos. Grouped region-crop calls
        # below then cover only fields still without any usable value —
        # typically zero further calls instead of up to six.
        if _consolidated_enabled() and wanted and images:
            # Prototype demo mode: ALL selected package images for the
            # same inspection ride ONE multimodal request (deduped,
            # capped at 8) — not one representative image.
            _panels = _representative_originals(images, ocr_result,
                                                max_panels=8)
            _image_id = "+".join(label for _, label in _panels) \
                if _panels else "front"
            # Consolidated failure leaves every field still wanted for
            # the targeted/grouped fallbacks below.
            still_wanted = list(wanted)
            if _panels:
                _hashes = [_payload_hash(p) for p, _ in _panels]
                _ocr_cands, _layout_ctx = build_ocr_context(
                    wanted, ocr_result, _image_id)
                _layout_ctx["pass"] = "consolidated"
                _layout_ctx["preprocessing_variant"] = "original-photo"
                _layout_ctx["panels"] = [
                    label for _, label in _panels]
                _summary = extract_with_vision(
                    resolved, _panels, wanted,
                    ocr_candidates=_ocr_cands,
                    layout_context=_layout_ctx,
                    inspection_id=inspection_id,
                    group_override="consolidated",
                    per_call_timeout_s=per_call_timeout_s,
                    call_log=calls_log, multi_images=len(_panels) > 1)
                total_calls += _summary.get("calls", 0)
                total_cached += _summary.get("cached", 0)
                if _summary.get("error") and not _summary.get(
                        "candidates"):
                    for _h in _hashes:
                        if _h:
                            failed_hashes.add(_h)
                    group_reports["consolidated"] = {
                        "image": _image_id, "fields": list(wanted),
                        "error": _summary.get("error")}
                    halt_ai = True
                    halt_reason = str(_summary.get("error") or
                                      "consolidated vision call failed")
                    log.info("vision consolidated pass image=%s fields=%d "
                             "failed: %s (AI halted for this inspection)",
                             _image_id, len(wanted),
                             _summary.get("error"))
                else:
                    all_candidates.extend(
                        _summary.get("candidates") or [])
                    consolidated_fields = sorted(
                        {str(c.get("field")) for c in
                         _summary.get("candidates") or []
                         if isinstance(c, dict) and c.get("field")})
                    group_reports["consolidated"] = {
                        "image": _image_id, "fields": list(wanted),
                        "n_candidates": len(
                            _summary.get("candidates") or []),
                        "cached": bool(_summary.get("cached")),
                        "preprocessing_variant": "original-photo",
                        "image_dimensions": _panel_dimensions(_panels)}
                    log.info("vision consolidated pass image=%s fields=%d "
                             "candidates=%d calls=%d",
                             _image_id, len(wanted),
                             len(_summary.get("candidates") or []),
                             _summary.get("calls", 0))
                    # Blank NOT_DETECTED candidates are NOT coverage:
                    # fields still without a usable value stay wanted
                    # so the grouped back-panel fallback can rescue
                    # them instead of going silent.
                    covered = {
                        str(c.get("field")) for c in
                        _summary.get("candidates") or []
                        if isinstance(c, dict) and c.get("field")
                        and (c.get("value") not in (None, "")
                             or str(c.get("status") or "").upper()
                             == "DETECTED")}
                    still_wanted = [f for f in wanted if f not in covered]
                    # ONE targeted second call: the small declaration
                    # block (MRP/batch/dates) plus ingredients. Sends
                    # high-resolution declaration/ingredient CROPS when
                    # the OCR region proposals yield them (label + value
                    # + context, colour intact, never thresholded);
                    # otherwise the same original images. Only fields
                    # still without a usable value are re-requested.
                    _missing = [f for f in TARGETED_SECOND_CALL_FIELDS
                                if f in still_wanted]
                    if _missing and not halt_ai and \
                            total_calls < MAX_VISION_CALLS:
                        _t_panels = _declaration_crops(
                            images, ocr_result)
                        if not _t_panels:
                            _t_panels = list(_panels)
                        _t_image_id = "+".join(
                            label for _, label in _t_panels)
                        _t_cands, _t_ctx = build_ocr_context(
                            _missing, ocr_result, _t_image_id)
                        _t_ctx["pass"] = "targeted-second"
                        _t_ctx["preprocessing_variant"] = \
                            "crop-color-highres" if _t_panels != \
                            list(_panels) else "original-photo"
                        _t_ctx["panels"] = [
                            label for _, label in _t_panels]
                        _t_summary = extract_with_vision(
                            resolved, _t_panels, _missing,
                            ocr_candidates=_t_cands,
                            layout_context=_t_ctx,
                            inspection_id=inspection_id,
                            group_override="targeted-second",
                            per_call_timeout_s=per_call_timeout_s,
                            call_log=calls_log,
                            multi_images=len(_t_panels) > 1)
                        total_calls += _t_summary.get("calls", 0)
                        total_cached += _t_summary.get("cached", 0)
                        if _t_summary.get("error") and not _t_summary.get(
                                "candidates"):
                            group_reports["targeted-second"] = {
                                "image": _t_image_id,
                                "fields": list(_missing),
                                "error": _t_summary.get("error")}
                            halt_ai = True
                            halt_reason = str(
                                _t_summary.get("error") or
                                "targeted vision call failed")
                        else:
                            all_candidates.extend(
                                _t_summary.get("candidates") or [])
                            group_reports["targeted-second"] = {
                                "image": _t_image_id,
                                "fields": list(_missing),
                                "n_candidates": len(
                                    _t_summary.get("candidates") or []),
                                "cached": bool(_t_summary.get("cached")),
                                "preprocessing_variant": _t_ctx.get(
                                    "preprocessing_variant")}
                        log.info("vision targeted-second pass fields=%d "
                                 "candidates=%d calls=%d",
                                 len(_missing),
                                 len(_t_summary.get("candidates") or []),
                                 _t_summary.get("calls", 0))
                        _covered2 = {
                            str(c.get("field")) for c in
                            _t_summary.get("candidates") or []
                            if isinstance(c, dict) and c.get("field")
                            and (c.get("value") not in (None, "")
                                 or str(c.get("status") or "").upper()
                                 == "DETECTED")}
                        still_wanted = [f for f in still_wanted
                                        if f not in _covered2]
                    if still_wanted:
                        plan = plan_groups(still_wanted)
                    else:
                        plan = {}
        deadline = t_start + overall_timeout_s
        for group, group_fields in plan.items():
            if total_calls >= MAX_VISION_CALLS:
                break
            if halt_ai:
                # Free-tier guard: an earlier hard failure stops ALL
                # further AI calls — no retry burn. Recorded per group
                # for diagnostics; OCR-only continues.
                group_reports[group] = {
                    "image": None, "fields": group_fields,
                    "error": "skipped: AI halted after earlier failure "
                    f"({halt_reason}); no retry on 429/503/timeout"}
                continue
            remaining = deadline - time.perf_counter()
            if remaining <= 0.5:
                break
            payload, image_id = select_group_image(group, images,
                                                   ocr_result)
            if payload is None:
                continue
            _phash = _payload_hash(payload)
            if _phash and _phash in failed_hashes:
                # Same bytes already failed/timed-out this inspection:
                # skip instead of burning another 30s call budget.
                group_reports[group] = {
                    "image": image_id, "fields": group_fields,
                    "error": "identical image bytes already failed in "
                    "this inspection; skipped"}
                continue
            ocr_cands, layout_ctx = build_ocr_context(
                group_fields, ocr_result, image_id)
            # Stage 2C §5 region-first: crop the evidence ROI when an
            # anchor exists; the full image stays noted as context.
            # Raw bytes (never decoded contents) are what we log.
            region_name, region = regions_mod.region_for_group(
                group, regions_mod.propose_regions(ocr_result,
                                                   image_id))
            crop_rect = None
            crop_dimensions = None
            preprocessing_variant = "full-original"
            mime_type = regions_mod.guess_mime(payload) \
                if isinstance(payload, (bytes, bytearray)) else None
            if region is not None and isinstance(
                    payload, (bytes, bytearray)):
                stage_size = _stage_size_for(ocr_result, image_id)
                crop = regions_mod.crop_region_jpeg(
                    payload, region.get("rect") or [], stage_size)
                if crop is not None:
                    payload = crop
                    crop_rect = region.get("rect")
                    mime_type = "image/jpeg"
                    try:
                        from PIL import Image as _Image

                        with _Image.open(io.BytesIO(
                                crop)) as _im:
                            crop_dimensions = [_im.size[0], _im.size[1]]
                    except Exception:
                        crop_dimensions = None
                    preprocessing_variant = "crop-jpeg-q85"
            layout_ctx["region"] = region_name
            layout_ctx["crop_rect"] = crop_rect
            layout_ctx["full_image_context"] = crop_rect is not None
            summary = extract_with_vision(
                resolved, [(payload, image_id)], group_fields,
                ocr_candidates=ocr_cands, layout_context=layout_ctx,
                inspection_id=inspection_id,
                group_override=group,
                per_call_timeout_s=min(per_call_timeout_s,
                                       max(remaining, 0.5)),
                call_log=calls_log)
            total_calls += summary.get("calls", 0)
            total_cached += summary.get("cached", 0)
            report: dict[str, Any] = {
                "image": image_id, "fields": group_fields,
                "region": region_name, "crop_rect": crop_rect,
                "crop_dimensions": crop_dimensions,
                "preprocessing_variant": preprocessing_variant,
                "image_bytes_size": len(payload)
                if isinstance(payload, (bytes, bytearray)) else None,
                "mime_type": mime_type}
            if summary.get("error") and not summary.get("candidates"):
                report["error"] = summary.get("error")
                if _phash:
                    failed_hashes.add(_phash)
                group_reports[group] = report
                continue
            all_candidates.extend(summary.get("candidates") or [])
            report["n_candidates"] = len(
                summary.get("candidates") or [])
            report["cached"] = bool(summary.get("cached"))
            group_reports[group] = report
        vision_ms = round((time.perf_counter() - t_start) * 1000, 1)
        if total_calls == 0:
            # Every vision call failed (or no image could be selected):
            # §11 — OCR-only continues, recorded as unavailable.
            first_error = next(
                (rep.get("error") for rep in group_reports.values()
                 if rep.get("error")), "all vision calls failed; "
                "OCR-only flow")
            ocr_result["vision"] = {
                "vision_enabled": False,
                "vision_provider": getattr(resolved, "name", None),
                "vision_model": getattr(resolved, "model", None),
                "vision_status": "unavailable",
            "vision_status_detail": "unavailable",
                "vision_error": first_error, "vision_calls": 0,
                "vision_latency_ms": vision_ms, "ocr_calls": ocr_calls,
                "ocr_ms": ocr_ms,
                "groups": group_reports}
            ocr_result["symbol_verification"] = build_symbol_verification(
                ocr_result, all_candidates, ai_available=False)
            log.info("vision unavailable: images=%d calls=%d latency_ms=%s "
                     "error=%s", len(images), total_calls, vision_ms,
                     str(first_error)[:160])
            return ocr_result
        reconciliation = overlay_reconciliation(
            ocr_result, all_candidates, requirements)
        # Stage 3C §N: stamp the originating vision group/region on each
        # reconciled field so review UI can show Region per field.
        try:
            _field_group: dict[str, str] = {}
            for _group, _fields in plan.items():
                for _f in _fields or []:
                    _field_group.setdefault(str(_f), _group)
            for _fname, _entry in (reconciliation.get("fields")
                                   or {}).items():
                if not isinstance(_entry, dict):
                    continue
                _group = _field_group.get(str(_fname))
                _rep = group_reports.get(_group, {}) if _group else {}
                if _rep.get("region"):
                    _entry["region"] = _rep.get("region")
                    _entry["region_image"] = _rep.get("image")
                    _entry["region_crop"] = bool(_rep.get("crop_rect"))
        except Exception:
            pass
        ocr_result["reconciliation"] = reconciliation
        ocr_result["symbol_verification"] = build_symbol_verification(
            ocr_result, all_candidates, ai_available=True)
        group_errors = [g for g, rep in group_reports.items()
                        if rep.get("error")]
        # Stage 2C §3 explicit states: ok / partial / unavailable.
        detail = "partial" if group_errors else "active"
        ocr_result["vision"] = {
            "vision_enabled": True,
            "vision_provider": getattr(resolved, "name", None),
            "vision_model": getattr(resolved, "model", None),
            "vision_status": "ok", "vision_status_detail": detail,
            "vision_error": "; ".join(
                f"{g}: {group_reports[g]['error']}"
                for g in group_errors) or None,
            "vision_calls": total_calls,
            "vision_latency_ms": vision_ms, "ocr_calls": ocr_calls,
            "ocr_ms": ocr_ms,
            "reconciliation_ms": reconciliation.get(
                "reconciliation_ms"),
            "total_ms": round(vision_ms + float(ocr_ms or 0), 1),
            "fields_requested": wanted,
            "fields_returned": sorted(
                {c.get("field") for c in all_candidates
                 if isinstance(c, dict) and c.get("value")}),
            "groups": group_reports,
            "calls_log": calls_log,
            "cached": total_cached,
            "consolidated": bool(consolidated_fields),
            "consolidated_fields": consolidated_fields,
            "targeted_second": "targeted-second" in group_reports,
            "fields": {"detected": reconciliation["field_counts"][
                "detected"],
                "needs_review": reconciliation["field_counts"][
                "needs_review"],
                "not_detected": reconciliation["field_counts"][
                "not_detected"]}}
        log.info("vision done: images=%d calls=%d (consolidated=%s) "
                 "latency_ms=%s reconciliation_ms=%s verdicts=%s",
                 len(images), total_calls, bool(consolidated_fields),
                 vision_ms, reconciliation.get("reconciliation_ms"),
                 _verdict_counts(reconciliation))
        try:
            _t_state = ("executed" if "targeted-second" in group_reports
                        else "skipped")
            _dims = (group_reports.get("consolidated") or {}).get(
                "image_dimensions")
            # §12 extraction summary: model, images + dimensions, call
            # counts, per-key-field states, targeted pass state. Never
            # key material, never image contents.
            log.info("Gemini extraction: model=%s images=%d dims=%s "
                     "calls=%d general_fields=%d declaration_fields=%d "
                     "ingredients=%s mrp=%s batch=%s packing=%s expiry=%s "
                     "targeted=%s",
                     getattr(resolved, "model", None), len(images),
                     _dims, total_calls, len(wanted),
                     len([f for f in TARGETED_SECOND_CALL_FIELDS
                          if f in wanted]),
                     _field_state(reconciliation, "ingredients"),
                     _field_state(reconciliation, "mrp"),
                     _field_state(reconciliation, "batch_lot"),
                     _field_state(reconciliation, "date_of_packing"),
                     _field_state(reconciliation, "expiry_date"),
                     _t_state)
        except Exception:
            pass
        return ocr_result
    except Exception as exc:  # never break the inspection
        ocr_result["vision"] = {
            "vision_enabled": False,
            "vision_provider": getattr(resolved, "name", None),
            "vision_model": getattr(resolved, "model", None),
            "vision_status": "unavailable",
            "vision_status_detail": "unavailable",
            "vision_error": _sanitize_error(exc), "vision_calls": 0,
            "vision_latency_ms": round(
                (time.perf_counter() - t_start) * 1000, 1),
            "ocr_calls": ocr_calls, "ocr_ms": ocr_ms}
        try:
            ocr_result["symbol_verification"] = \
                build_symbol_verification(
                    ocr_result, [], ai_available=False)
        except Exception:
            pass
        log.info("vision exception: %s", _sanitize_error(exc))
        return ocr_result


def _panel_dimensions(
        panels: list[tuple[Any, str]]) -> list[list[int] | None]:
    """Pixel dimensions per panel for diagnostics (never contents)."""
    dims: list[list[int] | None] = []
    try:
        from PIL import Image as _Image

        for payload, _label in panels:
            try:
                if isinstance(payload, (bytes, bytearray)):
                    with _Image.open(io.BytesIO(bytes(payload))) as _im:
                        dims.append([_im.size[0], _im.size[1]])
                    continue
            except Exception:
                pass
            try:
                import numpy as _np

                arr = _np.asarray(payload)
                dims.append([int(arr.shape[1]), int(arr.shape[0])])
            except Exception:
                dims.append(None)
    except Exception:
        pass
    return dims


def _field_state(reconciliation: dict[str, Any],
                 field: str) -> str:
    """One-word extraction state for the §12 summary log."""
    try:
        entry = (reconciliation.get("fields") or {}).get(field) or {}
        if entry.get("final_value") not in (None, ""):
            return str(entry.get("status") or "?")
        cands = entry.get("candidates") or []
        if cands:
            return "REVIEW"
        return "MISSING"
    except Exception:
        return "?"


def _verdict_counts(reconciliation: dict[str, Any]) -> dict[str, int]:
    """Verdict histogram for the phase log (never raises)."""
    counts: dict[str, int] = {}
    try:
        for entry in (reconciliation.get("fields") or {}).values():
            if not isinstance(entry, dict):
                continue
            verdict = str(entry.get("verdict") or "UNKNOWN")
            counts[verdict] = counts.get(verdict, 0) + 1
    except Exception:
        pass
    return counts
