"""Deterministic field extraction from OCR lines.

Pure functions over detected text — no ML, no guessing. A field is returned
only when a pattern matches; otherwise value is None (never a placeholder).
Each hit carries the mean confidence of the contributing lines plus
provenance (source image label/index and bounding box when available).

Multi-image: lines may come from several package photos (front/back/side)
and preprocessing variants. ``extract_fields`` keeps the legacy
``{value, confidence}`` shape; ``extract_fields_detailed`` additionally
reports ``image`` / ``image_index`` / ``box`` per field.
"""
from __future__ import annotations

import re
from typing import Any

FIELD_KEYS = (
    "product_name", "common_generic_name", "manufacturer",
    "quantity", "unit", "mrp", "batch_lot", "manufacturing_date",
    "best_before", "use_by", "fssai_license", "consumer_care",
    "country_of_origin", "unit_sale_price",
)

# --- MRP: candidate extraction with anchor + plausibility validation ---
# A bare long digit string (barcode "1010005", FSSAI run, phone number) must
# NEVER become an MRP. Every candidate needs an Rs/Rs./INR/rupee-symbol or
# MRP-word anchor AND a plausible packaged-goods price.
_MRP_PREFIX = (
    r"(?:m\s*\.?\s*r\s*\.?\s*p\s*\.?|maximum\s+retail\s+price)"
)
_MRP_RES = [
    re.compile(
        _MRP_PREFIX + r"[^0-9]{0,40}?(?:rs\.?|inr|\u20b9)?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/-)?",
        re.I),
    re.compile(r"\u20b9\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/-)?"),
    re.compile(r"\brs\.?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/-)?", re.I),
]
_MRP_CTX = re.compile(
    r"m\s*\.?\s*r\s*\.?\s*p\s*\.?|maximum\s+retail\s+price|"
    r"incl\.?\s*of\s*all\s*taxes|inclusive\s*of\s*all\s*taxes",
    re.I)
# A bare-Rs candidate whose digits run straight into a date ("Rs. 05/2024")
# or a weight unit ("Rs. 5 g") is a date/weight fragment, never a price.
_MRP_DATE_TAIL = re.compile(r"^[/\-.]\d")
_MRP_WEIGHT_TAIL = re.compile(
    r"^\s*(?:kg|kgs|gms?|gm|g|mg|ml|ltr?s?|litres?|pcs?|nos?)\b", re.I)
_MRP_ANCHOR = re.compile(r"rs\.?|inr|\u20b9", re.I)
# Packaged-food MRP integer part: at most 5 digits (<= 99999.99). Anything
# longer is a barcode / FSSAI / phone / batch artefact, never a price.
_MRP_MAX_INT_DIGITS = 5
# --- net quantity: contextual keywords + broad unit coverage ---
_QTY_RE = re.compile(
    r"([\d]+(?:[.,]\d+)?)\s*"
    r"(kg|kgs|kilo(?:gram)?s?|gram(?:me)?s?|gms?|gm|g|mg|ml|ltr(?:s)?|ltr|"
    r"l|litre|liter|pcs?|nos?|numbers?)\b", re.I)
_QTY_CTX = re.compile(
    r"\bnet\b|\bqty\b|quantity|weight|\bwt\b|contents?\b|pack\s*contains|"
    r"e-mark|℮", re.I)
# Spaceless-tolerant anchor for candidate scoring only: OCR merges print
# ("NETWT70g"), defeating \b boundaries. Substring fallback, used solely
# to credit a quantity sitting on the same line as net-context print.
_QTY_CTX_LOOSE = re.compile(r"net|qty|quantity|weight|contents?", re.I)


def _qty_anchored_same_line(text: str) -> bool:
    return bool(_QTY_CTX.search(text) or _QTY_CTX_LOOSE.search(text))
# --- dates ---
# Digit lookarounds (not \b): OCR often merges print without spaces
# ("MFD05/2024FSSAI..."), and D->0 / 4->F are not word boundaries.
_DATE_MY = re.compile(r"(?<!\d)(\d{1,2}[./-]\d{4})(?!\d)")
_DATE_FULL = re.compile(r"(?<!\d)(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})(?!\d)")
_MONTHS = (r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
           r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
           r"Nov(?:ember)?|Dec(?:ember)?")
_DATE_MY_TEXT = re.compile(r"\b((?:" + _MONTHS + r")\s+\d{4})\b", re.I)
_DATE_FULL_TEXT = re.compile(
    r"\b(\d{1,2}\s+(?:" + _MONTHS + r")\s+\d{2,4})\b", re.I)


def _find_date(text: str) -> re.Match | None:
    """First date-like token: full numeric, textual, then month/year."""
    return (_DATE_FULL.search(text) or _DATE_FULL_TEXT.search(text)
            or _DATE_MY.search(text) or _DATE_MY_TEXT.search(text))
_MFG_CTX = re.compile(
    r"\bmfd\b|\bmfg\b|mfd(?=[\d.])|mfg(?=[\d.])|manufactur|"
    r"packed(?:\s*on)?|\bpkd\b|\bpkg\b|pkd(?=[\d.])|"
    r"date\s*of\s*(?:manufacture|packing|packaging)", re.I)
_BB_CTX = re.compile(
    r"best\s*before|use\s*by|use-by|expir|best\s*if\s*used\s*by", re.I)
_BB_DURATION = re.compile(
    r"best\s*(?:before|within)\s*(\d+\s*(?:days?|months?|years?))", re.I)
_FSSAI_CTX = re.compile(r"fssai|fsslai|lic\.?\s*no|licence|license", re.I)
_FSSAI_NUM = re.compile(r"(\d[\d\s]{12,20}\d)")
_PHONE_1800 = re.compile(r"(1800[\s-]?\d{3,4}[\s-]?\d{3,4})")
_PHONE_91 = re.compile(r"(\+?91[\s-]?\d{10})")
_PHONE_10 = re.compile(r"\b([6-9]\d{9})\b")
_CARE_CTX = re.compile(
    r"care|call|customer|toll|helpline|contact|complaint|phone|tel|"
    r"consumer\s+complaint|contact\s+us", re.I)
_BATCH_RE = re.compile(
    r"(?:batch|lot|b\.?\s*no)(?:\s*no\.?)?[\s:.]*([A-Za-z0-9][A-Za-z0-9/\-]{1,19})",
    re.I)
_MAKER_CTX = re.compile(
    r"manufactured by|marketed by|packed by|mfd by|mkd by|imported by|"
    r"\bpvt\b|\bltd\b|limited|private limited", re.I)
_MAKER_STRIP = re.compile(
    r"^(?:manufactured by|marketed by|packed by|mfd by|mkd by|imported by)[\s:]*",
    re.I)
_COO_RE = re.compile(
    r"(?:country of origin|made in|manufactured in|product of)"
    r"\s*:?\s*([A-Za-z][A-Za-z ]{1,30})", re.I)
_USP_RE = re.compile(r"per\s*(kg|g|ml|l|litre|pc|pcs|piece|meter|m)\b", re.I)
_NUMERIC_LINE = re.compile(r"^[\d\s\u20b9Rs.,/:\-]+$", re.I)

_UNIT_CANON = {
    "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg",
    "kilograms": "kg", "gram": "g", "grams": "g", "gramme": "g",
    "grammes": "g", "gm": "g", "gms": "g", "litre": "l", "liter": "l",
    "ltrs": "l", "ltr": "l", "pc": "pcs", "nos": "pcs", "no": "pcs",
    "number": "pcs", "numbers": "pcs",
}


def _mean(confs: list[float]) -> float | None:
    vals = [c for c in confs if c]
    return round(sum(vals) / len(vals), 3) if vals else None


def _clean_num(raw: str) -> str:
    return raw.replace(",", "").replace(" ", "")


def _canon_unit(raw: str) -> str:
    return _UNIT_CANON.get(raw.strip().lower(), raw.strip().lower())


# --- quantities: grams range considered plausible for a retail package ---
_QTY_MIN_G = 0.01
_QTY_MAX_G = 100000.0
_G_PER_UNIT = {
    "kg": 1000.0, "g": 1.0, "mg": 0.001,
    "l": 1000.0, "ml": 1.0,
}


def _qty_grams(number: str, unit: str) -> float | None:
    """Numeric value in gram-equivalents, or None when not parseable."""
    try:
        num = float(number.replace(",", ""))
    except (TypeError, ValueError):
        return None
    factor = _G_PER_UNIT.get(_canon_unit(unit))
    if factor is None:  # count units (pcs/nos): plausibility is count-based
        return num
    return num * factor


def _qty_plausible(number: str, unit: str) -> bool:
    grams = _qty_grams(number, unit)
    if grams is None:
        return False
    if _canon_unit(unit) in ("pcs",):
        return 0 < grams <= 10000
    return _QTY_MIN_G <= grams <= _QTY_MAX_G


def _mrp_valid(number: str) -> bool:
    """Plausibility gate for an MRP candidate (digits before any decimal).

    Rejects long numeric strings — barcodes ("1010005"), FSSAI runs, phone
    numbers, batch codes — even when an MRP anchor sits nearby (OCR often
    merges neighbouring print into one line).
    """
    cleaned = _clean_num(number)
    int_part = cleaned.split(".")[0]
    if not int_part.isdigit():
        return False
    if len(int_part) > _MRP_MAX_INT_DIGITS:
        return False
    try:
        return float(cleaned) > 0
    except ValueError:
        return False


def _box_gap(box_a: Any, box_b: Any) -> float | None:
    """Vertical gap between two OCR boxes in box units (None if unusable)."""
    try:
        ys_a = [float(p[1]) for p in box_a]
        ys_b = [float(p[1]) for p in box_b]
    except (TypeError, IndexError, ValueError):
        return None
    if not ys_a or not ys_b:
        return None
    top_a, bot_a = min(ys_a), max(ys_a)
    top_b, bot_b = min(ys_b), max(ys_b)
    if bot_a <= top_b:
        return top_b - bot_a
    if bot_b <= top_a:
        return top_a - bot_b
    return 0.0  # overlapping rows


def _line_height(box: Any) -> float | None:
    try:
        ys = [float(p[1]) for p in box]
    except (TypeError, IndexError, ValueError):
        return None
    return (max(ys) - min(ys)) if ys else None


def _line_parts(lines: list[Any]) -> list[tuple[str, float, Any, Any]]:
    """Normalise OCR lines to (text, confidence, image, box)."""
    out: list[tuple[str, float, Any, Any]] = []
    for idx, ln in enumerate(lines):
        text = getattr(ln, "text", "") or ""
        try:
            conf = float(getattr(ln, "confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        image = getattr(ln, "image", "") or ""
        box = getattr(ln, "box", None)
        if not image:
            image = idx  # fall back to positional index
        out.append((text, conf, image, box))
    return out


def extract_fields(lines: list[Any]) -> dict[str, dict[str, Any]]:
    """Legacy shape: {field: {value, confidence}}."""
    detailed = extract_fields_detailed(lines)
    return {k: {"value": v["value"], "confidence": v["confidence"]}
            for k, v in detailed.items()}


# Confidence floor below which an extracted value is review-only.
STATUS_CONF_THRESHOLD = 0.6


def _anchors_present(lines: list[Any]) -> dict[str, bool]:
    """Whether each field's contextual anchor appears anywhere.

    An anchor with no parseable value means OCR uncertainty
    (NEEDS_REVIEW); no anchor at all means NOT_DETECTED. Neither is a
    legal violation — only the rule engine + inspector decide that.
    """
    corpus = "\n".join(str(getattr(ln, "text", "") or "") for ln in lines)
    bare_phone = bool(_PHONE_1800.search(corpus)
                      or _PHONE_91.search(corpus))
    substantial = any(
        len(s.strip()) >= 3 and not _NUMERIC_LINE.match(s.strip())
        and not any(rx.search(s) for rx in
                    (_MRP_RES[0:1] + [_QTY_RE, _BATCH_RE, _COO_RE,
                                      _MAKER_CTX, _USP_RE]))
        for s in corpus.split("\n"))
    return {
        "product_name": substantial,
        "common_generic_name": substantial,
        "manufacturer": bool(_MAKER_CTX.search(corpus)),
        "quantity": bool(_QTY_CTX.search(corpus)),
        "unit": bool(_QTY_CTX.search(corpus)),
        "mrp": bool(_MRP_CTX.search(corpus)),
        "batch_lot": bool(_BATCH_RE.search(corpus)),
        "manufacturing_date": bool(_MFG_CTX.search(corpus)),
        "best_before": bool(_BB_CTX.search(corpus)),
        "use_by": bool(_BB_CTX.search(corpus)),
        "fssai_license": bool(_FSSAI_CTX.search(corpus)),
        "consumer_care": bool(_CARE_CTX.search(corpus) or bare_phone),
        "country_of_origin": bool(_COO_RE.search(corpus)),
        "unit_sale_price": bool(_USP_RE.search(corpus)),
    }


def extract_with_status(lines: list[Any]) -> dict[str, dict[str, Any]]:
    """Detailed extraction plus a per-field review status.

    Status is one of DETECTED / NEEDS_REVIEW / NOT_DETECTED and reflects
    OCR evidence strength only — never a compliance verdict. A present
    value that fails structural validation (e.g. a whole line captured
    as a "date") is NEEDS_REVIEW, never DETECTED.
    """
    detailed = extract_fields_detailed(lines)
    anchors = _anchors_present(lines)
    validators = {
        "mrp": validate_mrp,
        "manufacturing_date": validate_date,
        "best_before": validate_date,
        "use_by": validate_date,
        "fssai_license": validate_fssai,
        "consumer_care": validate_phone,
    }
    out: dict[str, dict[str, Any]] = {}
    for key in FIELD_KEYS:
        hit = detailed[key]
        value, conf = hit["value"], hit["confidence"]
        if value not in (None, ""):
            valid = validators[key](value) if key in validators else True
            status = ("DETECTED" if valid and (conf is None
                      or conf >= STATUS_CONF_THRESHOLD) else "NEEDS_REVIEW")
        else:
            status = ("NEEDS_REVIEW" if anchors.get(key) else "NOT_DETECTED")
        out[key] = {**hit, "status": status,
                    "anchor_found": bool(anchors.get(key))}
    return out


def extract_fields_detailed(lines: list[Any]) -> dict[str, dict[str, Any]]:
    """Map OCR lines to structured fields with provenance.

    Every FIELD_KEYS key is always present. Each hit carries ``value``,
    ``confidence`` (mean of contributing lines), ``image`` (source image
    label), ``image_index`` (position of the source line) and ``box``.
    """
    parts = _line_parts(lines)
    texts = [(t, c) for t, c, _, _ in parts]
    out: dict[str, dict[str, Any]] = {
        k: {"value": None, "confidence": None, "image": None,
            "image_index": None, "box": None} for k in FIELD_KEYS}

    def set_field(key: str, value: str, confs: list[float],
                  image: Any = None, image_index: Any = None,
                  box: Any = None) -> None:
        if out[key]["value"] is None:
            out[key] = {"value": value, "confidence": _mean(confs),
                        "image": image, "image_index": image_index,
                        "box": box}

    def _loc(i: int) -> tuple[Any, Any, Any]:
        _, _, image, box = parts[i]
        return image, i, box

    # --- MRP: collect anchored candidates, validate, score, pick best ---
    # Candidate sources (in preference order, scored explicitly):
    #   A. MRP-word line, amount on the same line      (mrp_word=3)
    #   B. MRP-word line, amount on the adjacent line  (mrp_word=2, split=1)
    #   C. bare Rs./rupee amount, no MRP word          (rs_anchor=2)
    # Every candidate must pass _mrp_valid; long digit strings such as
    # "1010005" are rejected even beside an MRP anchor.
    mrp_cands: list[dict[str, Any]] = []

    def _add_mrp(raw_num: str, line_idx: int, confs: list[float],
                 mrp_word: int, split: bool, rs_anchor: bool,
                 tail: str = "") -> None:
        if not _mrp_valid(raw_num):
            return
        if _MRP_DATE_TAIL.match(tail or ""):
            return  # date fragment ("05" of "05/2024"), not a price
        if mrp_word == 0 and _MRP_WEIGHT_TAIL.match(tail or ""):
            return  # bare-Rs number attached to a weight unit
        image, idx, box = _loc(line_idx)
        try:
            conf_mean = sum(confs) / len(confs)
        except ZeroDivisionError:
            conf_mean = 0.0
        mrp_cands.append({
            "value": _clean_num(raw_num),
            "confs": list(confs),
            "score": (mrp_word * 3.0 + (1.0 if split else 0.0)
                      + (2.0 if rs_anchor else 0.0) + conf_mean),
            "image": image, "idx": idx, "box": box,
        })

    for i, (text, conf) in enumerate(texts):
        if not _MRP_CTX.search(text):
            continue
        for rx in _MRP_RES:
            m = rx.search(text)
            if m:
                _add_mrp(m.group(1), i, [conf], mrp_word=3, split=False,
                         rs_anchor=bool(_MRP_ANCHOR.search(text)),
                         tail=text[m.end(1):m.end(1) + 12])
                break
        # "MRP" on one line, amount on the next (tiny back-panel print
        # often wraps): join a 2-line window before matching.
        if i + 1 < len(texts):
            window = text + " " + texts[i + 1][0]
            for rx in _MRP_RES:
                m = rx.search(window)
                if m and _clean_num(m.group(1)) not in {
                        c["value"] for c in mrp_cands}:
                    _add_mrp(m.group(1), i, [conf, texts[i + 1][1]],
                             mrp_word=2, split=True,
                             rs_anchor=bool(_MRP_ANCHOR.search(window)),
                             tail=window[m.end(1):m.end(1) + 12])
                    break
    # Legacy fallback: a bare Rs./rupee amount (e.g. "Rs. 99") with no
    # MRP prefix still counts — historical extraction contract. A bare
    # number with no anchor ("Price: 250", "250", "1010005") never does.
    for i, (text, conf) in enumerate(texts):
        if _MRP_CTX.search(text):
            continue  # already covered above
        for rx in _MRP_RES[1:]:
            m = rx.search(text)
            if m:
                _add_mrp(m.group(1), i, [conf], mrp_word=0, split=False,
                         rs_anchor=True,
                         tail=text[m.end(1):m.end(1) + 12])
                break
    mrp_pick: dict[str, Any] = {"cands": mrp_cands, "best": None}
    if mrp_cands:
        # Provisional pick; the FSSAI/phone cross-check runs as a post-pass
        # below (those fields are extracted later in this function).
        best = max(mrp_cands, key=lambda c: c["score"])
        mrp_pick["best"] = best
        image, idx, box = best["image"], best["idx"], best["box"]
        set_field("mrp", best["value"], best["confs"], image, idx, box)

    # --- quantity + unit: scored candidates, never fabricated ---
    # Every "<number> <unit>" match becomes a candidate scored on:
    #   NET WT/QTY/WEIGHT anchor proximity (same line 3, adjacent 2/1,
    #     corpus-wide 0.5, none 0) + OCR confidence + box proximity bonus.
    # Implausible magnitudes (e.g. "9999999 g") are discarded. The best
    # plausible candidate wins; with no candidate the value stays None.
    qty_cands: list[dict[str, Any]] = []
    ctx_lines = [i for i, (text, _) in enumerate(texts)
                 if _QTY_CTX.search(text)]

    def _add_qty(raw_num: str, raw_unit: str, line_idx: int,
                 confs: list[float], anchor: float) -> None:
        if not _qty_plausible(raw_num, raw_unit):
            return
        image, idx, box = _loc(line_idx)
        try:
            conf_mean = sum(confs) / len(confs)
        except ZeroDivisionError:
            conf_mean = 0.0
        qty_cands.append({
            "number": _clean_num(raw_num),
            "unit": _canon_unit(raw_unit),
            "confs": list(confs),
            "score": anchor * 2.0 + conf_mean,
            "image": image, "idx": idx, "box": box,
        })

    for i, (text, conf) in enumerate(texts):
        for m in _QTY_RE.finditer(text):
            anchor = 3.0 if _qty_anchored_same_line(text) else 0.0
            _add_qty(m.group(1), m.group(2), i, [conf], anchor)
    # Cross-line pairing: "NET WT" on one line, "70 g" nearby. Box geometry
    # confirms the pairing when boxes exist; line-index proximity otherwise.
    if ctx_lines:
        for i in ctx_lines:
            for j in (i - 1, i + 1, i - 2, i + 2):
                if not (0 <= j < len(texts)):
                    continue
                for m in _QTY_RE.finditer(texts[j][0]):
                    dist = abs(j - i)
                    anchor = 2.0 if dist == 1 else 1.0
                    bonus = 0.0
                    gap = _box_gap(parts[i][3], parts[j][3])
                    if gap is not None:
                        height = _line_height(parts[i][3]) or 0.0
                        if gap <= 3 * max(height, 1.0):
                            bonus = 1.0
                        else:
                            continue  # boxes say: too far apart, skip
                    _add_qty(m.group(1), m.group(2), j,
                             [texts[j][1], texts[i][1]], anchor + bonus)
        # Corpus-level: a NET keyword anywhere legitimises bare quantities
        # (context and amount may live on different photos).
        for i, (text, conf) in enumerate(texts):
            for m in _QTY_RE.finditer(text):
                _add_qty(m.group(1), m.group(2), i, [conf], 0.5)
    if qty_cands:
        # Highest score wins (max keeps the first on exact ties, so
        # same-line anchored hits beat later bare ones deterministically).
        best = max(qty_cands, key=lambda c: c["score"])
        set_field("quantity", best["number"], best["confs"],
                  best["image"], best["idx"], best["box"])
        out["unit"] = {"value": best["unit"],
                       "confidence": _mean(best["confs"]),
                       "image": best["image"], "image_index": best["idx"],
                       "box": best["box"]}
        out["quantity"]["qty_candidates"] = [
            {"number": c["number"], "unit": c["unit"],
             "score": round(c["score"], 3)} for c in qty_cands if c is not best
        ][:5]

    # --- dates: MFD/MFG/PKD + BEST BEFORE / USE BY / EXPIRY ---
    for i, (text, conf) in enumerate(texts):
        if out["manufacturing_date"]["value"] is None and _MFG_CTX.search(text):
            m = _find_date(text)
            if m:
                image, idx, box = _loc(i)
                set_field("manufacturing_date", m.group(1), [conf],
                          image, idx, box)
    for i, (text, conf) in enumerate(texts):
        if _BB_CTX.search(text):
            m = _find_date(text)
            if m:
                low = text.lower()
                image, idx, box = _loc(i)
                if "use by" in low or "use-by" in low:
                    set_field("use_by", m.group(1), [conf], image, idx, box)
                else:
                    set_field("best_before", m.group(1), [conf],
                              image, idx, box)
            else:
                dur = _BB_DURATION.search(text)
                if dur and out["best_before"]["value"] is None:
                    image, idx, box = _loc(i)
                    set_field("best_before", dur.group(0).strip(), [conf],
                              image, idx, box)

    # --- FSSAI: 14 digits ONLY with licence context (never a bare barcode) ---
    for i, (text, conf) in enumerate(texts):
        if _FSSAI_CTX.search(text):
            m = _FSSAI_NUM.search(text)
            if m:
                digits = re.sub(r"\D", "", m.group(1))
                if len(digits) == 14:
                    image, idx, box = _loc(i)
                    set_field("fssai_license", digits, [conf],
                              image, idx, box)
                    break
            else:
                # digits possibly split across adjacent lines: join window
                if i + 1 < len(texts):
                    window = text + " " + texts[i + 1][0]
                    m2 = _FSSAI_NUM.search(window)
                    if m2:
                        digits = re.sub(r"\D", "", m2.group(1))
                        if len(digits) == 14:
                            image, idx, box = _loc(i)
                            set_field("fssai_license", digits,
                                      [conf, texts[i + 1][1]],
                                      image, idx, box)
                            break

    # --- consumer care / phone ---
    for i, (text, conf) in enumerate(texts):
        m = _PHONE_1800.search(text) or _PHONE_91.search(text)
        if m:
            image, idx, box = _loc(i)
            set_field("consumer_care", m.group(1), [conf], image, idx, box)
            break
    else:
        for i, (text, conf) in enumerate(texts):
            m = _PHONE_10.search(text)
            if m and _CARE_CTX.search(text):
                image, idx, box = _loc(i)
                set_field("consumer_care", m.group(1), [conf],
                          image, idx, box)
                break

    # --- MRP post-pass: never accept FSSAI/phone digits as the price ---
    if mrp_pick["best"] is not None:
        mrp_digits = re.sub(r"\D", "", mrp_pick["best"]["value"])
        fssai_digits = re.sub(
            r"\D", "", str(out["fssai_license"]["value"] or ""))
        care_digits = re.sub(
            r"\D", "", str(out["consumer_care"]["value"] or ""))
        clash = ((fssai_digits and mrp_digits == fssai_digits)
                 or (care_digits and len(care_digits) >= 6
                     and mrp_digits == care_digits))
        if clash:
            rest = [c for c in mrp_pick["cands"]
                    if c is not mrp_pick["best"]]
            out["mrp"] = {"value": None, "confidence": None, "image": None,
                          "image_index": None, "box": None}
            if rest:
                nxt = max(rest, key=lambda c: c["score"])
                nd = re.sub(r"\D", "", nxt["value"])
                if not ((fssai_digits and nd == fssai_digits)
                        or (care_digits and len(care_digits) >= 6
                            and nd == care_digits)):
                    set_field("mrp", nxt["value"], nxt["confs"],
                              nxt["image"], nxt["idx"], nxt["box"])

    # --- batch / lot ---
    for i, (text, conf) in enumerate(texts):
        m = _BATCH_RE.search(text)
        if m:
            image, idx, box = _loc(i)
            set_field("batch_lot", m.group(1).strip(":- "), [conf],
                      image, idx, box)
            break

    # --- manufacturer: only lines with maker keywords ---
    # Phone-number runs merged into the line by OCR are stripped: the
    # care number is extracted as its own field, never as part of a name.
    _MAKER_PHONE = re.compile(r"\+?\d[\d\s\-]{7,}\d")
    for i, (text, conf) in enumerate(texts):
        if _MAKER_CTX.search(text):
            cleaned = _MAKER_STRIP.sub("", text).strip(" ,:-")
            cleaned = _MAKER_PHONE.sub("", cleaned).strip(" ,:-")
            if cleaned:
                image, idx, box = _loc(i)
                set_field("manufacturer", cleaned, [conf], image, idx, box)
                break

    # --- country of origin ---
    for i, (text, conf) in enumerate(texts):
        m = _COO_RE.search(text)
        if m:
            image, idx, box = _loc(i)
            set_field("country_of_origin", m.group(1).strip(), [conf],
                      image, idx, box)
            break

    # --- unit sale price: presence line ---
    for i, (text, conf) in enumerate(texts):
        if _USP_RE.search(text):
            image, idx, box = _loc(i)
            set_field("unit_sale_price", text.strip(), [conf],
                      image, idx, box)
            break

    # --- product name: first substantial non-numeric, non-field line ---
    # Stage-1B: never infer the product name from declaration/section
    # lines (ingredients, nutrition, storage, care, allergen, feedback,
    # barcode, maker). Packaging identity (brand/front-panel) evidence
    # wins; section openers are skipped even when no value was parsed.
    _NAME_SKIP_EXTRA = re.compile(
        r"nutrition|ingredient|composition|contents\s*:|storage|store\s+in|"
        r"keep\s+in|allergen|contains\s*:|feedback|contact\s+us|"
        r"consumer\s+care|customer\s+care|direction|recipe|warning|"
        r"bar\s*-?\s*code|manufactured\s+by|packed\s+by|marketed\s+by|"
        r"imported\s+by|for\s+feedback", re.I)
    skip_res = (_MRP_RES + [_QTY_RE, _DATE_MY, _DATE_FULL, _DATE_MY_TEXT,
                           _DATE_FULL_TEXT, _FSSAI_NUM,
                           _PHONE_1800, _PHONE_91, _BATCH_RE, _COO_RE,
                           _MAKER_CTX, _BB_CTX, _CARE_CTX, _USP_RE,
                           _NAME_SKIP_EXTRA])
    for i, (text, conf) in enumerate(texts):
        stripped = text.strip()
        if len(stripped) < 3 or _NUMERIC_LINE.match(stripped):
            continue
        if any(rx.search(stripped) for rx in skip_res):
            continue
        image, idx, box = _loc(i)
        set_field("product_name", stripped, [conf], image, idx, box)
        # Engine maps common_generic_name <- product_name; mirror explicitly.
        out["common_generic_name"] = {"value": stripped,
                                      "confidence": _mean([conf]),
                                      "image": image, "image_index": idx,
                                      "box": box}
        break

    return out


# ------------------------------------------------- field validators ---
# Pure value validators: never invent, never OCR. Each returns True only
# for structurally valid values; borderline input stays reviewable.
_EMAIL_RE = re.compile(r"^[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^(?:https?://|www\.)[\w\-./?=%&+#]+$", re.I)
_BARCODE_RUN = re.compile(r"(?<!\d)(\d{8}|\d{12}|\d{13}|\d{14})(?!\d)")


def validate_mrp(value: Any) -> bool:
    """Numeric/currency-plausible MRP (same gate as extraction)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    text = re.sub(r"^(?:rs\.?|inr|\u20b9|m\.?\s*r\.?\s*p\.?)\s*",
                  "", str(value).strip(), flags=re.I)
    return _mrp_valid(text)


def validate_date(value: Any) -> bool:
    """Structurally valid calendar date with a plausible package year."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    text = str(value).strip()
    m = (_DATE_FULL.search(text) or _DATE_FULL_TEXT.search(text)
         or _DATE_MY.search(text) or _DATE_MY_TEXT.search(text))
    if not m:
        return False
    token = m.group(1)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
                "%m/%Y", "%m-%Y", "%m.%Y", "%Y-%m",
                "%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y",
                "%b %Y", "%B %Y"):
        try:
            from datetime import datetime as _dt

            d = _dt.strptime(token, fmt)
            if 1990 <= d.year <= 2040 and 1 <= d.month <= 12:
                return True
        except ValueError:
            continue
    return False


def validate_fssai(value: Any) -> bool:
    """Exactly 14 digits (FSSAI licence-number pattern)."""
    if value is None:
        return False
    return re.fullmatch(r"\d{14}", re.sub(r"\D", "", str(value))) is not None


def validate_phone(value: Any) -> bool:
    """Indian consumer-care phone shapes: 1800-XXX, +91-10-digit, 10-digit."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    text = str(value).strip()
    return bool(_PHONE_1800.search(text) or _PHONE_91.search(text)
                or _PHONE_10.search(text))


def validate_email(value: Any) -> bool:
    """Basic email structure (single @, dot-suffixed domain)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    return _EMAIL_RE.match(str(value).strip()) is not None


def ean13_check_digit(first12: str) -> str | None:
    """GS1 Mod-10 check digit for 12 digits; None when not computable."""
    if not re.fullmatch(r"\d{12}", first12 or ""):
        return None
    total = sum(int(d) * (3 if i % 2 else 1)
                for i, d in enumerate(first12))
    return str((10 - total % 10) % 10)


def find_barcode_candidates(lines: list[Any]) -> list[dict[str, Any]]:
    """Digit runs shaped like retail barcodes (8/12/13/14 digits).

    EAN-13 runs additionally carry checksum validity. Runs matching the
    FSSAI (14-digit with licence context) or phone patterns on the same
    line are excluded — a barcode candidate must not be a licence or
    care number in disguise. Pure evidence surfacing: no decoding claim.
    """
    parts = _line_parts(lines)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, (text, conf) in enumerate([(t, c) for t, c, _, _ in parts]):
        stripped = (text or "").strip()
        if not stripped:
            continue
        has_fssai_ctx = bool(_FSSAI_CTX.search(stripped))
        has_care_ctx = bool(_CARE_CTX.search(stripped)
                            or _PHONE_1800.search(stripped)
                            or _PHONE_91.search(stripped))
        for m in _BARCODE_RUN.finditer(re.sub(r"[\s\-]", "", stripped)):
            digits = m.group(1)
            if digits in seen:
                continue
            seen.add(digits)
            if len(digits) == 14 and has_fssai_ctx:
                continue  # licence number, not a barcode
            if len(digits) == 10 and has_care_ctx:
                continue  # care number, not a barcode
            check_ok: bool | None = None
            if len(digits) == 13:
                check_ok = (ean13_check_digit(digits[:12]) == digits[12])
            _, _, image, box = parts[i]
            out.append({"digits": digits, "length": len(digits),
                        "ean13_checksum_valid": check_ok,
                        "confidence": round(conf, 3), "image": image,
                        "image_index": i, "box": box})
    return out


def decode_barcode(image_crop: Any = None,
                   digits: str | None = None) -> dict[str, Any]:
    """Barcode decoding interface (no decoder backend bundled).

    OCR digit runs are not barcode decodes: bars cannot be verified from
    glyphs. Until a decoder backend (pyzbar/zxing) is vendored, this
    returns NEEDS_MANUAL_SCAN with the candidate evidence attached, so
    callers — and reports — never present an OCR guess as a scan.
    """
    return {"status": "NEEDS_MANUAL_SCAN",
            "value": None,
            "digits": digits,
            "reason": "no barcode decoder backend bundled; verify with a "
                      "scanner and confirm manually"}


def extract_contact_codes(lines: list[Any]) -> dict[str, Any]:
    """Email / website / barcode candidates outside FIELD_KEYS.

    Kept separate (never merged into FIELD_KEYS, whose membership is
    contract-tested) and surfaced as additive evidence by the service.
    """
    parts = _line_parts(lines)
    email = website = None
    econf = wconf = None
    eimg = wimg = None
    eidx = widx = None
    ebox = wbox = None
    for i, (text, conf) in enumerate([(t, c) for t, c, _, _ in parts]):
        stripped = (text or "").strip()
        if not stripped:
            continue
        if email is None:
            for token in re.split(r"\s+", stripped):
                if validate_email(token.strip(",;()")):
                    email = token.strip(",;()")
                    econf, eimg, eidx = round(conf, 3), parts[i][2], i
                    ebox = parts[i][3]
                    break
        if website is None:
            for token in re.split(r"\s+", stripped):
                if _URL_RE.match(token.strip(",;()")):
                    website = token.strip(",;()")
                    wconf, wimg, widx = round(conf, 3), parts[i][2], i
                    wbox = parts[i][3]
                    break
        if email is not None and website is not None:
            break
    return {
        "email": {"value": email, "confidence": econf, "image": eimg,
                  "image_index": eidx, "box": ebox,
                  "status": "DETECTED" if email else "NOT_DETECTED"},
        "website": {"value": website, "confidence": wconf, "image": wimg,
                    "image_index": widx, "box": wbox,
                    "status": "DETECTED" if website else "NOT_DETECTED"},
        "barcode_candidates": find_barcode_candidates(lines),
    }
