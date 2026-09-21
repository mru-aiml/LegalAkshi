"""Stage 2 field-specific deterministic validators.

Reusable gates for reconciled candidates (OCR + vision alike). Each
validator returns (ok, reason): ok=True only when the value AND its
evidence context are trustworthy. Anything doubtful -> NEEDS_REVIEW
downstream, never silent acceptance. No guessing, no rewriting.
"""
from __future__ import annotations

import re

_MRP_ANCHOR = re.compile(
    r"m\s*\.?\s*r\s*\.?\s*p|max(imum)?\s*retail\s*price|retail\s*price|"
    r"incl.*all\s*taxes|rs\.?|inr|\u20b9", re.I)
_FSSAI_ANCHOR = re.compile(r"fssai|lic\.?\s*no|licen[sc]e", re.I)
# Stage 2C §10: OCR damage on the opener ("FSSA", "LIC N0" with a zero)
# is tolerated for CONTEXT; the 14-digit value gate is unchanged.
_FSSAI_DAMAGED_EXTRA = re.compile(r"fssa\b|lic\s*n\s*0", re.I)
_FSSAI_WORD_DAMAGED = re.compile(r"fssai?|fss[l1i]|essai|f[5s]sai", re.I)
_DATE_ANCHOR = re.compile(
    r"\bmfd\b|\bmfg\b|manufactur|packed?\s*(on|by)?|\bpkd\b|\bpkg\b|"
    r"date\s*of\s*(manufacture|packing|packaging)|best\s*before|use\s*by|"
    r"use-by|expir|consume\s*before", re.I)
_CARE_ANCHOR = re.compile(
    r"care|call|customer|toll|helpline|contact|complaint|phone|tel|"
    r"consumer|service|feedback|@", re.I)
_BATCH_ANCHOR = re.compile(
    r"\bbatch(?:\s*(?:no\.?|number|num\.?))?|\blot(?:\s*(?:no\.?|number|"
    r"num\.?))?|\bb\.?\s*(?:no\.?|number)|\bb\s*/\s*n", re.I)
_MAKER_ANCHOR = re.compile(
    r"manufactured\s+(?:by|for)|marketed\s+(?:by|for)|packed\s+(?:by|for)|"
    r"mfd\s+by|mkd\s+by|mkt\s+by|mfg\s+by|imported\s+by|\bpvt\b|\bltd\b|"
    r"limited|private limited", re.I)
_MAKER_STRIP = re.compile(
    r"^(?:manufactured\s+(?:by|for)|marketed\s+(?:by|for)|"
    r"packed\s+(?:by|for)|mfd\s+by|mkd\s+by|mkt\s+by|mfg\s+by|"
    r"imported\s+by)[\s:.]*", re.I)
_ING_HEADING = re.compile(r"ingredients?|composition|contents|contains",
                          re.I)
_FOREIGN_SECTION = re.compile(
    r"nutrition|storage|store\s*in|keep\s*in|once\s*opened|airtight|"
    r"manufactured\s*by|packed\s*by|marketed\s*by|best\s*before|use\s*by|"
    r"consumer\s*care|customer\s*care|fssai", re.I)
_NUTRIENT_VOCAB = frozenset(
    "energy protein carbohydrate carbohydrates totalfat saturatedfat "
    "transfat total sugars added sugars sodium serving size per "
    "fat fibre fiber cholesterol calcium iron vitamin".split())
_VALID_UNITS = frozenset(
    "kg g mg l ml pcs inr rs".split())
_PHONE_RUN = re.compile(r"(\+?91[\s-]?\d{10}|1800[\s-]?\d{3,4}[\s-]?\d{3,4}|"
                        r"\b[6-9]\d{9}\b)")
# Stage 2B §7: net-quantity context words. A standalone small count
# ("1", "2") is a serving/pack count, nutrition value, or percentage —
# never a net quantity — unless this context (or strong spatial
# association supplied by the caller) says otherwise.
_QTY_NET_CTX = re.compile(
    r"net\b|net\s*wt|qty|quantity|weight|\bwt\b|contents?|pack\s*contains|"
    r"e-mark|\u212e", re.I)
# Stage 2C §6: count/serving/preparation/nutrition language that must
# never read as net quantity ("Pack of 4", "4 servings", "1 packet",
# preparation quantities, "12%" nutrition values).
_QTY_COUNT_CTX = re.compile(
    r"servings?|serves?\b|serve\b|pack\s*of|packets?|per\s*serv|prepar|"
    r"\d\s*%|%\s*(daily|dv)|makes\s+\d|yields\s+\d", re.I)
# Stage 2B §7 / 2C §8: batch/lot stop-values. Bare label furniture
# ("No" from "Batch No", NET/NOT/NEW/PACK/LOT fragments, "NA",
# dashes) is never a batch code — unless part of an actual batch
# declaration (which carries Batch/Lot context validated elsewhere).
_BATCH_STOP = frozenset(
    {"no", "na", "n/a", "nil", "none", "-", "--", "null", "nr", "nil.",
     "net", "not", "new", "pack", "lot"})
# Fatal inside an ingredient VALUE even beside a heading (region
# boundary is wrong, not the wording).
_ING_VALUE_FATAL = re.compile(
    r"per\s*100|approximate\s*values?|nutrition(al)?\s*information|"
    r"serving\s*size|nutritive\s*value", re.I)
# Stage 2B §7 / 2C §12: ingredient contamination markers. Nutrition
# tables, serving/storage/preparation blocks, care/manufacturer
# sections, price/batch/date declarations, and marketing slogans must
# never enter the ingredient list. Strong stop markers end the block.
_ING_FORBIDDEN = re.compile(
    r"nutrition|nutritive|per\s*100|approximate\s*values?|serving|"
    r"storage|store\s*in|prepar|directions?\s+for\s+use|"
    r"consumer\s*care|customer\s*care|helpline|toll\s*free|"
    r"manufactured\s*by|marketed\s*by|packed\s*by|"
    r"best\s*before|use\s*by|fssai|mfg\b|"
    r"mrp|max\s*retail\s*price|lot\s*no|batch\s*no|"
    r"ingredients?\s*not\s*found",
    re.I)


def validate_mrp_candidate(value: str | None,
                           evidence: str | None = None) -> tuple[bool, str]:
    """MRP: numeric amount + MRP/Rs context; reject phone/FSSAI/nutrition."""
    if value is None or str(value).strip() == "":
        return False, "blank"
    text = str(value).strip()
    cleaned = text.replace(",", "").strip()
    try:
        amount = float(cleaned)
    except ValueError:
        return False, "not numeric"
    if amount <= 0:
        return False, "non-positive amount"
    if len(cleaned.split(".")[0]) > 5:
        return False, "digit run too long (phone/fssai/barcode?)"
    digits = re.sub(r"\D", "", cleaned)
    if len(digits) >= 10:
        return False, "phone-length digit run rejected"
    if len(digits) == 14:
        return False, "fssai-length digit run rejected"
    ev = str(evidence or "")
    # Stage 2B §7: context must be VISIBLE — in the evidence line or
    # carried by the value itself ("Rs. 108", "MRP 108"). A bare
    # plausible number ("108") with anchorless evidence is never an
    # MRP. (ocr_fields.validate_mrp is plausibility-only and must not
    # stand in for context.)
    if not _MRP_ANCHOR.search(f"{ev} {text}"):
        return False, "no MRP/currency context"
    return True, "numeric amount with MRP/currency context"


def validate_quantity_candidate(value: str | None, unit: str | None = None,
                                evidence: str | None = None
                                ) -> tuple[bool, str]:
    """Quantity: numeric amount + valid unit + net-quantity evidence.

    Stage 2C §6: a bare number ("1", "4", "8", "270") is never a net
    quantity by itself — explicit NET QUANTITY / NET WT / NET WEIGHT /
    CONTENTS context (or strong spatial association supplied as
    evidence) is required. Pack counts ("Pack of 4"), servings
    ("4 servings"), preparation quantities, and nutrition values are
    rejected, not reinterpreted.
    """
    if value is None or str(value).strip() == "":
        return False, "blank"
    try:
        amount = float(str(value).replace(",", "").strip().split()[0])
    except ValueError:
        return False, "not numeric"
    if amount <= 0:
        return False, "non-positive amount"
    u = (unit or "").strip().lower()
    if u not in _VALID_UNITS and u not in ("pcs", "nos", "g", "kg", "ml",
                                           "l", "mg"):
        # unit embedded in value ("70 g") is acceptable too
        m = re.search(r"([\d.,]+)\s*([a-zA-Z]+)", str(value))
        if not m or m.group(2).lower() not in _VALID_UNITS \
                and m.group(2).lower() not in ("pcs", "gms", "gm", "ltr"):
            return False, f"invalid unit {unit!r}"
    text = str(value).strip()
    ev = str(evidence or "")
    combined = f"{ev} {text} {u}"
    # A standalone small count ("1", "2") is the classic serving/pack
    # fragment — reported specifically so reviewers see why.
    if amount in (1.0, 2.0) and not _QTY_NET_CTX.search(combined):
        return False, "standalone small count without net-quantity " \
            "context (serving/pack count?)"
    # Count/serving/preparation/nutrition fragments are never net
    # quantity unless genuine net context is also present.
    if _QTY_COUNT_CTX.search(combined) \
            and not _QTY_NET_CTX.search(combined):
        return False, "count/serving/preparation context, not net " \
            "quantity (pack of N, N servings?)"
    if not _QTY_NET_CTX.search(combined):
        return False, "no net-quantity context (NET QUANTITY/WT/WEIGHT)"
    if _FOREIGN_SECTION.search(ev) and not _QTY_NET_CTX.search(ev):
        return False, "nutrition/foreign-section context"
    return True, "numeric amount with net-quantity context"


def validate_date_candidate(value: str | None,
                            evidence: str | None = None
                            ) -> tuple[bool, str]:
    """Dates: valid format + MFD/BEST-BEFORE context; reject decimals."""
    from app.services.ocr import fields as ocr_fields

    if value is None or str(value).strip() == "":
        return False, "blank"
    text = str(value).strip()
    if not ocr_fields.validate_date(text):
        return False, "invalid date format (e.g. 70.16 rejected)"
    ev = str(evidence or "")
    if ev and not _DATE_ANCHOR.search(ev):
        return False, "no date anchor (mfd/best-before/use-by)"
    return True, "valid date with anchor"


def validate_fssai_candidate(value: str | None,
                             evidence: str | None = None
                             ) -> tuple[bool, str]:
    """FSSAI: licence context + 14-digit structure; reject phone/batch."""
    from app.services.ocr import fields as ocr_fields

    if value is None or str(value).strip() == "":
        return False, "blank"
    digits = re.sub(r"\D", "", str(value))
    if len(digits) != 14:
        return False, "must be 14 digits"
    ev = str(evidence or "")
    if ev and not (_FSSAI_ANCHOR.search(ev)
                   or _FSSAI_WORD_DAMAGED.search(ev)
                   or _FSSAI_DAMAGED_EXTRA.search(ev)):
        return False, "no FSSAI/licence context"
    if digits[0] in "6789" and _PHONE_RUN.fullmatch(digits[:10] or ""):
        return False, "phone-like run rejected"
    if not ocr_fields.validate_fssai(digits):
        return False, "failed structural check"
    return True, "14-digit licence with context"


def validate_care_candidate(value: str | None,
                            evidence: str | None = None
                            ) -> tuple[bool, str]:
    """Consumer care: phone/email/web evidence + care context preferred.

    Stage 2C §11: formatting is normalised (spaces/dashes) but the
    underlying number is preserved; the care region must not
    contaminate ingredients/manufacturer/storage (callers keep regions
    separate; the ingredient validator rejects care markers).
    """
    if value is None or str(value).strip() == "":
        return False, "blank"
    text = str(value).strip()
    has_phone = bool(_PHONE_RUN.search(text))
    has_mail = "@" in text or "http" in text.lower() or "www." in text.lower()
    if not (has_phone or has_mail):
        return False, "no phone/email/web evidence"
    return True, "contact evidence present"


def normalize_care_number(value: str | None) -> str | None:
    """Canonical care digits (Stage 2C §11): strip separators, keep a
    leading +; letters/emails pass through unchanged. Never invents
    digits — returns None for blank input."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if "@" in text or "http" in text.lower() or "www." in text.lower():
        return text
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    return ("+" if text.startswith("+") else "") + digits


def validate_batch_candidate(value: str | None,
                             evidence: str | None = None
                             ) -> tuple[bool, str]:
    """Batch/lot: Batch/Lot context; reject PIN/phone/licence/MRP."""
    if value is None or str(value).strip() == "":
        return False, "blank"
    text = str(value).strip()
    # Stage 2B §7: "No" (a "Batch No" label fragment), "NA", dashes, and
    # friends are label furniture — never a batch code.
    if text.strip(" .:").lower() in _BATCH_STOP:
        return False, "label fragment, not a batch code (e.g. 'No')"
    digits = re.sub(r"\D", "", text)
    if len(digits) == 14:
        return False, "fssai-length run rejected"
    if len(digits) >= 10 and digits.isdigit():
        return False, "phone-length run rejected"
    ev = str(evidence or "")
    if ev and not _BATCH_ANCHOR.search(ev):
        return False, "no batch/lot context"
    if not re.fullmatch(r"[A-Za-z0-9/\-]{2,20}", text):
        return False, "bad batch shape"
    return True, "batch code with context"


def validate_manufacturer_candidate(
        value: str | None, evidence: str | None = None
) -> tuple[bool, str]:
    """Manufacturer: maker-opener context or company designator required.

    A bare resemblance to a company name is not enough (Stage 2B §7).
    Opener prefixes are stripped, never rewritten; see
    normalize_maker_spacing for the known OCR spacing fixes.
    """
    if value is None or str(value).strip() == "":
        return False, "blank"
    text = str(value).strip()
    if len(text) < 3:
        return False, "too short"
    ev = str(evidence or "")
    # Stage 2B §7: opener context is mandatory — a bare resemblance to
    # a company name is never enough. (Evidence defaults to nothing:
    # callers must supply the source line; the OCR overlay does.)
    if not ev or not _MAKER_ANCHOR.search(ev):
        return False, "no maker context (mfg-by/marketed-by/...)"
    return True, "maker context present"


def normalize_maker_spacing(value: str | None) -> str | None:
    """Known OCR spacing/column-cut fixes only; never invents characters.

    - inserts missing spaces in glued all-caps runs ("NESTLEINDIA" stays
      as-is: word segmentation would be invention — only spacing around
      punctuation/designators is normalised);
    - drops a dangling single capital letter fragment from an OCR column
      cut ("... LIMITED B" -> "... LIMITED").
    Returns None for blank input.
    """
    if value is None or str(value).strip() == "":
        return None
    text = re.sub(r"\s+", " ", str(value)).strip(" ,:-")
    text = re.sub(r"\s+[A-Z]\s*$", "", text)  # column-cut fragment
    text = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", ". ", text)
    return re.sub(r"\s+", " ", text).strip(" ,:-") or None


def validate_ingredient_candidate(
        value: str | None, evidence: str | None = None
) -> tuple[bool, str]:
    """Ingredients: heading/context + ingredient-like language; exclude
    nutrition/storage/care/manufacturer sections."""
    if value is None or str(value).strip() == "":
        return False, "blank"
    text = str(value).strip()
    ev = str(evidence or "")
    # Stage 2C §12: nutrition-table phrases inside the VALUE itself are
    # fatal — a genuine ingredient list never contains "per 100g",
    # "approximate values", or a "nutrition information" block, even
    # when an INGREDIENTS heading is also present (heading + dump =
    # wrong region boundary, not a clean list).
    if _ING_VALUE_FATAL.search(text):
        return False, "nutrition-table text inside ingredient value " \
            "(wrong region boundary)"
    # Stage 2B §7: contamination guard — nutrition tables, serving/
    # storage/preparation blocks, care/manufacturer sections, and
    # marketing text must never enter the ingredient list.
    if _ING_FORBIDDEN.search(text) and not _ING_HEADING.search(text):
        return False, "foreign-section contamination in value"
    if ev and _ING_FORBIDDEN.search(ev) and not _ING_HEADING.search(ev):
        return False, "no ingredient heading context"
    if ev and not _ING_HEADING.search(ev):
        return False, "no ingredient heading context"
    if "," not in text and len(text.split()) < 2:
        return False, "not ingredient-like language"
    return True, "ingredient-like list with heading"


def validate_nutrition_candidate(field: str, value: str | None,
                                unit: str | None = None,
                                evidence: str | None = None
                                ) -> tuple[bool, str]:
    """Nutrition: known nutrient vocabulary + numeric + unit (Stage 2B §8).

    Row/column discipline: when row evidence is supplied it must mention
    the nutrient (or a nutrition-table anchor) — values must never shift
    from one row into another.
    """
    if value is None or str(value).strip() == "":
        return False, "blank"
    if field.strip().lower().replace(" ", "") not in _NUTRIENT_VOCAB \
            and field.strip().lower() not in _NUTRIENT_VOCAB:
        # allow unknown-but-plausible nutrient rows only with evidence
        if not evidence:
            return False, f"unknown nutrient {field!r}"
    try:
        float(str(value).replace(",", "").strip().split()[0])
    except ValueError:
        return False, "not numeric"
    ev = str(evidence or "")
    if ev:
        nutrient_tok = re.sub(r"[^a-z]", "",
                              field.strip().lower().replace(" ", ""))
        ev_nospace = re.sub(r"[^a-z]", "", ev.lower())
        # Stage 2C §13 table context: the nutrient row, a nutrition
        # heading, or a per-100/per-serve table anchor must be visible.
        if nutrient_tok and nutrient_tok not in ev_nospace \
                and "nutrition" not in ev.lower() \
                and "nutritive" not in ev.lower() \
                and "per100" not in ev_nospace \
                and "per100g" not in ev_nospace \
                and "perserve" not in ev_nospace \
                and "table" not in ev.lower() \
                and "column" not in ev.lower():
            return False, "row evidence missing (possible row shift)"
    return True, "numeric nutrient value"


def validate_veg_nonveg_candidate(value: str | None,
                                 evidence: str | None = None
                                 ) -> tuple[bool, str]:
    """Veg/non-veg: vision-first symbol evidence required (Stage 2B §7).

    Returns VEG / NON_VEG only with symbol evidence; UNKNOWN (or blank)
    is never a detection — the overlay maps it to NOT_DETECTED. Status
    is never inferred from ingredients alone.
    """
    if value is None or str(value).strip() == "":
        return False, "blank -> UNKNOWN"
    v = str(value).strip().upper().replace("-", "_").replace(" ", "_")
    if v in ("UNKNOWN", "UNCLEAR", "NOT_DETECTED"):
        return False, "unknown symbol"
    if v in ("VEG", "VEGETARIAN", "VEG_SYMBOL"):
        v = "VEGETARIAN"
    elif v in ("NON_VEG", "NONVEG", "NON_VEGETARIAN", "NON_VEG_SYMBOL"):
        v = "NON_VEG"
    else:
        return False, "unrecognised symbol value"
    ev = str(evidence or "")
    if ev and "symbol" not in ev.lower() and "logo" not in ev.lower() \
            and "dot" not in ev.lower() and "mark" not in ev.lower():
        return False, "no symbol evidence"
    return True, "symbol evidence present"


VALIDATORS = {
    "mrp": validate_mrp_candidate,
    "quantity": validate_quantity_candidate,
    "manufacturing_date": validate_date_candidate,
    "best_before": validate_date_candidate,
    "use_by": validate_date_candidate,
    "fssai_license": validate_fssai_candidate,
    "consumer_care": validate_care_candidate,
    "batch_lot": validate_batch_candidate,
    "batch": validate_batch_candidate,
    "manufacturer": validate_manufacturer_candidate,
    "ingredients": validate_ingredient_candidate,
    "veg_nonveg": validate_veg_nonveg_candidate,
}


def validate_field(field: str, value: str | None, **kwargs: object
                   ) -> tuple[bool, str]:
    """Dispatch to the field validator; unknown fields pass through with
    a neutral reason (never silently rejected, never invented).

    Only the keyword arguments each validator actually accepts are
    passed (evidence must never be silently dropped on a signature
    mismatch — that would wrongly reject grounded candidates).
    """
    import inspect as _inspect

    fn = VALIDATORS.get(field)
    if fn is None:
        if value is None or str(value).strip() == "":
            return False, "blank"
        return True, "no specific validator (passthrough)"
    try:
        accepted = set(_inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        accepted = {"value", "evidence"}
    filtered = {k: v for k, v in kwargs.items() if k in accepted}
    try:
        return fn(value, **filtered)  # type: ignore[call-arg]
    except TypeError:
        return fn(value)  # type: ignore[call-arg,return-value]


def cross_validate_final(fields):
    """Deterministic second gate after OCR + Vision (Stage 2C §16).

    Applies ONLY to vision-influenced DETECTED entries (sources include
    "vision"); pure-OCR entries are never demoted here (they already
    passed the OCR anchor gates — validator technicalities must not
    rewrite OCR evidence). A demoted entry keeps its candidates and
    gains an explicit reason; values are never deleted or invented.

    Priority order (§17): explicit visual evidence > OCR evidence >
    vision evidence > cross-image agreement > this validation.
    """
    import copy as _copy

    out = {k: _copy.deepcopy(v) for k, v in (fields or {}).items()}
    for key, entry in out.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "DETECTED":
            continue
        if "vision" not in (entry.get("sources") or []):
            continue
        value = entry.get("final_value")
        evidence = " ".join(str(e or "") for e in
                            (entry.get("evidence") or []))
        ok, reason = _final_evidence_check(key, value, evidence,
                                           entry.get("unit"))
        if not ok:
            entry["status"] = "NEEDS_REVIEW"
            entry["needs_review_reason"] = f"cross-field: {reason}"
            entry.setdefault("score_reasons", []).append(
                f"cross-field: {reason}")
    return out


def _final_evidence_check(field, value, evidence, unit=None):
    """Per-field evidence recheck for vision-influenced detections."""
    text = "" if value is None else str(value)
    ev = evidence or ""
    combined = f"{ev} {text}"
    base = str(field).split(":", 1)[0]
    if base == "quantity":
        if not _QTY_NET_CTX.search(combined):
            return False, "quantity needs quantity evidence"
        return True, "ok"
    if base == "mrp":
        if not _MRP_ANCHOR.search(combined):
            return False, "mrp needs price evidence"
        return True, "ok"
    if base in ("batch_lot", "batch"):
        if not _BATCH_ANCHOR.search(combined):
            return False, "batch needs batch evidence"
        return True, "ok"
    if base in ("manufacturing_date", "best_before", "use_by"):
        if not _DATE_ANCHOR.search(combined):
            return False, "date needs date evidence"
        return True, "ok"
    if base == "fssai_license":
        if len(re.sub(r"\D", "", text)) != 14:
            return False, "fssai needs license-number structure"
        return True, "ok"
    if base == "consumer_care":
        ok, _ = validate_care_candidate(text, ev or None)
        if not ok:
            return False, "phone needs phone structure"
        return True, "ok"
    if base == "manufacturer":
        if not _MAKER_ANCHOR.search(ev):
            return False, "manufacturer needs manufacturer context"
        return True, "ok"
    if base == "ingredients":
        if _ING_FORBIDDEN.search(text) \
                and not _ING_HEADING.search(text):
            return False, "ingredients must not contain " \
                "nutrition/storage/marketing markers"
        return True, "ok"
    if base == "veg_nonveg":
        if not any(w in ev.lower() for w in
                   ("symbol", "logo", "dot", "mark")):
            return False, "veg needs image/symbol evidence"
        return True, "ok"
    if base in _NUTRIENT_VOCAB or base in (
            "total_sugars", "added_sugars", "saturated_fat", "trans_fat",
            "serving_size", "nutrition_basis"):
        lowered = ev.lower()
        if not any(w in lowered for w in
                   ("nutrition", "nutritive", "table", "per 100",
                    "per100", "per serve", "perserve")) \
                and base.replace("_", "") not in re.sub(
                    r"[^a-z]", "", lowered):
            return False, "nutrition needs table context"
        return True, "ok"
    return True, "no specific final gate (passthrough)"
