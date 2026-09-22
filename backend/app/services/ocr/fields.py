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
    # "Rs" OCR confusions ("R5" for "Rs"): still need a valid price
    # plus the date/weight-tail and FSSAI/phone post-pass gates below.
    re.compile(r"\br(?:s|5)\.?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:/-)?", re.I),
]
_MRP_CTX = re.compile(
    r"m\s*\.?\s*r\s*\.?\s*p\s*\.?|maximum\s+retail\s+price|"
    r"max\s+retail\s+price|retail\s+price|"
    r"incl\.?\s*of\s*all\s*taxes|inclusive\s*of\s*all\s*taxes",
    re.I)
# OCR-damaged MRP words ("MRF"/"MPP"/"NRP", "MAXMUM RETAIL"): accepted
# ONLY beside a currency-anchored amount on the same line, and ranked
# below clean hits. Never a bare discovery path.
_MRP_PREFIX_OCR = (r"(?:m[rn][pf]|max[i1]mum\s+retail\s+price|"
                    r"max\s+retail\s+price|pr[i1l]ce)")
_MRP_CTX_OCR = re.compile(_MRP_PREFIX_OCR, re.I)
_MRP_RES_OCR = [
    re.compile(
        _MRP_PREFIX_OCR + r"[^0-9]{0,40}?(?:rs\.?|inr|\u20b9)\s*"
        r"([\d,]+(?:\.\d{1,2})?)\s*(?:/-)?", re.I),
]
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
    r"pack\s*size|net\s*contents?|"
    r"e-mark|℮", re.I)
# Spaceless-tolerant anchor for candidate scoring only: OCR merges print
# ("NETWT70g"), defeating \b boundaries. Substring fallback, used solely
# to credit a quantity sitting on the same line as net-context print.
_QTY_CTX_LOOSE = re.compile(r"net|qty|quantity|weight|contents?", re.I)
# OCR-damaged net-weight anchors ("NET WEI6HT", "NETWT", "QUANLITY").
# Used everywhere the clean anchor is used; the value still needs a
# real number+unit, so this cannot invent quantities.
_QTY_CTX_OCR = re.compile(r"net\s*wt\.?|wei[i1l]ght|quanlity|netwt", re.I)
# Digit-as-unit confusion ("100 9" for "100 g"): accepted ONLY beside
# a same-line net anchor — never standalone ("Room 9", years, counts).
_QTY_DIGIT_UNIT_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*([96])(?!\d)")
# Stage 3 §7: serving/pack/preparation/nutrition language that must
# never read as net quantity without same-line net context. Per-100 /
# per-serving rows and nutrient rows ("8g protein", "Energy 450 kcal")
# are table content, never the package declaration.
_QTY_COUNT_CTX = re.compile(
    r"servings?|serves?\b|serve\b|pack\s*of|packets?|per\s*serv|prepar|"
    r"per\s*100|nutrition|energy|protein|makes\s+\d|yields\s+\d", re.I)


def _qty_anchored_same_line(text: str) -> bool:
    return bool(_QTY_CTX.search(text) or _QTY_CTX_LOOSE.search(text)
                or _QTY_CTX_OCR.search(text))
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
# Short month/year ("05/24"): weakest token, only ever accepted beside
# an explicit MFG/BEST-BEFORE context (callers gate on that first).
_DATE_MY_SHORT = re.compile(r"(?<!\d)(\d{1,2}[./-]\d{2})(?!\d)")


def _find_date(text: str) -> re.Match | None:
    """First date-like token: full numeric, textual, month/year, short."""
    return (_DATE_FULL.search(text) or _DATE_FULL_TEXT.search(text)
            or _DATE_MY.search(text) or _DATE_MY_TEXT.search(text)
            or _DATE_MY_SHORT.search(text))


def _valid_date_token(token: str | None) -> bool:
    """Strict plausibility for a date token (Stage-1E, Part E).

    Month 01-12, day valid when present, year plausible (1990-2040 for
    four-digit years). "70.16" has no valid interpretation and is
    rejected here — extraction stores blank + NEEDS_REVIEW with reason
    invalid_date_candidate instead of accepting it. Never transforms
    the token into a guessed date.
    """
    if not token or not str(token).strip():
        return False
    text = str(token).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
                "%d.%m.%y", "%m/%Y", "%m-%Y", "%m.%Y", "%m/%y", "%m-%y",
                "%m.%y", "%Y-%m",
                "%d %b %Y", "%d %B %Y", "%d %b %y", "%d %B %y",
                "%b %Y", "%B %Y"):
        try:
            from datetime import datetime as _dt

            d = _dt.strptime(text, fmt)
            if not 1 <= d.month <= 12:
                continue
            if "%Y" in fmt and not 1990 <= d.year <= 2040:
                continue
            if "%y" in fmt and not d.year <= 2040:
                # Two-digit years map to 19xx/20xx; food packages live
                # near the present — accept, the officer confirms.
                pass
            return True
        except ValueError:
            continue
    return False
_MFG_CTX = re.compile(
    r"\bmfd\b|\bmfg\b|mfd(?=[\d.])|mfg(?=[\d.])|manufactur|"
    r"manufactured\s+on|date\s*of\s*manufacture|"
    r"mfg\s*date|mfd\s*date|"
    r"\bmfo\b|\bnfd\b|\bmf[69]\b|"
    r"packed(?:\s*on)?|\bpkd\b|\bpkg\b|pkd(?=[\d.])|packed\s+on|"
    r"date\s*of\s*(?:manufacture|packing|packaging)", re.I)
# OCR-damaged date openers ("MFO"/"NFD"/"MF6"): region proposals only,
# and only beside an actual date token — never a blind crop.
_MFG_CTX_OCR = re.compile(r"\bmfo\b|\bnfd\b|\bmf[69]\b", re.I)
_BB_CTX = re.compile(
    r"best\s*before|best\s*before\s*end|use\s*before|use\s*by|use-by|"
    r"expir|best\s*if\s*used\s*by|consume\s*before", re.I)
_BB_DURATION = re.compile(
    r"best\s*(?:before|within)\s*(\d+\s*(?:days?|months?|years?))", re.I)
_FSSAI_CTX = re.compile(r"fssai|fsslai|lic\.?\s*no|licence|license", re.I)
# OCR-damaged FSSAI words ("FSSA"/"FSSAl"/"ESSAI"/"F5SAI"): value
# extraction and region proposals. A 14-digit run on the same line is
# still mandatory, so this cannot invent licences.
_FSSAI_CTX_OCR = re.compile(r"fssai?|fss[l1i]|essai|f[5s]sai", re.I)
_FSSAI_NUM = re.compile(r"(\d[\d\s]{12,20}\d)")
_PHONE_1800 = re.compile(r"(1800[\s-]?\d{3,4}[\s-]?\d{3,4})")
# OCR digit confusions on the toll-free prefix ("l800"/"180O"): same
# shape gate as the clean pattern, tried alongside it.
_PHONE_1800_OCR = re.compile(
    r"([1lI][8B][0O]{2}[\s-]?\d{3,4}[\s-]?\d{3,4})")
_PHONE_91 = re.compile(r"(\+?91[\s-]?\d{10})")
_PHONE_10 = re.compile(r"\b([6-9]\d{9})\b")
_CARE_CTX = re.compile(
    r"care|call|customer|toll|helpline|contact|complaint|phone|tel|"
    r"consumer\s+complaint|contact\s+us|"
    r"customer\s+service|consumer\s+service", re.I)
# OCR-damaged care words ("Ca11"/"Contaet"/"He1pline"): region
# proposals only, and only beside a phone-like run or @ on the line.
_CARE_CTX_OCR = re.compile(
    r"ca[l1][l1]|conta[ce]t|he[l1]pline|custorner|fee[d]?back", re.I)
# Batch/lot forms: "Batch", "Batch No", "Batch Number", "Lot",
# "Lot No", "Lot Number", "B.No", "B. No.", "B/N". The keyword is
# mandatory — bare alphanumeric codes are never batch values. Keyword
# needs a word boundary ("Plot 12" is not lot+"12") plus a real
# separator ("Lotus" never parses as lot+"us").
_BATCH_RE = re.compile(
    r"(?:\bbatch(?:\s*(?:no\.?|number|num\.?))?|\blot(?:\s*(?:no\.?|number|num\.?))?|"
    r"\bb\.?\s*(?:no\.?|number)|\bb\s*/\s*n)"
    r"[\s:.]+([A-Za-z0-9][A-Za-z0-9/\-]{1,19})",
    re.I)
_MAKER_CTX = re.compile(
    r"manufactured\s+(?:by|for)|marketed\s+(?:by|for)|packed\s+(?:by|for)|"
    r"mfd\s+by|mkd\s+by|mkt\s+by|mfg\s+by|imported\s+by|"
    r"pkg\.?(?:\s*mtrl\.?)?\.?\s*(?:mfd|mkt|mfg)\s*\.?\s*by|"
    r"mtrl\.?\s*(?:mfd|mkt|mfg)\s*\.?\s*by|\bpkg\b|\bmtrl\b|"
    r"mfd\s*\.?\s*by|mkt\s*\.?\s*by|"
    r"\bpvt\b|\bltd\b|limited|private limited", re.I)
_MAKER_STRIP = re.compile(
    r"^(?:manufactured\s+(?:by|for)|marketed\s+(?:by|for)|"
    r"packed\s+(?:by|for)|mfd\s+by|mkd\s+by|mkt\s+by|mfg\s+by|"
    r"pkg\.?(?:\s*mtrl\.?)?\.?\s*(?:mfd|mkt|mfg)\s*\.?\s*by|"
    r"mtrl\.?\s*(?:mfd|mkt|mfg)\s*\.?\s*by|pkg|mtrl|"
    r"mfd\s*\.?\s*by|mkt\s*\.?\s*by|"
    r"imported\s+by)[\s:.]*", re.I)


def normalize_maker_value(raw: str | None) -> str | None:
    """Readable manufacturer value without rewriting company names.

    Strips maker openers (including glued OCR forms like
    "Pkg.Mtrl.Mfd.By:"), spaces out dotted abbreviations
    ("PVT.LTD." -> "PVT. LTD."), collapses whitespace, and drops a
    dangling single capital fragment from an OCR column cut
    ("... LIMITED B" -> "... LIMITED"). Words are never altered,
    added, or removed beyond the opener strip and that cut fix.
    """
    if raw is None:
        return None
    text = _MAKER_STRIP.sub("", str(raw)).strip(" ,:-")
    text = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])", ". ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,:-")
    text = re.sub(r"\s+[A-Z]$", "", text)  # column-cut fragment
    return text or None
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


def _box_center(box: Any) -> tuple[float, float] | None:
    """Center point of an OCR box (None when unusable)."""
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
    except (TypeError, IndexError, ValueError):
        return None
    if not xs or not ys:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _box_area(box: Any) -> float | None:
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
    except (TypeError, IndexError, ValueError):
        return None
    if not xs or not ys:
        return None
    return max(0.0, max(xs) - min(xs)) * max(0.0, max(ys) - min(ys))


def _spatial_neighbors(anchor_idx: int,
                       parts: list[tuple[str, float, Any, Any]],
                       radius_mult: float = 6.0,
                       max_hits: int = 6) -> list[int]:
    """Line indices spatially near an anchor line (Package Intelligence).

    Radius scales with the anchor's own text height, so nearby small
    print ("MRP" label -> "Rs. 50" value below it) is found without a
    full-frame rescan. Returns indices ordered nearest-first, excluding
    the anchor itself. Boxless lines never match (no guessing).
    """
    _, _, _, anchor_box = parts[anchor_idx]
    center = _box_center(anchor_box)
    height = _line_height(anchor_box)
    if center is None or not height or height <= 0:
        return []
    scored: list[tuple[float, int]] = []
    for j, (_, _, _, box) in enumerate(parts):
        if j == anchor_idx:
            continue
        other = _box_center(box)
        if other is None:
            continue
        dist = abs(other[0] - center[0]) + abs(other[1] - center[1])
        if dist <= radius_mult * height:
            scored.append((dist, j))
    scored.sort()
    return [j for _, j in scored[:max_hits]]


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


# --- product identity veto terms (Stage-1E): a candidate that IS one
# of these singletons, or CONTAINS one of these declaration phrases,
# is a nutrition-table row, quantity statement, or section label —
# never a product name ("BISCUITS NET WEIGHT" is rejected; "Protein
# Bar" still qualifies). Matching runs on spaceless lowercase text:
# singletons need whole-candidate equality, phrases need substring —
# never loose single-word substrings ("lot" alone never kills "Lotus").
# Stage 3B.1 additions: serving/directions/storage/claims language.
_PRODUCT_VETO = frozenset(
    "transfat totalfat saturatedfat energy protein carbohydrate "
    "carbohydrates sugars ingredients nutrition fssai mrp manufactured "
    "packed weight license licence batch lot mfd servingsize calories "
    "expiry serving servings directions storage".split())
_PRODUCT_VETO_PHRASES = frozenset(
    "transfat totalfat saturatedfat netweight netwt netquantity "
    "bestbefore useby consumercare customercare maximumretailprice "
    "ingredients nutrition manufacturedby manufacturedfor packedby "
    "mfdby batchno lotno licenceno fssai helpline "
    "servingsize servingper howtouse directionsforuse storageinstructions "
    "keepincool storeincool marketingclaim newpack".split())
_VETO_STRIP_WORDS = frozenset(
    "logo symbol table information facts declaration label per".split())
_GENERIC_WORDS = frozenset(
    "biscuits biscuit cookies noodles atta flour oil chips namkeen "
    "chocolate tea coffee milk candy toffee mixture bhujia papad "
    "rusk cake bread jam sauce ketchup".split())


def _norm_name_key(text: str) -> str:
    """Spaceless lowercase comparison form for names."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _product_foreign_markers(stripped: str) -> bool:
    """Foreign-section markers (storage/care/date/FSSAI/nutrition) inside
    a product candidate. Inline spaceless check (no food import — fields
    must not depend on food at module level)."""
    nospace = re.sub(r"[^a-z0-9]", "", (stripped or "").lower())
    return any(m in nospace for m in (
        "onceopened", "airtight", "consumercare", "customercare",
        "feedback", "tollfree", "helpline", "bestbefore", "useby",
        "fssai", "nutrition", "storage", "storein", "keepin"))


def _product_vetoed(stripped: str) -> bool:
    """Whether a candidate line is really a nutrition/section term."""
    toks = re.findall(r"[A-Za-z0-9%/.]+", stripped.lower())
    core = [t for t in toks
            if t not in _VETO_STRIP_WORDS
            and not re.fullmatch(r"\d+(?:\.\d+)?\s*(?:g|kg|mg|ml|l|%)?", t)]
    spaceless = re.sub(r"[^a-z]", "", "".join(core))
    if any(phrase in spaceless for phrase in _PRODUCT_VETO_PHRASES):
        return True
    return spaceless in _PRODUCT_VETO


def _score_product_names(parts: list[tuple[str, float, Any, Any]],
                         skip_res: list) -> list[dict[str, Any]]:
    """Rank product-name candidates; brand/generic scored alongside.

    Score = prominence (box area vs per-image median) + front placement
    + cross-image repetition + OCR confidence + lexical shape, minus
    digit/heading penalties. Veto terms reject outright. Returns best-
    first candidates with reasons; [] when nothing qualifies (caller
    leaves the value blank for NEEDS_REVIEW).
    """
    eligible: list[dict[str, Any]] = []
    for i, (text, conf, image, box) in enumerate(parts):
        stripped = (text or "").strip()
        if len(stripped) < 3 or _NUMERIC_LINE.match(stripped):
            continue
        if any(rx.search(stripped) for rx in skip_res):
            continue
        if _product_vetoed(stripped):
            continue
        if "@" in stripped or "http" in stripped.lower():
            continue
        eligible.append({"line_index": i, "value": stripped,
                         "confidence": conf, "image": image, "box": box})
    if not eligible:
        return []
    # Prominence baseline: largest eligible box per image (display
    # lettering is what matters; median-based norms collapse on
    # few-line images).
    largest: dict[Any, float] = {}
    for cand in eligible:
        area = _box_area(cand["box"])
        if area:
            largest[cand["image"]] = max(area,
                                         largest.get(cand["image"], 0.0))
    # Repetition across DISTINCT images only: region re-OCR of the same
    # image must not inflate a candidate (same text, same photo).
    seen: dict[str, set] = {}
    for cand in eligible:
        key = _norm_name_key(cand["value"])
        seen.setdefault(key, set()).add(cand["image"])
    scored: list[dict[str, Any]] = []
    for cand in eligible:
        reasons: list[str] = []
        area = _box_area(cand["box"])
        if area and largest.get(cand["image"]):
            prom = min(1.0, area / max(largest[cand["image"]], 1e-6))
            reasons.append(f"prominence {prom:.2f}")
        else:
            prom = 0.5  # boxless lines: neutral, legacy-like behaviour
            reasons.append("no box: neutral prominence")
        score = prom * 1.0
        if cand["image"] == "front":
            score += 0.3
            reasons.append("front panel")
        repeats = len(seen.get(_norm_name_key(cand["value"]), set())) - 1
        if repeats:
            boost = min(0.4, 0.2 * repeats)
            score += boost
            reasons.append(f"repeated x{repeats + 1} images")
        score += 0.2 * max(0.0, min(1.0, cand["confidence"]))
        words = cand["value"].split()
        if cand["value"] == cand["value"].upper() and len(words) <= 6:
            score += 0.1
            reasons.append("display case")
        elif cand["value"][:1].isupper():
            score += 0.1
            reasons.append("title case")
        if any(ch.isdigit() for ch in cand["value"]):
            score -= 0.3
            reasons.append("contains digits")
        if len(words) == 1 and cand["value"] == cand["value"].upper():
            # Single-word display caps ("BRITANNIA") read as brand, not
            # product — the brand scorer below handles that role.
            score -= 0.5
            reasons.append("single-word display (brand-like)")
        if cand["value"].rstrip().endswith(":"):
            score -= 0.3
            reasons.append("heading-like colon")
        if len(words) > 8:
            score -= 0.5
            reasons.append("too long for a name")
        if _product_foreign_markers(cand["value"]):
            score -= 0.6
            reasons.append("foreign-section markers")
        # Boxless evidence keeps a low bar (legacy first-line behaviour;
        # the veto list does the real work there). Boxed lines need
        # genuine prominence support — otherwise blank + NEEDS_REVIEW.
        threshold = 1.0 if area else 0.3
        cand["score"] = round(score, 3)
        cand["reasons"] = reasons
        cand["threshold"] = threshold
        scored.append(cand)
    scored.sort(key=lambda c: c["score"], reverse=True)
    winners = [c for c in scored if c["score"] >= c["threshold"]]
    if not winners:
        return []
    best = winners[0]
    # Brand: most prominent ALL-CAPS short line (front preferred),
    # scored independently of the product pick.
    brand = None
    brand_cands = [c for c in scored
                   if c["value"] == c["value"].upper()
                   and 1 <= len(c["value"].split()) <= 4
                   and not any(ch.isdigit() for ch in c["value"])]
    if brand_cands:
        def _brand_key(c: dict[str, Any]) -> tuple:
            area = _box_area(c["box"]) or 0.0
            return (c["image"] == "front", area)

        brand = max(brand_cands, key=_brand_key)["value"]
        if brand == best["value"]:
            brand = None  # same line serves both; keep product value
    best["brand"] = brand
    # Generic: category word present anywhere, else mirror product.
    low_all = " ".join(c["value"] for c in scored).lower()
    generic = next((w for w in sorted(_GENERIC_WORDS, key=len,
                                      reverse=True)
                    if re.search(r"\b" + re.escape(w) + r"\b", low_all)),
                   None)
    best["generic"] = generic.capitalize() if generic else None
    return winners


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
                      or _PHONE_1800_OCR.search(corpus)
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
        "mrp": bool(_MRP_CTX.search(corpus)
                    or _MRP_CTX_OCR.search(corpus)),
        "batch_lot": bool(_BATCH_RE.search(corpus)),
        "manufacturing_date": bool(_MFG_CTX.search(corpus)
                                       or _MFG_CTX_OCR.search(corpus)),
        "best_before": bool(_BB_CTX.search(corpus)),
        "use_by": bool(_BB_CTX.search(corpus)),
        "fssai_license": bool(_FSSAI_CTX.search(corpus)
                                 or _FSSAI_CTX_OCR.search(corpus)),
        "consumer_care": bool(_CARE_CTX.search(corpus) or bare_phone),
        "country_of_origin": bool(_COO_RE.search(corpus)),
        "unit_sale_price": bool(_USP_RE.search(corpus)),
    }


def _fused_confidence(key: str, conf: float | None,
                      anchor_found: bool,
                      contaminated: bool) -> tuple[float | None, list[str]]:
    """Honest field confidence (Stage-1D, additive signal).

    Combines OCR confidence with field-format context: missing anchor
    context and contamination markers discount the reading. Returns
    (fused, reasons). The raw OCR ``confidence`` is never rewritten —
    fusion only ever demotes DETECTED to NEEDS_REVIEW, never promotes.
    """
    reasons: list[str] = []
    if conf is None:
        return None, ["no OCR confidence"]
    fused = float(conf)
    reasons.append(f"ocr confidence {conf}")
    if not anchor_found:
        fused *= 0.7
        reasons.append("no anchor context x0.7")
    if contaminated:
        fused *= 0.5
        reasons.append("contamination markers x0.5")
    return round(fused, 3), reasons


def _line_contaminated(text: str | None, key: str = "") -> bool:
    """Strong unrelated-section markers in a source line.

    Each field's OWN anchor words never count (an MRP line legitimately
    contains "MRP"; a care line contains "care"). Only foreign-section
    markers discount the reading.
    """
    if not text:
        return False
    low = text.lower()
    nospace = re.sub(r"[^a-z0-9]", "", low)
    markers = {"consumercare", "customercare", "feedback", "tollfree",
               "helpline", "manufacturedby", "packedby", "marketedby",
               "importedby", "bestbefore", "useby", "fssai", "airtight",
               "onceopened", "nutrition"}
    own: dict[str, set[str]] = {
        "consumer_care": {"consumercare", "customercare", "feedback",
                          "tollfree", "helpline"},
        "manufacturer": {"manufacturedby", "packedby", "marketedby",
                         "importedby"},
        "manufacturing_date": {"bestbefore", "useby"},
        "best_before": {"bestbefore", "useby"},
        "use_by": {"bestbefore", "useby"},
        "batch_lot": {"bestbefore", "useby"},
        "fssai_license": {"fssai"},
    }
    markers = markers - own.get(key, set())
    return any(m in nospace for m in markers)


def extract_with_status(lines: list[Any]) -> dict[str, dict[str, Any]]:
    """Detailed extraction plus a per-field review status.

    Status is one of DETECTED / NEEDS_REVIEW / NOT_DETECTED and reflects
    OCR evidence strength only — never a compliance verdict. A present
    value that fails structural validation (e.g. a whole line captured
    as a "date") is NEEDS_REVIEW, never DETECTED. Fused confidence
    (OCR x anchor x contamination) can only demote DETECTED to
    NEEDS_REVIEW, never promote a weak read.
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
    parts = _line_parts(lines)
    out: dict[str, dict[str, Any]] = {}
    for key in FIELD_KEYS:
        hit = detailed[key]
        value, conf = hit["value"], hit["confidence"]
        anchor_found = bool(anchors.get(key))
        source_text = None
        try:
            idx = hit.get("image_index")
            if isinstance(idx, int) and 0 <= idx < len(parts):
                if parts[idx][2] == hit.get("image"):
                    source_text = parts[idx][0]
        except (TypeError, IndexError):
            source_text = None
        contaminated = _line_contaminated(source_text, key)
        fused, fuse_reasons = _fused_confidence(
            key, conf, anchor_found, contaminated)
        fuse_reasons = list(hit.get("score_reasons", []) or []) \
            + fuse_reasons
        if value not in (None, ""):
            valid = validators[key](value) if key in validators else True
            status = ("DETECTED" if valid and (conf is None
                      or conf >= STATUS_CONF_THRESHOLD) else "NEEDS_REVIEW")
            if status == "DETECTED" and fused is not None \
                    and fused < STATUS_CONF_THRESHOLD:
                status = "NEEDS_REVIEW"
                fuse_reasons.append(
                    f"fused {fused} < {STATUS_CONF_THRESHOLD}: demoted")
        else:
            status = ("NEEDS_REVIEW" if anchor_found else "NOT_DETECTED")
        out[key] = {**hit, "status": status,
                    "anchor_found": anchor_found,
                    "fused_confidence": fused,
                    "score_reasons": fuse_reasons}
    _cross_validate(out)
    return out


def _cross_validate(out: dict[str, dict[str, Any]]) -> None:
    """Deterministic cross-field consistency (Stage-1D, in place).

    A value that belongs to another field can never stand: nutrition
    terms as product names, phone digits as MRP/FSSAI, FSSAI digits as
    MRP. Stage-2B addition: multiple distinct MRP monetary candidates
    with close scores (several prices on the pack, only one spatial
    winner) demote to NEEDS_REVIEW with the pool preserved — the
    officer, with spatial evidence, picks the declaration. Demotes to
    NEEDS_REVIEW with a reason — never deletes, never invents. Mutates
    ``out`` in place, no return.
    """
    prod = (out.get("product_name") or {}).get("value") or ""
    if prod and _product_vetoed(str(prod)):
        out["product_name"]["status"] = "NEEDS_REVIEW"
        out["product_name"].setdefault("score_reasons", []).append(
            "cross-field: value is a nutrition/section term")
    mrp_digits = re.sub(r"\D", "",
                        str((out.get("mrp") or {}).get("value") or ""))
    for other in ("fssai_license", "consumer_care"):
        other_digits = re.sub(
            r"\D", "", str((out.get(other) or {}).get("value") or ""))
        if mrp_digits and other_digits and mrp_digits == other_digits:
            out["mrp"]["status"] = "NEEDS_REVIEW"
            out["mrp"].setdefault("score_reasons", []).append(
                f"cross-field: digits equal {other}")
    fssai_digits = re.sub(
        r"\D", "", str((out.get("fssai_license") or {}).get("value") or ""))
    care_digits = re.sub(
        r"\D", "", str((out.get("consumer_care") or {}).get("value") or ""))
    if fssai_digits and care_digits and fssai_digits == care_digits:
        out["fssai_license"]["status"] = "NEEDS_REVIEW"
        out["fssai_license"].setdefault("score_reasons", []).append(
            "cross-field: digits equal consumer_care")
    # Stage 3B.8: multi-line manufacturer reconstruction is never
    # trusted silently — the officer confirms the joined company name.
    if (out.get("manufacturer") or {}).get("maker_joined"):
        out["manufacturer"]["status"] = "NEEDS_REVIEW"
        out["manufacturer"].setdefault("score_reasons", []).append(
            "cross-field: multi-line reconstruction needs review")
    # Stage-2B §7: several distinct MRP monetary candidates with close
    # scores (unit-sale price vs MRP, two panels) — no silent pick.
    try:
        pool = (out.get("mrp") or {}).get("all_candidates") or []
        seen: dict[str, float] = {}
        for cand in pool:
            val = str(cand.get("value", "")).strip()
            try:
                score = float(cand.get("score", 0) or 0)
            except (TypeError, ValueError):
                continue
            if val and (val not in seen or score > seen[val]):
                seen[val] = score
        ordered = sorted(seen.values(), reverse=True)
        if len(seen) >= 2 and (ordered[0] - ordered[1]) < 0.5:
            out["mrp"]["status"] = "NEEDS_REVIEW"
            out["mrp"].setdefault("score_reasons", []).append(
                "cross-field: multiple monetary candidates need spatial "
                "review")
    except Exception:
        pass


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
                  box: Any = None, reasons: list[str] | None = None
                  ) -> None:
        if out[key]["value"] is None:
            out[key] = {"value": value, "confidence": _mean(confs),
                        "image": image, "image_index": image_index,
                        "box": box}
            if reasons:
                out[key]["all_candidates"] = [
                    {"value": value, "score": _mean(confs),
                     "reasons": list(reasons), "image": image, "box": box}]

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
        reasons = []
        if mrp_word >= 3:
            reasons.append("MRP word, same line")
        elif mrp_word == 2:
            reasons.append("MRP word, adjacent line")
        elif mrp_word == 1:
            reasons.append("MRP word, nearby box")
        else:
            reasons.append("bare currency amount")
        if split:
            reasons.append("split across lines")
        if rs_anchor:
            reasons.append("currency anchored")
        mrp_cands.append({
            "value": _clean_num(raw_num),
            "confs": list(confs),
            "score": (mrp_word * 3.0 + (1.0 if split else 0.0)
                      + (2.0 if rs_anchor else 0.0) + conf_mean),
            "reasons": reasons,
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
        else:
            # OCR-damaged MRP word ("MRF Rs. 50"): same-line
            # currency-anchored amount only, ranked below clean hits.
            for rx in _MRP_RES_OCR:
                m = rx.search(text)
                if m:
                    _add_mrp(m.group(1), i, [conf], mrp_word=2,
                             split=False, rs_anchor=True,
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
        # Spatial fallback: a currency amount in a box NEAR the MRP
        # label (adjacent rows/columns the line window misses). Ranked
        # below same-line and window hits; same validity gates apply.
        for j in _spatial_neighbors(i, parts):
            for rx in _MRP_RES[1:]:
                m = rx.search(texts[j][0])
                if m and _clean_num(m.group(1)) not in {
                        c["value"] for c in mrp_cands}:
                    _add_mrp(m.group(1), j, [texts[j][1], conf],
                             mrp_word=1, split=True, rs_anchor=True,
                             tail=texts[j][0][m.end(1):m.end(1) + 12])
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
        # The full candidate pool is retained as evidence (never silently
        # dropped); only the scorer's pick becomes the value.
        ordered_cands = sorted(mrp_cands, key=lambda c: c["score"],
                               reverse=True)
        best = ordered_cands[0]
        mrp_pick["best"] = best
        image, idx, box = best["image"], best["idx"], best["box"]
        set_field("mrp", best["value"], best["confs"], image, idx, box)
        out["mrp"]["all_candidates"] = [
            {"value": c["value"], "score": round(c["score"], 3),
             "reasons": c.get("reasons", []), "image": c["image"],
             "box": c["box"]} for c in ordered_cands[:5]]

    # --- quantity + unit: scored candidates, never fabricated ---
    # Every "<number> <unit>" match becomes a candidate scored on:
    #   NET WT/QTY/WEIGHT anchor proximity (same line 3, adjacent 2/1,
    #     corpus-wide 0.5, none 0) + OCR confidence + box proximity bonus.
    # Implausible magnitudes (e.g. "9999999 g") are discarded. The best
    # plausible candidate wins; with no candidate the value stays None.
    qty_cands: list[dict[str, Any]] = []
    ctx_lines = [i for i, (text, _) in enumerate(texts)
                 if _QTY_CTX.search(text) or _QTY_CTX_OCR.search(text)]

    def _add_qty(raw_num: str, raw_unit: str, line_idx: int,
                 confs: list[float], anchor: float,
                 reason: str = "") -> None:
        if not _qty_plausible(raw_num, raw_unit):
            return
        # Stage-2B §7: a standalone small count ("1", "2") is a serving
        # count, pack count, or nutrition fragment — never a net quantity
        # — unless same-line/adjacent net context or strong spatial
        # association backs it (anchor >= 2.0). Corpus-level bare hits
        # (0.5) are rejected.
        if raw_num.replace(",", "").strip() in ("1", "2", "1.0", "2.0") \
                and anchor < 2.0:
            return
        # Stage 3 §7: serving/pack/preparation/nutrition fragments
        # ("Pack of 4", "4 servings", "1 packet", "8g protein") are never
        # net quantity unless the same line carries net context.
        try:
            _line_text = parts[line_idx][0] if 0 <= line_idx < len(
                parts) else ""
        except (TypeError, IndexError):
            _line_text = ""
        if _QTY_COUNT_CTX.search(_line_text or "") and anchor < 3.0:
            return
        image, idx, box = _loc(line_idx)
        try:
            conf_mean = sum(confs) / len(confs)
        except ZeroDivisionError:
            conf_mean = 0.0
        if not reason:
            reason = ("net-context same line" if anchor >= 3.0
                      else "net-context nearby" if anchor >= 1.0
                      else "bare amount")
        qty_cands.append({
            "number": _clean_num(raw_num),
            "unit": _canon_unit(raw_unit),
            "confs": list(confs),
            "score": anchor * 2.0 + conf_mean,
            "reasons": [reason],
            "image": image, "idx": idx, "box": box,
        })

    for i, (text, conf) in enumerate(texts):
        for m in _QTY_RE.finditer(text):
            anchor = 3.0 if _qty_anchored_same_line(text) else 0.0
            _add_qty(m.group(1), m.group(2), i, [conf], anchor)
        # Digit-as-unit ("100 9"): same-line net anchor mandatory.
        if _QTY_CTX.search(text) or _QTY_CTX_OCR.search(text):
            for m in _QTY_DIGIT_UNIT_RE.finditer(text):
                _add_qty(m.group(1), "g", i, [conf], 2.5,
                         "net-context digit-as-unit")
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
        # Spatial pairing: amount boxes near a NET-context line that the
        # line-index window misses (multi-column layouts). Ranked between
        # adjacent-line and corpus-level evidence.
        for i in ctx_lines:
            for j in _spatial_neighbors(i, parts):
                if abs(j - i) <= 2:
                    continue  # already covered by the index window
                for m in _QTY_RE.finditer(texts[j][0]):
                    _add_qty(m.group(1), m.group(2), j,
                             [texts[j][1], texts[i][1]], 1.5)
    if qty_cands:
        # Highest score wins; front-panel candidates win exact ties
        # (net quantity lives on the principal display), then earliest.
        best = max(qty_cands,
                   key=lambda c: (c["score"], c["image"] == "front"))
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
        ordered_qty = sorted(qty_cands, key=lambda c: c["score"],
                             reverse=True)
        out["quantity"]["all_candidates"] = [
            {"value": f"{c['number']} {c['unit']}",
             "score": round(c["score"], 3),
             "reasons": c.get("reasons", []), "image": c["image"],
             "box": c["box"]} for c in ordered_qty[:5]]

    # --- dates: MFD/MFG/PKD + BEST BEFORE / USE BY / EXPIRY ---
    # Tokens are strictly validated BEFORE acceptance: "70.16" is
    # rejected with reason invalid_date_candidate (blank +
    # NEEDS_REVIEW via the anchor), never stored, never transformed.
    for i, (text, conf) in enumerate(texts):
        if out["manufacturing_date"]["value"] is None and _MFG_CTX.search(text):
            m = _find_date(text)
            if m and _valid_date_token(m.group(1)):
                image, idx, box = _loc(i)
                set_field("manufacturing_date", m.group(1), [conf],
                          image, idx, box,
                          ["mfg-context line"])
            elif m:
                out["manufacturing_date"].setdefault(
                    "score_reasons", []).append(
                    "invalid_date_candidate: "
                    f"{m.group(1)[:24]} rejected")
            else:
                # Spatial fallback: date token in a box near the MFG
                # label (split rows/columns). Same-line hits win first.
                for j in _spatial_neighbors(i, parts):
                    mj = _find_date(texts[j][0])
                    if mj and _valid_date_token(mj.group(1)):
                        image, idx, box = _loc(j)
                        set_field("manufacturing_date", mj.group(1),
                                  [texts[j][1], conf], image, idx, box,
                                  ["mfg-context nearby box"])
                        break
    for i, (text, conf) in enumerate(texts):
        if _BB_CTX.search(text):
            m = _find_date(text)
            if m and _valid_date_token(m.group(1)):
                low = text.lower()
                image, idx, box = _loc(i)
                if "use by" in low or "use-by" in low:
                    set_field("use_by", m.group(1), [conf], image, idx, box,
                              ["use-by-context line"])
                else:
                    set_field("best_before", m.group(1), [conf],
                              image, idx, box,
                              ["best-before-context line"])
            elif m:
                out["best_before"].setdefault(
                    "score_reasons", []).append(
                    "invalid_date_candidate: "
                    f"{m.group(1)[:24]} rejected")
            else:
                dur = _BB_DURATION.search(text)
                if dur and out["best_before"]["value"] is None:
                    image, idx, box = _loc(i)
                    set_field("best_before", dur.group(0).strip(), [conf],
                              image, idx, box,
                              ["best-before duration"])

    # --- FSSAI: 14 digits ONLY with licence context (never a bare barcode) ---
    # Stage 3B.6: split OCR lines ("LIC." / "NO." / number across lines)
    # are joined when every contributing line is context or a digit
    # fragment; candidates overlapping a barcode-candidate box are
    # skipped (cross-check against barcode/QR regions).
    import types as _types

    def _local_box_rect(box: Any) -> tuple[float, float, float, float] | None:
        # Local copy of the service helper (fields must not import the
        # service layer): 4-point/dict box -> (x0, y0, x1, y1).
        try:
            if isinstance(box, dict):
                x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
                return (x, y, x + w, y + h)
            pts = [list(map(float, p)) for p in box]  # type: ignore[union-attr]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            return (min(xs), min(ys), max(xs), max(ys))
        except (TypeError, ValueError, IndexError, KeyError):
            return None

    def _local_iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
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

    _shims = [_types.SimpleNamespace(
        text=t, confidence=c,
        image=parts[_k][2] if 0 <= _k < len(parts) else "",
        box=parts[_k][3] if 0 <= _k < len(parts) else None)
        for _k, (t, c) in enumerate(texts)]
    try:
        _barcode_runs = [
            (_local_box_rect(c.get("box")),
             re.sub(r"\D", "", str(c.get("digits", ""))))
            for c in find_barcode_candidates(_shims)]
        _barcode_runs = [(r, d) for r, d in _barcode_runs
                         if r is not None and d]
    except Exception:
        _barcode_runs = []

    def _on_barcode(box: Any, digits: str = "") -> bool:
        # A barcode-region overlap vetoes the candidate — unless the
        # overlapping run IS these digits on a licence-context line
        # (OCR-damaged openers like "FSSA Lic N0." are invisible to the
        # barcode helper's clean-context exclusion, but the 14-digit
        # run itself is still the licence, not a barcode).
        rect = _local_box_rect(box)
        if rect is None:
            return False
        mine = re.sub(r"\D", "", str(digits or ""))
        for b, bdigits in _barcode_runs:
            if _local_iou(rect, b) <= 0.5:
                continue
            if mine and mine == bdigits:
                continue
            return True
        return False

    def _fragment_ok(frag: str) -> bool:
        s = frag.strip()
        if not s:
            return True
        if _FSSAI_CTX.search(s) or _FSSAI_CTX_OCR.search(s):
            return True
        nospace = re.sub(r"[^a-z0-9]", "", s.lower())
        if nospace in ("lic", "licno", "no", "n0", "fssai"):
            return True
        digits = re.sub(r"\D", "", s)
        rest = re.sub(r"[\d\s.,:/\-]", "", s).strip()
        return bool(digits) and not rest

    for i, (text, conf) in enumerate(texts):
        if _FSSAI_CTX.search(text) or _FSSAI_CTX_OCR.search(text):
            m = _FSSAI_NUM.search(text)
            if m:
                digits = re.sub(r"\D", "", m.group(1))
                if len(digits) == 14:
                    image, idx, box = _loc(i)
                    if _on_barcode(box, digits):
                        continue  # barcode run, not a licence number
                    set_field("fssai_license", digits, [conf],
                              image, idx, box,
                              ["licence-context 14-digit run"])
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
                            if _on_barcode(box, digits):
                                continue
                            set_field("fssai_license", digits,
                                      [conf, texts[i + 1][1]],
                                      image, idx, box,
                                      ["licence-context split across lines"])
                            break
                # Stage 3B.6 three-line splits ("LIC." / "NO." / number):
                # join only when every line is context or a digit
                # fragment, so unrelated numbers never glue together.
                if out["fssai_license"]["value"] is None \
                        and i + 2 < len(texts):
                    frags = [text, texts[i + 1][0], texts[i + 2][0]]
                    if all(_fragment_ok(f) for f in frags):
                        joined = " ".join(frags)
                        m3 = _FSSAI_NUM.search(joined)
                        if m3:
                            digits = re.sub(r"\D", "", m3.group(1))
                            if len(digits) == 14:
                                image, idx, box = _loc(i)
                                set_field(
                                    "fssai_license", digits,
                                    [conf, texts[i + 1][1],
                                     texts[i + 2][1]],
                                    image, idx, box,
                                    ["licence-context split across 3 lines"])
                                break
                # Spatial fallback: 14-digit run in a nearby box.
                for j in _spatial_neighbors(i, parts):
                    m3 = _FSSAI_NUM.search(texts[j][0])
                    if m3:
                        digits = re.sub(r"\D", "", m3.group(1))
                        if len(digits) == 14:
                            image, idx, box = _loc(j)
                            if _on_barcode(box, digits):
                                continue
                            set_field("fssai_license", digits,
                                      [texts[j][1], conf], image, idx,
                                      box,
                                      ["licence-context nearby box"])
                            break
                if out["fssai_license"]["value"] is not None:
                    break

    # --- consumer care / phone ---
    # Stage 3B.9: an 1800-prefix fragment of a 14-digit FSSAI run is
    # never a care number — the licence extractor owns FSSAI-context
    # lines. Accepted values also carry a canonical digit form
    # (normalized_value); the original evidence text is preserved.
    for i, (text, conf) in enumerate(texts):
        m = (_PHONE_1800.search(text) or _PHONE_1800_OCR.search(text)
             or _PHONE_91.search(text))
        if m:
            if (_FSSAI_CTX.search(text) or _FSSAI_CTX_OCR.search(text)) \
                    and len(re.sub(r"\D", "", text)) >= 14:
                continue
            image, idx, box = _loc(i)
            set_field("consumer_care", m.group(1), [conf], image, idx, box,
                      ["toll-free/STD phone pattern"])
            break
    else:
        for i, (text, conf) in enumerate(texts):
            m = _PHONE_10.search(text)
            if m and (_CARE_CTX.search(text)
                      or _CARE_CTX_OCR.search(text)):
                image, idx, box = _loc(i)
                set_field("consumer_care", m.group(1), [conf],
                          image, idx, box,
                          ["care-context 10-digit number"])
                break
        else:
            # Spatial fallback: phone/email near a care-context line.
            for i, (text, conf) in enumerate(texts):
                if not _CARE_CTX.search(text):
                    continue
                for j in _spatial_neighbors(i, parts):
                    mj = (_PHONE_1800.search(texts[j][0])
                          or _PHONE_1800_OCR.search(texts[j][0])
                          or _PHONE_91.search(texts[j][0])
                          or _PHONE_10.search(texts[j][0]))
                    if mj:
                        image, idx, box = _loc(j)
                        set_field("consumer_care", mj.group(1),
                                  [texts[j][1], conf], image, idx, box,
                                  ["care-context nearby box"])
                        break
                if out["consumer_care"]["value"] is not None:
                    break
    # Stage 3B.9 canonical phone form ("1800 103 1947" -> "18001031947",
    # "+91-..." keeps its +); the raw matched text stays the value.
    try:
        _care_val = out["consumer_care"]["value"]
        if _care_val and "@" not in _care_val \
                and "http" not in _care_val.lower():
            _care_digits = re.sub(r"\D", "", str(_care_val))
            if _care_digits:
                out["consumer_care"]["normalized_value"] = (
                    "+" if str(_care_val).strip().startswith("+") else ""
                ) + _care_digits
    except Exception:
        pass

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
    # Stage-2B §7 / 2C §8: bare label fragments ("No" from "Batch No",
    # NET/NOT/NEW/PACK/LOT, "NA", dashes) are never batch values.
    # Stage 3B.7: opener-only lines ("BATCH" with no code) pair with a
    # code-shaped token on the adjacent line ("BATCH" / "A12345") when
    # spatially adjacent; declaration openers (NET/MRP/MFG/...) stop.
    _BATCH_STOP = frozenset(
        {"no", "na", "n/a", "nil", "none", "-", "--", "null", "nr",
         "net", "not", "new", "pack", "lot", "number", "numbers",
         "batch", "mrp", "mfg", "mfd", "exp"})
    _BATCH_STOP_RES = (
        _MRP_CTX, _MFG_CTX, _BB_CTX, _QTY_CTX, _FSSAI_CTX, _CARE_CTX)

    def _batch_code_ok(code: str) -> bool:
        return bool(code) and code.strip(" .").lower() not in _BATCH_STOP \
            and len(code) >= 2

    for i, (text, conf) in enumerate(texts):
        m = _BATCH_RE.search(text)
        if m:
            code = m.group(1).strip(":- ")
            if _batch_code_ok(code):
                image, idx, box = _loc(i)
                set_field("batch_lot", code, [conf],
                          image, idx, box,
                          ["batch/lot pattern"])
                break
            # A stop-word "code" ("No" in "Lot No.") means the keyword
            # fired without a real value: fall through to opener-only
            # pairing below instead of abandoning the line.
        # Opener-only line? Try the adjacent line for a code token.
        if (m is not None) or _BATCH_RE.match(text.strip()) or re.fullmatch(
                r"(?i)\s*(?:batch|lot)(?:\s*no\.?)?\s*[:.]?\s*", text):
            for j in (i + 1, i - 1):
                if not (0 <= j < len(texts)):
                    continue
                nxt = texts[j][0].strip()
                if not nxt or any(rx.search(nxt)
                                  for rx in _BATCH_STOP_RES):
                    continue
                cm = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9/\-]{1,19})",
                                  nxt.strip(":- "))
                if cm and _batch_code_ok(cm.group(1)):
                    image, idx, box = _loc(j)
                    set_field("batch_lot", cm.group(1), [conf, texts[j][1]],
                              image, idx, box,
                              ["batch/lot split across lines"])
                    break
            if out["batch_lot"]["value"] is not None:
                break

    # --- manufacturer: only lines with maker keywords ---
    # Phone-number runs merged into the line by OCR are stripped: the
    # care number is extracted as its own field, never as part of a name.
    # Values are normalized for readability (opener strip incl. glued
    # OCR forms like "Pkg.Mtrl.Mfd.By:", dotted-abbreviation spacing);
    # company names themselves are never rewritten.
    # Stage 3B.8: a maker line may continue on the next line ("NESTLE" /
    # "INDIA LIMITED"). The continuation is joined only when it looks
    # like a name fragment (no declaration anchors, no section openers,
    # no heavy digits); the joined value is flagged maker_joined so the
    # status pass routes it to NEEDS_REVIEW instead of trusting it.
    _MAKER_PHONE = re.compile(r"\+?\d[\d\s\-]{7,}\d")
    _MAKER_EMAIL_URL = re.compile(
        r"[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}|(?:https?://|www\.)[\w\-./?=%&]+",
        re.I)
    _MAKER_CONT = re.compile(r"^[A-Z][A-Za-z&.,'()\- ]{1,39}$")
    _MAKER_STOP_RES = (_MRP_CTX, _MFG_CTX, _BB_CTX, _QTY_CTX, _FSSAI_CTX,
                       _CARE_CTX, _BATCH_RE, _COO_RE, _USP_RE)
    for i, (text, conf) in enumerate(texts):
        if _MAKER_CTX.search(text):
            cleaned = normalize_maker_value(text)
            if cleaned:
                cleaned = _MAKER_PHONE.sub("", cleaned)
                cleaned = _MAKER_EMAIL_URL.sub("", cleaned).strip(" ,:-")
            confs = [conf]
            joined = False
            if cleaned and i + 1 < len(texts):
                nxt, nconf = texts[i + 1][0].strip(), texts[i + 1][1]
                if nxt and _MAKER_CONT.match(nxt) and not any(
                        rx.search(nxt) for rx in _MAKER_STOP_RES):
                    merged = f"{cleaned} {nxt}".strip()
                    if len(merged) <= 80:
                        cleaned = normalize_maker_value(merged) or merged
                        confs.append(nconf)
                        joined = True
            if cleaned:
                image, idx, box = _loc(i)
                set_field("manufacturer", cleaned, confs,
                          image, idx, box,
                          ["maker-context line"]
                          + (["multi-line reconstruction"]
                             if joined else []))
                if joined:
                    out["manufacturer"]["maker_joined"] = True
                break

    # --- country of origin ---
    for i, (text, conf) in enumerate(texts):
        m = _COO_RE.search(text)
        if m:
            image, idx, box = _loc(i)
            set_field("country_of_origin", m.group(1).strip(), [conf],
                      image, idx, box,
                      ["origin phrase"])
            break

    # --- unit sale price: presence line ---
    for i, (text, conf) in enumerate(texts):
        if _USP_RE.search(text):
            image, idx, box = _loc(i)
            set_field("unit_sale_price", text.strip(), [conf],
                      image, idx, box,
                      ["unit-price phrase"])
            break

    # --- product identity: dedicated candidate scorer (Stage-1D) ---
    # A nutrition-table term ("TRANS FAT") must NEVER become the product
    # name. Every eligible line is scored on visual prominence (box
    # size), front placement, cross-image repetition, OCR confidence
    # and lexical shape; veto terms reject outright. Brand / product /
    # generic are scored independently (one OCR line is never forced to
    # serve all three). Below threshold the value stays blank for
    # NEEDS_REVIEW — a blank field beats a wrong value.
    _NAME_SKIP_EXTRA = re.compile(
        r"nutrition|ingredient|composition|contents\s*:|storage|store\s+in|"
        r"keep\s+in|allergen|contains\s*:|feedback|contact\s+us|"
        r"consumer\s+care|customer\s+care|direction|recipe|how\s+to|warning|"
        r"bar\s*-?\s*code|manufactured\s+(?:by|for)|packed\s+(?:by|for)|"
        r"marketed\s+(?:by|for)|imported\s+by|for\s+feedback|"
        r"retail\s+price|maximum\s+retail\s+price", re.I)
    skip_res = (_MRP_RES + [_QTY_RE, _DATE_MY, _DATE_FULL, _DATE_MY_TEXT,
                           _DATE_FULL_TEXT, _DATE_MY_SHORT, _FSSAI_NUM,
                           _PHONE_1800, _PHONE_1800_OCR, _PHONE_91,
                           _BATCH_RE, _COO_RE,
                           _MAKER_CTX, _BB_CTX, _CARE_CTX, _USP_RE,
                           _NAME_SKIP_EXTRA])
    name_cands = _score_product_names(parts, skip_res)
    if name_cands:
        best = name_cands[0]
        image, idx, box = _loc(best["line_index"])
        set_field("product_name", best["value"], [best["confidence"]],
                  image, idx, box)
        out["product_name"]["all_candidates"] = [
            {"value": c["value"], "score": c["score"],
             "reasons": c["reasons"], "image": parts[c["line_index"]][2],
             "box": parts[c["line_index"]][3]}
            for c in name_cands[:5]]
        out["product_name"]["brand"] = best.get("brand")
        out["product_name"]["score_reasons"] = best.get("reasons", [])
        generic = best.get("generic") or best["value"]
        out["common_generic_name"] = {"value": generic,
                                      "confidence": _mean(
                                          [best["confidence"]]),
                                      "image": image, "image_index": idx,
                                      "box": box}

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
         or _DATE_MY.search(text) or _DATE_MY_TEXT.search(text)
         or _DATE_MY_SHORT.search(text))
    if not m:
        return False
    token = m.group(1)
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
                "%m/%Y", "%m-%Y", "%m.%Y", "%m/%y", "%m-%y", "%m.%y",
                "%Y-%m",
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
