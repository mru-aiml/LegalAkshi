"""Structured food-label extraction layer (FOOD products).

OCR text -> structured food label. This layer EXTRACTS; it never decides
compliance. Every field carries value/confidence/provenance/source image
and a detection state:

  DETECTED      — value extracted with usable confidence
  NEEDS_REVIEW  — label context present (e.g. "Ingredients:") but the value
                  could not be parsed confidently; an officer must verify.
                  OCR failure is NOT a legal violation.
  NOT_DETECTED  — no label context found at all (distinct from
                  CONFIRMED_MISSING, which only the rule engine + inspector
                  can conclude).

Reusable across biscuits/noodles/chips/beverages/spices/dairy — no
product-specific hard-coding.
"""
from __future__ import annotations

import re
import time
from typing import Any

LOW_CONF = 0.6
# Coherence gates: below 0.35 the text is garbage (NOT_DETECTED, raw kept
# for audit); below 0.70 it is partial (NEEDS_REVIEW); at/above it reads
# as a real declaration (DETECTED, subject to the confidence rule).
COHERENCE_GARBAGE = 0.35
COHERENCE_COHERENT = 0.70

_ING_HEAD_COLON = re.compile(
    r"ingredients?\s*[:.\-]|composition\s*[:.\-]|contents\s*:|contains\s*:|"
    r"ingredients?\s*/|/+\s*contents?", re.I)
_ING_HEAD_BARE = re.compile(
    r"^\s*(?:ingredients?|composition)\s*[.:\-]?\s*$", re.I)
_ING_HEAD_LINE = re.compile(
    r"ingredients?|composition|contents|contains", re.I)
_ING_KEYWORD = re.compile(r"ingredients?", re.I)
# Hindi/Devanagari equivalent printed on Indian packs ("sāmagri").
_ING_HEAD_HINDI = re.compile(r"सामग्री|samagr[iy]", re.I)
# Canonical heading spellings for tolerant (OCR-error-proof) matching.
_ING_CANONICALS = ("ingredients", "ingredient", "composition", "contents")


def _edit_distance(a: str, b: str) -> int:
    """Tiny Levenshtein (no dependency) for OCR-variant heading matching."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _heading_core(text: str) -> str:
    """First word-ish token of a line, normalised for fuzzy comparison."""
    s = text.strip().lower()
    s = re.sub(r"^[^a-z0-9\u0900-\u097f]+", "", s)
    m = re.match(r"[a-z0-9\u0900-\u097f]+", s)
    return m.group(0) if m else ""
# "Net contents 70 g" is a quantity line, never an ingredient heading.
_QTY_LIKE = re.compile(
    r"\b\d[\d.,]*\s*(?:kg|kgs|gms?|gm|g|mg|ml|ltr?s?|litres?|pcs?|nos?)\b"
    r"|\bnet\b.*\b(?:wt|qty|quantity|weight|contents?)\b", re.I)
# Domain fragments OCR hallucinates into ingredient text
# ("transformpilates.in", "shop-online.com", ...). Removed only from the
# cleaned view; raw_text always preserves the audit trail.
_DOMAIN_FRAG = re.compile(
    r"\b[\w-]+(?:\.[\w-]+)*\.(?:in|com|net|org|co|info|biz|online|shop|store)"
    r"\b", re.I)
_BARCODE_LINE = re.compile(r"^[\d\s\-/\\|lI]{8,}$")
_DUP_SPACE = re.compile(r"\s+")
_NUTRI_HEAD = re.compile(r"nutrition(?:al)?(?:\s+information|\s+facts)?", re.I)
_ALLERGEN_HEAD = re.compile(r"allerge?n", re.I)
_STORAGE_HEAD = re.compile(r"stor(?:age|e)|keep\s+in|store\s+in", re.I)
_COOK_HEAD = re.compile(
    r"direction|preparation|how\s+to\s+(?:cook|prepare|use)|recipe|cooking",
    re.I)
_WARN_HEAD = re.compile(r"warn|c caution|note:|statutory| disclaimer", re.I)
_FSSAI_HEAD = re.compile(r"fssai|fsslai", re.I)
_CARE_EMAIL = re.compile(r"[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)[\w\-./?=%&]+", re.I)
_MRP_LINE = re.compile(r"m\s*\.?\s*r\s*\.?\s*p|maximum\s+retail\s+price", re.I)
_MFG_HEAD = re.compile(
    r"\bmfd\b|\bmfg\b|manufactur|packed(?:\s*on)?|\bpkd\b", re.I)
_BB_HEAD = re.compile(r"best\s*before|use\s*by|expir", re.I)
_BATCH_HEAD = re.compile(r"batch|lot\s*no|b\.?\s*no", re.I)
_MAKER_HEAD = re.compile(
    r"manufactured(?:\s*&\s*marketed)?(?:\s*/\s*packed)?\s+by|marketed by|"
    r"packed by|mfd\.?\s+by|mkd\.?\s+by|mfg\.?\s+by|imported by", re.I)
_ADDRESS_LIKE = re.compile(
    r"\b\d{6}\b|road|street|nagar|gali|plot|phase|estate|area|district|"
    r"state|india|tel\b|phone|limited|ltd|pvt|works|factory|plant", re.I)
_CARE_HEAD = re.compile(
    r"consumer\s+care|customer\s+care|helpline|toll[\s-]*free|contact\s+us|"
    r"call\s+us", re.I)
_PHONE_LIKE = re.compile(r"1800[\s-]?\d|\+?91[\s-]?\d{10}|\b[6-9]\d{9}\b")
_PACKER_HEAD = re.compile(r"packed\s+by|packer", re.I)
_IMPORTER_HEAD = re.compile(r"import(?:ed)?\s+by|importer", re.I)
_BARCODE_HEAD = re.compile(r"bar\s*-?\s*code", re.I)
_DIRECTIONS_HEAD = re.compile(r"directions?\s+(?:for\s+use|to\s+cook)", re.I)
# A standalone "Contains ..." line opens the allergen declaration, never a
# continuation of the ingredient list. Anchored at line start (with an
# optional colon) so mid-list "contains X" prose is not misread.
_CONTAINS_HEAD = re.compile(
    r"^\s*contains\b\s*(?::|$)|^\s*contains\s+(?:milk|soy|soya|wheat|nut|"
    r"egg|mustard|sesame|sulphite|sulfite|gluten|peanut|tree\s+nut)", re.I)
# Consumer-feedback block openers ("FOR FEEDBACK", "for queries/...").
_FEEDBACK_HEAD = re.compile(
    r"for\s+feedback|for\s+(?:any\s+)?quer(?:y|ies)|feedback\b|"
    r"\bcomplaints?\b.*:|\bwrite\s+to\s+us\b", re.I)
# --- Stage-1B explicit ingredient boundary patterns (spec §3.8) ---
# Word-boundary regexes for spaced print; spaceless matching below catches
# OCR-glued runs ("CONSUMERCARE", "ONCEOPENED", "Forfeeback-Contacd").
_STORAGE_EXTRA = re.compile(
    r"\bstore\b|\bstorage\b|store\s+in|keep\b|once\s+opened|airtight|"
    r"clean\s+(?:and\s+)?(?:dry|airtight)\s+(?:place|container)|"
    r"\bcontainer\b.*\bonce\b|\btransfer\b.*\bcontainer\b", re.I)
_CARE_EXTRA = re.compile(
    r"consumer\s*care|customer\s*care|executive.*care\s*cell|"
    r"\bfeedback\b|\bcontact\b|toll\s*free|helpline|call\s+us|"
    r"contact\s+us|write\s+to", re.I)
_MAKER_EXTRA = re.compile(
    r"manufactured\s+by|packed\s+by|marketed\s+by|imported\s+by|"
    r"\bmfd\b.*\bby\b|\bpkd\b.*\bby\b", re.I)
_DATE_EXTRA = re.compile(
    r"\bmfd\b|\bmfg\b|\bpkd\b|\bpkg\b|manufactured(?:\s+on)?|packed(?:\s+on)?|"
    r"best\s+before|use\s+by|expir|batch|lot\s*no|\bb\.?\s*no\b", re.I)
_QTY_MRP_EXTRA = re.compile(
    r"net\s*(?:wt|qty|quantity|weight|contents?)|maximum\s+retail\s+price|"
    r"\bmrp\b|\bm\.?\s*r\.?\s*p\b", re.I)
_FSSAI_EXTRA = re.compile(r"fssai|lic\.?\s*no|licen[sc]e", re.I)
_NUTRI_BARCODE_EXTRA = re.compile(
    r"nutrition|per\s+100\s*g|bar\s*-?\s*code|\bwebsite\b|\.in\b|\.com\b|@",
    re.I)
_SECTION_HEADS = (_NUTRI_HEAD, _ALLERGEN_HEAD, _STORAGE_HEAD, _COOK_HEAD,
                  _DIRECTIONS_HEAD, _WARN_HEAD, _MFG_HEAD, _BB_HEAD,
                  _BATCH_HEAD, _MRP_LINE, _FSSAI_HEAD, _CARE_EMAIL,
                  _CARE_HEAD, _PHONE_LIKE, _MAKER_HEAD, _PACKER_HEAD,
                  _IMPORTER_HEAD, _ING_HEAD_COLON, _BARCODE_HEAD,
                  _CONTAINS_HEAD, _FEEDBACK_HEAD)


def _is_section_head(text: str) -> bool:
    """A line that opens a different labelled section (address ends here)."""
    if _ING_HEAD_COLON.search(text) or _ING_HEAD_BARE.match(text):
        return True
    if any(rx.search(text) for rx in _SECTION_HEADS):
        return True
    return ingredient_boundary_decision(text)[0]


def _spaceless(text: str) -> str:
    """Lowercase alphanumeric-only form for glued-OCR matching."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Spaceless markers for glued OCR runs. Short fragments (<4 chars) are
# excluded to avoid substring false positives inside ingredient words.
_SPACELESS_BOUNDARIES: tuple[tuple[str, str], ...] = (
    ("STORAGE", "onceopened"),
    ("STORAGE", "airtight"),
    ("STORAGE", "storein"),
    ("CONSUMER_CARE", "consumercare"),
    ("CONSUMER_CARE", "customercare"),
    ("CONSUMER_CARE", "executive"),
    ("CONSUMER_CARE", "tollfree"),
    ("CONSUMER_CARE", "helpline"),
    ("FEEDBACK", "feedback"),
    ("FEEDBACK", "feeback"),  # observed OCR variant "Forfeeback"
    ("FEEDBACK", "forfeed"),
    ("FEEDBACK", "contact"),
    ("FEEDBACK", "contac"),
    ("MANUFACTURER", "manufacturedby"),
    ("MANUFACTURER", "marketedby"),
    ("PACKER", "packedby"),
    ("IMPORTER", "importedby"),
    ("BEST_BEFORE", "bestbefore"),
    ("BEST_BEFORE", "useby"),
    ("MFG_DATE", "manufactured"),
    ("FSSAI", "fssai"),
    ("MRP", "maximumretailprice"),
    ("NUTRITION", "nutrition"),
    ("NUTRITION", "per100g"),
    ("BARCODE", "barcode"),
    ("QUANTITY", "netquantity"),
    ("QUANTITY", "netwt"),
)

# Allergen words that, together with a "contain"-like fragment, mark the
# standalone allergen declaration ("COHTAINSWHEATMILKSOYANDSULPHITE").
_ALLERGEN_WORDS = frozenset(
    "wheat milk soy soya nut nuts egg mustard sesame sulphite sulfite "
    "gluten peanut".split())


def ingredient_boundary_decision(text: str) -> tuple[bool, str | None, str]:
    """Position-and-context-aware ingredient section-boundary decision.

    Returns (is_boundary, section, reason). Never blindly stops on a word
    that legitimately occurs inside an ingredient declaration: short
    generic words (store/keep/mrp) require line-start/colon/header context
    or a strong co-marker, while distinctive markers (ONCE OPENED,
    AIRTIGHT, CONSUMER CARE, FEEDBACK, FSSAI+digits, phone/email/URL)
    terminate on their own. OCR-glued runs are matched spacelessly.
    """
    s = (text or "").strip()
    if not s:
        return False, None, "empty line"
    low = s.lower()
    nospace = _spaceless(s)
    has_comma = "," in s or ";" in s
    has_paren = "(" in s or ")" in s
    has_pct = "%" in s
    looks_ingredient = has_comma or has_paren or has_pct

    def _at_start(words: list[str]) -> bool:
        head = low[:42]
        return any(w in head for w in words)

    # 1. Distinctive storage closers: never ingredient content.
    if "once opened" in low or "onceopened" in nospace:
        return True, "STORAGE", "storage closer 'once opened'"
    if "airtight" in low:
        return True, "STORAGE", "storage marker 'airtight'"
    if re.search(r"\bstore\b|\bstorage\b", low) and (
            _at_start(["store", "storage", "keep"]) or ":" in s
            or "container" in low or "transfer" in low or not looks_ingredient):
        return True, "STORAGE", "storage instruction opener"
    if re.search(r"\bkeep\b", low) and (
            _at_start(["keep", "store"]) or "dry" in low or "away" in low
            or "cool" in low or not looks_ingredient):
        return True, "STORAGE", "storage instruction 'keep'"
    # 2. Care / feedback / contact block (incl. OCR variants).
    if ("consumer care" in low or "customer care" in low
            or "consumercare" in nospace or "customercare" in nospace
            or "executive" in low and "care" in low
            or "toll free" in low or "tollfree" in nospace
            or "helpline" in low):
        return True, "CONSUMER_CARE", "consumer-care marker"
    if ("feedback" in low or "feeback" in nospace or "forfeed" in nospace
            or ("contact" in low or "contac" in nospace)
            and ("care" in low or "cell" in low or "us" in low
                 or _PHONE_LIKE.search(s) is not None
                 or _CARE_EMAIL.search(s) is not None)):
        return True, "FEEDBACK", "feedback/contact marker"
    if _CARE_EMAIL.search(s) or _URL_RE.search(s):
        return True, "CONSUMER_CARE", "email/website contact line"
    if _PHONE_LIKE.search(s) and (
            "care" in low or "contact" in low or "feedback" in low
            or "toll" in low or "helpline" in low or "call" in low):
        return True, "CONSUMER_CARE", "care phone line"
    # 3. Maker / packer / importer openers.
    if _MAKER_EXTRA.search(s) or _MAKER_HEAD.search(s):
        return True, "MANUFACTURER", "maker/packer opener"
    # 4. Dates / batch / lot.
    if _BB_HEAD.search(s):
        return True, "BEST_BEFORE", "best-before/use-by opener"
    if _BATCH_HEAD.search(s):
        return True, "BATCH", "batch/lot opener"
    if _MFG_HEAD.search(s) and (
            _find_mfg_date_token(s) is not None or _at_start(
                ["mfd", "mfg", "pkd", "pkg", "manufactured", "packed"])
            or ":" in s or not looks_ingredient):
        return True, "MFG_DATE", "manufacturing/packing date line"
    # 5. FSSAI / licence.
    if "fssai" in low or "fsslai" in nospace:
        return True, "FSSAI", "FSSAI marker"
    # 6. MRP / price (needs anchor context; bare numbers never stop).
    if _MRP_LINE.search(s) and (
            re.search(r"rs\.?|inr|\u20b9|\d", s, re.I) is not None
            and (_at_start(["mrp", "m.r", "maximum"]) or ":" in s
                 or re.search(r"rs\.?|inr|\u20b9", s, re.I) is not None)):
        # Guard: a genuine ingredient line mentioning "mrp" is unheard of;
        # the anchor requirement above is the context check.
        return True, "MRP", "MRP declaration line"
    # 7. Net quantity declaration.
    if re.search(r"net\s*(?:wt|qty|quantity|weight|contents?)", low) and (
            re.search(r"\d", s) is not None or ":" in s
            or _at_start(["net"])):
        return True, "QUANTITY", "net-quantity declaration"
    # 8. Nutrition / barcode.
    if _NUTRI_HEAD.search(s):
        return True, "NUTRITION", "nutrition heading"
    if _BARCODE_HEAD.search(s):
        return True, "BARCODE", "barcode label"
    # 9. Spaceless glued markers (OCR merged print).
    for section, marker in _SPACELESS_BOUNDARIES:
        if marker in nospace and len(marker) >= 5:
            # Short generic fragments already handled with context above;
            # spaceless hits are distinctive enough to terminate, except
            # when the whole line is clearly an ingredient clause.
            if section in ("STORAGE", "CONSUMER_CARE", "FEEDBACK",
                           "MANUFACTURER", "PACKER", "IMPORTER",
                           "BEST_BEFORE", "MFG_DATE", "FSSAI", "NUTRITION",
                           "BARCODE", "QUANTITY", "MRP"):
                if looks_ingredient and section in ("MFG_DATE", "MRP"):
                    continue  # needs anchor context (handled above)
                return True, section, f"glued-OCR marker '{marker}'"
    # 10. Allergen declaration, incl. OCR-damaged openers.
    if _CONTAINS_HEAD.search(s) or _ALLERGEN_HEAD.search(s):
        return True, "ALLERGEN", "allergen declaration opener"
    if ("tain" in nospace and any(w in nospace for w in _ALLERGEN_WORDS)
            and not looks_ingredient):
        return True, "ALLERGEN", "damaged allergen declaration line"
    if ("contain" in nospace or "contan" in nospace) and _at_start(
            ["contain", "contan", "cohtain", "contai"]):
        return True, "ALLERGEN", "allergen 'contains' opener"
    return False, None, "no boundary marker with supporting context"


def _find_mfg_date_token(text: str) -> Any:
    from app.services.ocr.fields import _find_date as _fd

    try:
        return _fd(text or "")
    except Exception:
        return None


# Canonical section names for normalized boundary detection. Order matters:
# specific role openers (PACKER/IMPORTER/FEEDBACK/CONTAINS) precede the
# generic ones (MANUFACTURER/CONSUMER_CARE) so "Packed by X" does not
# read as a manufacturer block and "FOR FEEDBACK call us" does not read
# as consumer care.
_SECTION_CANONICALS: tuple[tuple[str, Any], ...] = (
    ("NUTRITION", _NUTRI_HEAD),
    ("ALLERGEN", _ALLERGEN_HEAD),
    ("CONTAINS", _CONTAINS_HEAD),
    ("INGREDIENTS", _ING_HEAD_COLON),
    ("STORAGE", _STORAGE_HEAD),
    ("DIRECTIONS", _DIRECTIONS_HEAD),
    ("COOKING", _COOK_HEAD),
    ("WARNING", _WARN_HEAD),
    ("PACKER", _PACKER_HEAD),
    ("IMPORTER", _IMPORTER_HEAD),
    ("MANUFACTURER", _MAKER_HEAD),
    ("BEST_BEFORE", _BB_HEAD),
    ("MFG_DATE", _MFG_HEAD),
    ("BATCH", _BATCH_HEAD),
    ("MRP", _MRP_LINE),
    ("FSSAI", _FSSAI_HEAD),
    ("FEEDBACK", _FEEDBACK_HEAD),
    ("CONSUMER_CARE", _CARE_HEAD),
    ("BARCODE", _BARCODE_HEAD),
)


def match_section_boundary(text: str) -> str | None:
    """Canonical section name a line opens, or None.

    Normalized (tolerant regexes, canonical labels) rather than exact
    string matching, so OCR variants ("NUTRIT1ON", "Mfd.") still stop the
    ingredient block instead of contaminating it. A bare ingredient
    heading wins outright; a shared heading line ("Contains: ...") is
    classified by the specific sections first so allergen openers are
    never mistaken for ingredient headings.
    """
    s = (text or "").strip()
    if not s:
        return None
    if _ING_HEAD_BARE.match(s):
        return "INGREDIENTS"
    for name, rx in _SECTION_CANONICALS:
        if name == "INGREDIENTS":
            continue  # resolved below via the tolerant heading matcher
        if rx.search(s):
            return name
    if _is_ingredient_heading(s):
        return "INGREDIENTS"
    return None
_VEG_TEXT = re.compile(r"veg(?:etarian)?(?:\s+logo)?|non[\s-]*veg", re.I)
_VARIANT_RE = re.compile(
    r"masala|atta\s*noodles|chicken|tomato|peri\s*peri|yummy|classic|"
    r"chocolate|vanilla|plain|salted|unsalted|mixed\s+masala", re.I)

_NUTRIENTS = (
    ("energy", re.compile(r"energy[^0-9]{0,15}([\d.,]+\s*(?:kcal|kj|cal))", re.I)),
    ("protein", re.compile(r"protein[^0-9]{0,15}([\d.,]+\s*g)", re.I)),
    ("carbohydrate", re.compile(r"carbohydrate[^0-9]{0,15}([\d.,]+\s*g)", re.I)),
    ("total_sugars", re.compile(r"total\s*sugars?[^0-9]{0,15}([\d.,]+\s*g)", re.I)),
    ("added_sugars", re.compile(r"added\s*sugars?[^0-9]{0,15}([\d.,]+\s*g)", re.I)),
    ("total_fat", re.compile(r"total\s*fat[^0-9]{0,15}([\d.,]+\s*g)", re.I)),
    ("saturated_fat", re.compile(r"saturated\s*fat[^0-9]{0,15}([\d.,]+\s*g)", re.I)),
    ("trans_fat", re.compile(r"trans\s*fat[^0-9]{0,15}([\d.,]+\s*g)", re.I)),
    ("sodium", re.compile(r"sodium[^0-9]{0,15}([\d.,]+\s*(?:mg|g))", re.I)),
    ("salt", re.compile(r"(?<!added\s)salt[^0-9]{0,15}([\d.,]+\s*(?:mg|g))", re.I)),
)


def _parts(lines: list[Any]) -> list[tuple[str, float, Any, Any]]:
    out = []
    for i, ln in enumerate(lines):
        text = getattr(ln, "text", "") or ""
        try:
            conf = float(getattr(ln, "confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        image = getattr(ln, "image", "") or i
        out.append((text, conf, image, i))
    return out


def _mean(confs: list[float]) -> float | None:
    vals = [c for c in confs if c]
    return round(sum(vals) / len(vals), 3) if vals else None


def _field(value: Any, confs: list[float], ctx_found: bool,
           image: Any = None, image_index: Any = None) -> dict[str, Any]:
    conf = _mean(confs)
    if value:
        detection = ("DETECTED" if conf is None or conf >= LOW_CONF
                     else "NEEDS_REVIEW")
    else:
        detection = "NEEDS_REVIEW" if ctx_found else "NOT_DETECTED"
    return {"value": value, "confidence": conf, "provenance": "OCR",
            "image": image, "image_index": image_index,
            "detection": detection}


def _window_text(parts: list[tuple], start: int, span: int = 6) -> str:
    return " ".join(t for t, _, _, _ in parts[start:start + span])


_NUTRI_VALUE_ONLY = re.compile(
    r"^\s*[\d.,]+\s*(?:kcal|kj|cal|g|mg|mcg|%)\s*$", re.I)


def _reconstruct_nutrition_block(
        parts: list[tuple]) -> tuple[str, list[float]]:
    """Rebuild the nutrition table as label/value rows.

    Collects lines below the NUTRITION heading until the next labelled
    section; splices value-only rows ("450 kcal") onto the preceding label
    row ("Energy"); never invents values — unmatched labels simply yield
    no value. Returns (block_text, confidences).
    """
    nidx = next((i for i, (t, _, _, _) in enumerate(parts)
                 if _NUTRI_HEAD.search(t)), None)
    if nidx is None:
        return "", []
    rows: list[str] = [parts[nidx][0]]
    confs: list[float] = [parts[nidx][1]]
    for t, c, _, _ in parts[nidx + 1:nidx + 17]:
        s = (t or "").strip()
        if not s:
            continue
        if _is_section_head(s) and not _NUTRI_HEAD.search(s):
            break
        if _NUTRI_VALUE_ONLY.match(s) and rows:
            rows[-1] = (rows[-1] + " " + s).strip()
            confs.append(c)
            continue
        rows.append(s)
        confs.append(c)
        if len(" ".join(rows)) > 1500:
            break
    return " ".join(r for r in rows if r).strip(), confs


def find_dense_text_band(lines: list[Any], frame_size: tuple[int, int],
                         top_frac: float = 0.30) -> dict[str, Any] | None:
    """No-heading fallback: densest small-text cluster in the lower panel.

    Scores box-bearing lines below ``top_frac`` of the frame by text
    density (chars per unit area); lines that open a labelled declaration
    section (FSSAI/MRP/dates/maker/care/...) are excluded so the band can
    never lock onto a declaration block. Returns the bounding band of the
    best cluster, or None when no usable boxes exist (e.g. mocked lines).
    Never proposes the full frame — capped at ~50% of frame height.
    """
    try:
        w, h = float(frame_size[0]), float(frame_size[1])
    except (TypeError, ValueError, IndexError):
        return None
    scored = []
    for ln in lines or []:
        text = (getattr(ln, "text", "") or "").strip()
        box = getattr(ln, "box", None)
        if len(text) < 12 or not box:
            continue
        if _is_section_head(text) or _is_ingredient_heading(text):
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except (TypeError, IndexError, ValueError):
            continue
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if y0 < h * top_frac or (y1 - y0) <= 0:
            continue
        area = max(x1 - x0, 1.0) * (y1 - y0)
        density = len(text) / area
        try:
            conf = float(getattr(ln, "confidence", 0) or 0)
        except (TypeError, ValueError):
            conf = 0.0
        scored.append((density * (0.5 + conf), (x0, y0, x1, y1)))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    # Merge the top clusters into one band (full width, capped height).
    tops = [b[1] for _, b in scored[:4]]
    bots = [b[3] for _, b in scored[:4]]
    top, bottom = min(tops), max(bots)
    pad = (bottom - top) * 0.4 + 6
    top = max(0.0, top - pad)
    bottom = min(h, bottom + pad)
    if bottom - top > h * 0.5:
        bottom = top + h * 0.5
    if bottom - top < 8:
        return None
    return {"kind": "ingredients-scan",
            "rect": (0.0, top, w, bottom),
            "reason": "no ingredient heading; densest small-text band"}


# --- Stage-1B ingredient contamination guard (spec §5) ---
# Strong evidence of unrelated sections. A line is rejected only on these
# distinctive markers — single food words (milk, wheat, soy) NEVER reject,
# since they legitimately occur inside ingredient declarations.
_CONTAMINATION_MARKERS: tuple[tuple[str, str], ...] = (
    ("storage 'once opened / airtight container'", "onceopened"),
    ("storage 'once opened / airtight container'", "airtight"),
    ("storage instruction", "storage"),
    ("storage instruction", "storein"),
    ("consumer-care block", "consumercare"),
    ("consumer-care block", "customercare"),
    ("consumer-care block", "executive"),
    ("consumer-care block", "tollfree"),
    ("consumer-care block", "helpline"),
    ("feedback/contact block", "feedback"),
    ("feedback/contact block", "feeback"),
    ("feedback/contact block", "contact"),
    ("feedback/contact block", "contac"),
    ("maker/packer block", "manufacturedby"),
    ("maker/packer block", "packedby"),
    ("maker/packer block", "marketedby"),
    ("maker/packer block", "importedby"),
    ("date/batch block", "bestbefore"),
    ("date/batch block", "useby"),
    ("FSSAI block", "fssai"),
    ("MRP block", "maximumretailprice"),
    ("nutrition block", "nutrition"),
    ("barcode block", "barcode"),
    ("net-quantity block", "netquantity"),
)


def contamination_verdict(text: str) -> tuple[bool, str]:
    """Whether an ingredient-candidate line is contamination.

    Returns (reject, reason). Spaceless matching catches glued OCR
    ("Forfeeback-Contacd", "CONSUMERCARE"). Position/context aware:
    a marker only rejects when it carries section context (line-start,
    colon, contact tokens, digits); ingredient punctuation (commas,
    parentheses, percentages) alone never *prevents* rejection of a
    genuine storage/care line, but a bare short word inside a long
    comma-separated declaration does not reject either.
    """
    s = (text or "").strip()
    if not s:
        return False, "empty line"
    low = s.lower()
    nospace = _spaceless(s)
    # Distinctive closers always reject, wherever they appear.
    if "once opened" in low or "onceopened" in nospace:
        return True, "storage closer 'once opened'"
    if "airtight" in low:
        return True, "storage marker 'airtight'"
    # Email / website / phone with care context.
    if _CARE_EMAIL.search(s) or _URL_RE.search(s):
        return True, "email/website contact line"
    if _PHONE_LIKE.search(s) and any(
            w in low for w in ("care", "contact", "feedback", "toll",
                               "helpline", "call", "consumer", "customer")):
        return True, "care/contact phone line"
    # Care / feedback / maker / date / FSSAI / MRP / nutrition openers.
    if ("consumer care" in low or "customer care" in low
            or "consumercare" in nospace or "customercare" in nospace
            or ("executive" in low and "care" in low)
            or "toll free" in low or "tollfree" in nospace
            or "helpline" in low):
        return True, "consumer-care block marker"
    if ("feedback" in low or "feeback" in nospace or "forfeed" in nospace):
        return True, "feedback block marker"
    if (("contact" in low or "contac" in nospace)
            and any(w in low for w in ("care", "cell", "us", "consumer",
                                       "customer", "feedback"))
            or "contactus" in nospace):
        return True, "contact block marker"
    if _MAKER_EXTRA.search(s):
        return True, "maker/packer/importer opener"
    if _BB_HEAD.search(s) or _BATCH_HEAD.search(s):
        return True, "date/batch opener"
    if _MFG_HEAD.search(s) and (
            _find_mfg_date_token(s) is not None
            or re.match(r"\s*(mfd|mfg|pkd|pkg|manufactured|packed)\b",
                        low)):
        return True, "manufacturing/packing date line"
    if "fssai" in low or "fsslai" in nospace:
        return True, "FSSAI block marker"
    if _NUTRI_HEAD.search(s) or "per100g" in nospace:
        return True, "nutrition block marker"
    if _BARCODE_HEAD.search(s):
        return True, "barcode label line"
    if _MRP_LINE.search(s) and re.search(r"rs\.?|inr|\u20b9|\d", s, re.I):
        return True, "MRP declaration line"
    if re.search(r"net\s*(?:wt|qty|quantity|weight|contents?)", low) and (
            re.search(r"\d", s) is not None or ":" in s):
        return True, "net-quantity declaration line"
    # Storage openers need line-start/colon context (never blind mid-list).
    head = low[:44]
    if ("storage" in head or re.search(r"\bstore\b", head)
            or re.search(r"\bkeep\b", head)) and (
            ":" in s or "container" in low or "transfer" in low
            or "dry" in low or "cool" in low or "away" in low
            or len(s) < 70):
        return True, "storage instruction opener"
    # Damaged allergen opener with allergen payload (not mid-list prose).
    if ("tain" in nospace and any(w in nospace for w in _ALLERGEN_WORDS)
            and "," not in s and "(" not in s and "%" not in s):
        return True, "damaged allergen declaration line"
    return False, "no contamination marker with supporting context"


def filter_contaminated_ingredient_lines(
        lines: list[Any]) -> tuple[list[Any], list[dict[str, Any]]]:
    """Split ingredient-candidate lines into (accepted, rejected).

    Rejections carry {text, reason, confidence, source_box} for evidence;
    the raw OCR audit trail is untouched — rejected text stays in
    raw_text but never enters the cleaned declaration.
    """
    accepted: list[Any] = []
    rejected: list[dict[str, Any]] = []
    for ln in lines or []:
        text = str(getattr(ln, "text", "") or "")
        if not text.strip():
            continue
        bad, reason = contamination_verdict(text)
        if bad:
            try:
                conf = float(getattr(ln, "confidence", 0) or 0)
            except (TypeError, ValueError):
                conf = 0.0
            rejected.append({"text": text.strip()[:280], "reason": reason,
                             "confidence": round(conf, 3),
                             "source_box": getattr(ln, "box", None)})
        else:
            accepted.append(ln)
    return accepted, rejected


def _is_ingredient_heading(text: str) -> bool:
    """A line that opens the ingredient declaration (not net contents).

    Tolerant of OCR damage: "INGREDIENTS." / "INGREDIENTS / CONTENTS" /
    "INGRED1ENTS" / "COMPOSITON" / Hindi "सामग्री" all match, provided the
    line carries no digits (headings never contain quantities — this keeps
    "Net contents 70 g" excluded).
    """
    if _QTY_LIKE.search(text):
        return False
    if _ING_HEAD_COLON.search(text) or _ING_HEAD_BARE.match(text):
        return True
    if _ING_HEAD_HINDI.search(text):
        return True
    stripped = text.strip()
    # Quantity/date/barcode lines are excluded by _QTY_LIKE above plus the
    # digit-run guard below; digits alone must not veto ("INGRED1ENTS").
    if re.search(r"\d{4,}", stripped):
        return False
    core = _heading_core(stripped)
    if len(core) < 6:
        return False
    if core in _ING_CANONICALS:
        # Exact first-token hit: the heading may share its line with the
        # declaration itself ("INGREDIENTS Refined Wheat Flour ...",
        # colon dropped by OCR) — length is irrelevant then.
        return True
    if len(stripped) > 42:
        return False
    return any(_edit_distance(core, canon) <= 2
               for canon in _ING_CANONICALS)


def clean_ingredient_text(raw: str | None) -> tuple[str | None, list[str]]:
    """Conservative OCR-artifact cleaning for ingredient declarations.

    Removes only high-confidence garbage: domain fragments
    ("transformpilates.in"), standalone barcode digit runs, and exact
    duplicate lines. Legitimate names are never deleted — when in doubt
    the token stays. Returns (cleaned_text, notes).
    """
    if not raw:
        return None, []
    notes: list[str] = []
    lines = [ln.strip() for ln in re.split(r"[\n]+", raw) if ln.strip()]
    seen: set[str] = set()
    kept: list[str] = []
    for ln in lines:
        if _BARCODE_LINE.match(ln.replace(" ", "")):
            notes.append(f"dropped barcode-like line: {ln[:40]}")
            continue
        key = _DUP_SPACE.sub(" ", ln).lower()
        if key in seen:
            notes.append(f"dropped duplicate line: {ln[:40]}")
            continue
        seen.add(key)
        cleaned_line, n = _DOMAIN_FRAG.subn("", ln)
        if n:
            notes.append(f"removed {n} domain-like token(s)")
        # Standalone long digit runs (barcode spill, counts) are not
        # ingredient content; percentages/INS numbers are short and kept.
        barcodes = re.findall(r"\b\d(?:[\d\s\-]{6,}\d)\b", cleaned_line)
        if barcodes:
            cleaned_line = re.sub(r"\b\d(?:[\d\s\-]{6,}\d)\b", "",
                                  cleaned_line)
            notes.append(f"removed barcode-like digit run(s): "
                         f"{barcodes[0][:20]}")
        cleaned_line = _DUP_SPACE.sub(" ", cleaned_line).strip(" ,;")
        if cleaned_line:
            kept.append(cleaned_line)
    cleaned = " ".join(kept).strip() or None
    return cleaned, notes


_ADDITIVE_CATEGORIES = (
    ("preservative", re.compile(r"preservative", re.I)),
    ("colour", re.compile(r"colou?r", re.I)),
    ("flavour", re.compile(r"flavo?ur", re.I)),
    ("sweetener", re.compile(r"sweetener", re.I)),
    ("emulsifier", re.compile(r"emulsifier", re.I)),
    ("stabilizer", re.compile(r"stabiliz", re.I)),
    ("antioxidant", re.compile(r"antioxidant", re.I)),
    ("acidity regulator", re.compile(r"acidity\s+regulator", re.I)),
    ("raising agent", re.compile(r"raising\s+agent", re.I)),
    ("thickener", re.compile(r"thickener", re.I)),
)


def _additive_category(segment: str) -> str | None:
    for category, rx in _ADDITIVE_CATEGORIES:
        if rx.search(segment):
            return category
    if re.search(r"\bINS\s*\d+", segment, re.I):
        return "additive (INS)"
    return None


def _x_overlap_frac(rect: tuple[float, float, float, float],
                      span: tuple[float, float]) -> float:
    """Horizontal overlap fraction of a rect over an x-span."""
    x0, _, x1, _ = rect
    s0, s1 = span
    width = max(x1 - x0, 1e-6)
    return max(0.0, min(x1, s1) - max(x0, s0)) / width


def extract_food_label(lines: list[Any],
                       region_lines: list[Any] | None = None,
                       heading_remainder: str | None = None,
                       ingredient_source: dict[str, Any] | None = None,
                       image_columns: dict[str, tuple[float, float]] | None = None
                       ) -> dict[str, Any]:
    """Extract the structured food label.

    region_lines: targeted Stage-3 OCR of the ingredient band (crop below
    the ingredient heading). When supplied, ingredient assembly uses those
    lines instead of the full-page text. heading_remainder: the Stage-1
    heading line's own post-heading text ("INGREDIENTS: Refined Wheat
    Flour ...") — the crop starts below the heading row, so without this
    seed the first ingredients would be silently dropped.
    ingredient_source: optional hybrid provenance record
    {"ocr_source": "rapidocr"|"tesseract"|"hybrid", "fallback": {...}|None,
    "force_review": bool, "review_reason": str}. Purely additive metadata;
    force_review routes an otherwise-DETECTED read to NEEDS_REVIEW with
    the reason disclosed (never a violation).
    image_columns: optional {image_label: (x0, x1)} heading-column spans
    in line-box coordinates. Full-page assembly (no region lines) skips
    lines outside their image's column — neighbouring marketing/storage
    columns cannot join the paragraph. Lines without boxes, images
    without columns, and region-crop lines are always kept (fail-open).
    """
    parts = _parts(lines)
    full = "\n".join(t for t, _, _, _ in parts)
    fields: dict[str, Any] = {}

    # --- identity: brand = first strong line, common name, variant ---
    brand = common = variant = None
    bconf: list[float] = []
    cconf: list[float] = []
    for t, c, img, idx in parts:
        s = t.strip()
        if len(s) >= 3 and not re.match(r"^[\d\s\u20b9Rs.,/:\-]+$", s,
                                        re.I):
            if brand is None:
                brand, bconf, bimg, bidx = s, [c], img, idx
            elif common is None and s.lower() != (brand or "").lower():
                common, cconf, cimg, cidx = s, [c], img, idx
                break
    fields["brand_name"] = _field(
        brand, bconf, False, *( (bimg, bidx) if brand else (None, None)))
    fields["common_generic_name"] = _field(
        common or brand, cconf or bconf, False,
        *((cimg, cidx) if common else ((bimg, bidx) if brand else (None, None))))
    m = _VARIANT_RE.search(full)
    fields["variant_flavour"] = _field(
        m.group(0) if m else None, [], bool(m))
    fields["product_description"] = _field(None, [], False)

    # --- ingredients: header line + following lines until nutrition/other ---
    # Targeted region lines (Stage-3 crop below the heading, when the
    # service supplies them) take precedence; otherwise assemble from the
    # full-page lines following the heading.
    raw, rconfs, rimg, ridx = None, [], None, None
    col_filtered = 0
    assembly_notes: list[str] = []
    head_idx = next((i for i, (t, _, _, _) in enumerate(parts)
                     if _is_ingredient_heading(t)), None)
    region = [ln for ln in (region_lines or [])
              if str(getattr(ln, "text", "") or "").strip()]
    # Stage-1B: contamination guard runs BEFORE assembly. Rejected lines
    # (storage/care/feedback/maker/date/MRP/FSSAI/nutrition) are recorded
    # with evidence and never enter the cleaned declaration; raw_text
    # below still preserves the full audit trail.
    ingredient_rejected: list[dict[str, Any]] = []
    ingredient_accepted_texts: list[str] = []
    if region:
        kept_region, region_rej = filter_contaminated_ingredient_lines(region)
        for rej in region_rej:
            ingredient_rejected.append(rej)
            assembly_notes.append(
                f"excluded contaminated line ({rej['reason']}): "
                f"{rej['text'][:48]}")
        region = kept_region
        buf = [str(getattr(ln, "text", "") or "").strip() for ln in region]
        rconfs = [float(getattr(ln, "confidence", 0) or 0) for ln in region]
        rimg = getattr(region[0], "image", "") or None
        ridx = 0
        seed = (heading_remainder or "").strip()
        if seed and not any(seed[:24].lower() in (b or "").lower()
                            for b in buf):
            buf.insert(0, seed)
            rconfs.insert(0, rconfs[0] if rconfs else 0.0)
        # Section stop: a crop that overshoots the paragraph end (BEST
        # BEFORE / MRP / care rows inside the crop) must not absorb the
        # trailing declaration. Position-and-context-aware boundary
        # decision (never a blind keyword stop inside a real declaration).
        cut: list[str] = []
        cut_confs: list[float] = []
        for bline, bconf in zip(buf, rconfs):
            boundary = match_section_boundary(bline)
            if boundary is None:
                is_b, boundary, _why = ingredient_boundary_decision(bline)
                if not is_b:
                    boundary = None
            if boundary is not None and boundary != "INGREDIENTS" and cut:
                assembly_notes.append(f"stopped assembly at section "
                                      f"boundary: {boundary}")
                break
            cut.append(bline)
            cut_confs.append(bconf)
        buf, rconfs = cut, cut_confs
        raw = " ".join(b for b in buf if b).strip() or None
        ingredient_accepted_texts = list(buf)
    elif head_idx is not None:
        buf = [parts[head_idx][0].split(":", 1)[-1].strip()]
        rconfs.append(parts[head_idx][1])
        rimg, ridx = parts[head_idx][2], parts[head_idx][3]
        col_filtered = 0
        part_lines = list(lines or [])
        for j, (t, c, _, _) in enumerate(parts[head_idx + 1:head_idx + 12],
                                         start=head_idx + 1):
            # Any other labelled declaration (nutrition, MRP, dates,
            # FSSAI, care, batch, maker) ends the ingredient block.
            # Contamination guard first: storage/care/feedback lines are
            # recorded as rejected evidence, never assembled.
            if t.strip():
                bad, why = contamination_verdict(t)
                if bad:
                    try:
                        _cf = float(c or 0)
                    except (TypeError, ValueError):
                        _cf = 0.0
                    _ln = part_lines[j] if j < len(part_lines) else None
                    ingredient_rejected.append({
                        "text": t.strip()[:280], "reason": why,
                        "confidence": round(_cf, 3),
                        "source_box": getattr(_ln, "box", None)})
                    # A rejected section opener still ends the block
                    # (mirrors the section-head stop below): following
                    # nutrition/MRP/care rows must not join as
                    # continuations. Routine boundary stops stay silent
                    # (as before) — the exclusion itself is disclosed in
                    # rejected_lines; only mid-block exclusions add a
                    # review note.
                    if _is_section_head(t) and buf and len(
                            " ".join(buf)) > 20:
                        break
                    assembly_notes.append(
                        f"excluded contaminated line ({why}): "
                        f"{t.strip()[:48]}")
                    continue
            if _is_section_head(t) or len(t.strip()) == 0:
                if buf and len(" ".join(buf)) > 20:
                    break
                continue
            # Column guard: neighbouring-column lines cannot join the
            # paragraph. Region-crop lines were column-filtered at crop
            # level; rotated/lowconf band lines carry foreign frames;
            # boxless lines and unknown images are always kept.
            if image_columns and j < len(part_lines):
                ln = part_lines[j]
                variant = str(getattr(ln, "variant", "") or "")
                if not (variant.startswith("region:") or variant == "rot90"):
                    rect = _line_rect(ln)
                    col = image_columns.get(parts[j][2])
                    if rect is not None and col is not None and \
                            _x_overlap_frac(rect, col) < 0.3:
                        col_filtered += 1
                        continue
            buf.append(t.strip())
            rconfs.append(c)
            if len(" ".join(buf)) > 1500:
                break
        raw = " ".join(b for b in buf if b).strip() or None
        ingredient_accepted_texts = list(buf)
    t_ing = time.perf_counter()
    cleaned, cleaning_notes = clean_ingredient_text(raw)
    # Stage-1B: deterministic spacing repair on the cleaned declaration
    # (glued OCR words -> known vocabulary only; bare "(663)" untouched).
    if cleaned:
        repaired, repair_notes = repair_ingredient_spacing(cleaned)
        if repaired and repaired != cleaned:
            cleaned = repaired
            cleaning_notes = [*cleaning_notes,
                              *[f"spacing repair: {n}" for n in repair_notes]]
    cleaning_notes = [*assembly_notes, *cleaning_notes]
    if col_filtered:
        cleaning_notes = [*cleaning_notes,
                          f"column-filtered {col_filtered} off-column "
                          f"line(s) during assembly"]
    parsed = parse_ingredients(cleaned if cleaned else raw)
    for item in parsed:
        item["confidence"] = _mean(rconfs)
    scored_text = cleaned or raw
    coherence = score_ingredient_coherence(scored_text, rconfs)
    src = ingredient_source or {}
    if raw:
        mean_conf = _mean(rconfs)
        if coherence["score"] < COHERENCE_GARBAGE:
            # Garbage is never stored as a confident declaration; the raw
            # text stays available for audit, nothing is fabricated.
            detection = "NOT_DETECTED"
        elif (coherence["score"] < COHERENCE_COHERENT or cleaning_notes
              or (mean_conf is not None and mean_conf < LOW_CONF)):
            detection = "NEEDS_REVIEW"
        elif src.get("force_review"):
            # Hybrid reconciliation could not resolve the engines'
            # disagreement: human review, never a silent verdict.
            detection = "NEEDS_REVIEW"
            cleaning_notes = [*cleaning_notes,
                              "reconcile: " + str(src.get("review_reason")
                                                  or "engines disagree")]
        else:
            detection = "DETECTED"
    else:
        detection = ("NEEDS_REVIEW" if head_idx is not None or region
                     or _ING_KEYWORD.search(full) else "NOT_DETECTED")
    try:
        from app.services.ingredient_analysis import analyze_ingredients
        analysis = analyze_ingredients(parsed)
    except Exception:
        analysis = {"ingredients": [], "summary": {},
                    "note": "ingredient screening unavailable"}
    ingredients_ms = round((time.perf_counter() - t_ing) * 1000, 1)
    fields["ingredients"] = {
        "raw_text": raw,  # audit trail: exactly what OCR read
        "cleaned_text": cleaned,  # reconstruction + guard output (spec §6)
        "cleaning_notes": cleaning_notes,
        "coherence": coherence,  # 0..1 + reasons; gates the status above
        "parsed_items": parsed,
        "analysis": analysis,  # recognised / additives / unknown lists
        "confidence": _mean(rconfs),
        "provenance": "OCR",
        "ocr_source": src.get("ocr_source", "rapidocr"),
        "fallback": src.get("fallback"),
        "image": rimg, "image_index": ridx,
        "detection": detection,
        # Stage-1B pipeline evidence (RAW -> region -> accepted ->
        # rejected -> cleaned -> recognised -> additives/INS).
        "accepted_lines": ingredient_accepted_texts,
        "rejected_lines": ingredient_rejected,
        "heading": (parts[head_idx][0] if head_idx is not None else None),
    }

    # --- nutrition panel (table-aware reconstruction) ---
    t_nut = time.perf_counter()
    nutri: dict[str, Any] = {}
    nutri_block, nutri_confs = _reconstruct_nutrition_block(parts)
    nidx = next((i for i, (t, _, _, _) in enumerate(parts)
                 if _NUTRI_HEAD.search(t)), None)
    hay = nutri_block or full
    for name, rx in _NUTRIENTS:
        mm = rx.search(hay)
        nutri[name] = _field(mm.group(1).strip() if mm else None,
                             (nutri_confs if mm else []),
                             bool(nidx is not None or _NUTRI_HEAD.search(full)))
    fields["nutrition"] = nutri
    nutrition_ms = round((time.perf_counter() - t_nut) * 1000, 1)

    # --- business / traceability ---
    # The anchor line gives the name + role; following address-like lines
    # (PIN, Road/Street/Nagar/State, India) are collected via line order
    # and OCR boxes — never an arbitrary full-page regex grab.
    maker = packer = importer = address = care_ph = care_em = web = fssai = None
    maker_role = None
    mconf: list[float] = []
    mimg = midx = None
    maker_idx = next((i for i, (t, _, _, _) in enumerate(parts)
                      if _MAKER_HEAD.search(t)), None)
    if maker_idx is not None:
        t, c, img, idx = parts[maker_idx]
        anchor = t.strip()
        low = anchor.lower()
        if "import" in low:
            maker_role = "Importer"
        elif "market" in low:
            maker_role = "Marketer"
        elif "pack" in low:
            maker_role = "Packer"
        else:
            maker_role = "Manufacturer"
        maker = re.sub(r"^(?:manufactured(?:\s*&\s*marketed)?(?:\s*/\s*packed)?"
                       r"\s+by|marketed\s+by|packed\s+by|mfd\.?\s+by|"
                       r"mkd\.?\s+by|mfg\.?\s+by|"
                       r"imported\s+by)[\s:]*", "", anchor,
                       flags=re.I).strip(" ,:-") or anchor
        mconf, mimg, midx = [c], img, idx
        # Address continuation: next lines that look like an address and do
        # not open another labelled section.
        addr_buf = []
        for j in range(maker_idx + 1, min(maker_idx + 4, len(parts))):
            nxt = parts[j][0].strip()
            if not nxt or _is_section_head(nxt):
                break
            if _ADDRESS_LIKE.search(nxt):
                addr_buf.append(nxt)
                mconf.append(parts[j][1])
            elif addr_buf and len(nxt) > 3 and "," in nxt:
                addr_buf.append(nxt)
                mconf.append(parts[j][1])
            elif not addr_buf and len(nxt) > 3:
                # First continuation line: often the street/locality.
                addr_buf.append(nxt)
                mconf.append(parts[j][1])
            else:
                break
        address = " ".join(addr_buf).strip() or None
    for t, c, img, idx in parts:
        if packer is None and _PACKER_HEAD.search(t):
            packer = t.strip()
        if importer is None and _IMPORTER_HEAD.search(t):
            importer = t.strip()
    em = _CARE_EMAIL.search(full)
    um = _URL_RE.search(full)
    fm = re.search(r"fssai[^0-9]{0,25}(\d[\d\s]{12,20}\d)", full, re.I)
    fields["manufacturer"] = _field(maker, mconf, bool(_MAKER_HEAD.search(full)),
                                    *((mimg, midx) if maker else (None, None)))
    fields["manufacturer_name"] = fields["manufacturer"]
    fields["manufacturer_role"] = _field(
        maker_role, mconf, bool(_MAKER_HEAD.search(full)))
    fields["packer"] = _field(packer, [], bool(_PACKER_HEAD.search(full)))
    fields["importer"] = _field(importer, [], bool(_IMPORTER_HEAD.search(full)))
    fields["manufacturer_address"] = _field(
        address, mconf if address else [],
        bool(_MAKER_HEAD.search(full)))
    fields["consumer_care_email"] = _field(
        em.group(0) if em else None, [], bool(em))
    fields["website"] = _field(um.group(0) if um else None, [], bool(um))
    fields["fssai_license"] = _field(
        re.sub(r"\D", "", fm.group(1)) if fm and
        len(re.sub(r"\D", "", fm.group(1))) == 14 else None,
        [], bool(_FSSAI_HEAD.search(full)))

    # --- symbols / declarations ---
    vm = _VEG_TEXT.search(full)
    fields["veg_nonveg_symbol_text"] = _field(
        vm.group(0) if vm else None, [], bool(vm))
    allergen_block = None
    aidx = next((i for i, (t, _, _, _) in enumerate(parts)
                 if _ALLERGEN_HEAD.search(t)), None)
    if aidx is not None:
        allergen_block = _window_text(parts, aidx, 4)
    fields["allergens"] = _field(allergen_block, [], aidx is not None)
    sidx = next((i for i, (t, _, _, _) in enumerate(parts)
                 if _STORAGE_HEAD.search(t)), None)
    fields["storage_instructions"] = _field(
        _window_text(parts, sidx, 3) if sidx is not None else None, [],
        sidx is not None)
    cidx = next((i for i, (t, _, _, _) in enumerate(parts)
                 if _COOK_HEAD.search(t)), None)
    fields["cooking_instructions"] = _field(
        _window_text(parts, cidx, 4) if cidx is not None else None, [],
        cidx is not None)
    widx = next((i for i, (t, _, _, _) in enumerate(parts)
                 if _WARN_HEAD.search(t)), None)
    fields["warnings"] = _field(
        _window_text(parts, widx, 3) if widx is not None else None, [],
        widx is not None)

    # --- dates / batch / mrp context flags (values live in fields.py) ---
    fields["manufacturing_date_context"] = bool(_MFG_HEAD.search(full))
    fields["best_before_context"] = bool(_BB_HEAD.search(full))
    fields["batch_context"] = bool(_BATCH_HEAD.search(full))
    fields["mrp_context"] = bool(_MRP_LINE.search(full))

    detected = sum(1 for v in fields.values()
                   if isinstance(v, dict) and v.get("detection") == "DETECTED")
    status = "OK" if detected else "NEEDS_REVIEW"
    return {"status": status, "provenance": "OCR", "fields": fields,
            "timings": {"ingredients_ms": ingredients_ms,
                        "nutrition_ms": nutrition_ms}}


# --- Stage-1B deterministic ingredient reconstruction (spec §4) ---
# Order, percentages, INS numbers, parentheses and supported commas are
# preserved; words split across OCR lines are rejoined; glued words are
# separated ONLY when every piece is known food vocabulary (never
# invented); bare digit runs like "(663)" are kept verbatim and never
# promoted to INS codes without an explicit INS/E prefix.
def _ingredient_vocab() -> set[str]:
    """Known food-word vocabulary for safe glued-word segmentation."""
    vocab = set(_COMMON_FOOD_WORDS)
    vocab.update(_registry_vocab())
    vocab.update(
        "refined wheat flour maida sugar palm oil milk salt spices mixed "
        "herbs tomato onion powder water gluten rice corn soya starch "
        "contains allergen ingredients list".split())
    return vocab


def _segment_glued_token(token: str, vocab: set[str]) -> str | None:
    """Split a glued ALLCAPS/word token into known vocabulary words.

    Returns the spaced form only when the ENTIRE token segments into
    known words (each >= 2 chars); otherwise None (left untouched —
    never invent). Longest-match greedy with backtracking over the
    lowercased token.
    """
    low = token.lower()
    if len(low) < 10 or " " in token:
        return None
    if low in vocab:
        return None  # already a known word: nothing to do
    memo: dict[int, list[str] | None] = {}

    def _split(pos: int) -> list[str] | None:
        if pos == len(low):
            return []
        if pos in memo:
            return memo[pos]
        # Longest candidate first (prefer "wheat" over "whe"+"at").
        for end in range(len(low), pos + 1, -1):
            piece = low[pos:end]
            if len(piece) < 2 or piece not in vocab:
                continue
            rest = _split(end)
            if rest is not None:
                memo[pos] = [piece] + rest
                return memo[pos]
        memo[pos] = None
        return None

    pieces = _split(0)
    if not pieces or len(pieces) < 2:
        return None
    # Preserve the original capitalisation style (upper vs title).
    if token.isupper():
        return " ".join(p.upper() for p in pieces)
    if token[:1].isupper():
        return " ".join(p.capitalize() for p in pieces)
    return " ".join(pieces)


def repair_ingredient_spacing(text: str | None) -> tuple[str | None, list[str]]:
    """Deterministic spacing repair for ingredient declarations.

    - Missing space before "(" ("FLOUR(MAIDA)" -> "FLOUR (MAIDA)").
    - Glued words split only into known food vocabulary
      ("REFINEDWHEATFLOUR" -> "REFINED WHEAT FLOUR").
    Returns (repaired, notes). Never invents words; never converts bare
    numbers such as "(663)" into INS codes.
    """
    if not text:
        return text, []
    notes: list[str] = []

    def _paren_space(m: re.Match) -> str:
        # Never separate a digit from an INS-subclass suffix: "500(ii)"
        # is valid notation and must survive verbatim.
        prev, inner = m.group(1), m.group(2)
        if prev.isdigit() and re.fullmatch(r"[ivxlcdm]+", inner, re.I):
            return m.group(0)
        return prev + " (" + inner + ")"

    out = re.sub(r"(\S)\(([^()]*)\)", _paren_space, text)
    if out != text:
        notes.append("restored space before parenthesis")
    vocab = _ingredient_vocab()
    fixed_tokens: list[str] = []
    for tok in out.split(" "):
        core = re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", tok)
        if len(core) >= 10 and core.isalpha():
            split = _segment_glued_token(core, vocab)
            if split is not None:
                start = tok[:tok.find(core)] if core in tok else ""
                end = tok[tok.find(core) + len(core):] if core in tok else ""
                fixed_tokens.append(start + split + end)
                notes.append(f"separated glued word: {core[:24]}")
                continue
        fixed_tokens.append(tok)
    out = " ".join(fixed_tokens)
    out = _DUP_SPACE.sub(" ", out).strip()
    return out, notes


# --- ingredient OCR reconstruction -------------------------------------
# Ingredient lists are not prose: words break across boxes, INS numbers
# arrive as "5O1" / "(ii)" / "E500", and spacing inside numbers wanders.
# Repairs below apply ONLY inside tightly-gated patterns (INS/E-number
# tokens, hyphen line-breaks, intra-parenthesis digit runs) — arbitrary
# words are never autocorrected.
_INS_TOKEN = re.compile(
    r"\bINS\s*([0-9OIl]{1,4})(?:\s*\(([0-9OIlivxX|]{1,6})\))?", re.I)
_INS_BARE_PAREN = re.compile(r"\(\s*([0-9OIl]{3,4}(?:\s*\([0-9OIlivx|X|]+\))?)\s*\)")
_E_TOKEN = re.compile(r"\bE\s*([0-9OIl]{3,4})\b")
_DIGIT_CONFUSION = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1",
                                  "|": "1"})


def _repair_ins_token(token: str) -> str:
    """Map OCR letter/digit confusions strictly inside an INS/E token."""
    return token.translate(_DIGIT_CONFUSION)


# Valid INS parenthetical suffixes (roman numerals / subclass letters).
# Digits are never valid here, so a digit-looking glyph inside the suffix
# is far more likely a misread letter than vice versa.
_INS_SUFFIX_VALID = frozenset(
    ["i", "ii", "iii", "iv", "v", "a", "b", "c", "d", "e", "f"])
_INS_SUFFIX_MAP = str.maketrans({"I": "i", "l": "i", "1": "i", "|": "i",
                                 "O": "o", "0": "o"})


def _ins_number_for_segment(seg: str) -> str | None:
    """INS number for one ingredient segment, or None.

    Requires an explicit INS/E prefix in the text — or a square-bracket
    group carrying a roman-letter suffix ("[322(i)]"), which on Indian
    packs is the additive-subclass notation. A bare digit run ("(4510)",
    "[471]") is kept verbatim in the segment but never promoted to an
    INS number — inventing additive identities from bare digits is
    forbidden.
    """
    ins = _INS_TOKEN.search(seg)
    if ins is None:
        # Bracketed subclass form only: digits + roman suffix in [...] and
        # no plain-INS alternative present.
        m = re.search(r"\[\s*([0-9OIl]{3,4})\s*\(\s*([ivxlcdm]+)\s*\)\s*\]",
                      seg, re.I)
        if not m:
            return None
        num = _repair_ins_token(m.group(1)).replace(" ", "")
        if not re.fullmatch(r"[0-9]+", num):
            return None
        return f"INS {num}({m.group(2).lower()})"
    # Uppercase the prefix/digits only: roman-numeral suffixes stay
    # lowercase ("INS 500(ii)" is the valid notation).
    num = _repair_ins_token(ins.group(1)).replace(" ", "")
    head = re.match(r"[0-9]+", num)
    if not head:
        return None
    suffix_raw = ins.group(2) or ""
    if not suffix_raw:
        return "INS " + head.group(0)
    suffix, suffix_ok = _repair_ins_suffix(suffix_raw)
    return "INS " + head.group(0) + (f"({suffix})" if suffix_ok
                                     else f"({suffix_raw})")


def _repair_ins_suffix(suffix: str) -> tuple[str, bool]:
    """Normalise an INS parenthetical suffix; (repaired, known-good?).

    "(il)" -> "(ii)" (valid); anything still outside the known set is
    left untouched — never invented.
    """
    norm = suffix.translate(_INS_SUFFIX_MAP)
    if norm in _INS_SUFFIX_VALID:
        return norm, True
    if suffix in _INS_SUFFIX_VALID:
        return suffix, True
    return suffix, False


def _squeeze_paren_numbers(text: str) -> str:
    """Collapse stray spaces inside parenthesised digit runs: (4 51)->(451)."""
    def _fix(m: re.Match) -> str:
        inner = re.sub(r"\s+", "", m.group(1))
        return "(" + inner + ")"
    return re.sub(r"\(\s*(\d(?:[\d\s]{0,6})\d)\s*\)", _fix, text)


def reconstruct_ingredient_text(lines: list[Any]) -> tuple[str, list[str]]:
    """Rebuild one ingredient paragraph from region OCR lines.

    Returns (text, notes). Box-ordered (top-to-bottom rows, left-to-right
    within a row); hyphenated line-breaks rejoined; INS/E-number tokens
    normalised; every repair disclosed in notes.
    """
    notes: list[str] = []
    rows: list[tuple[str, float]] = []
    for ln in lines or []:
        text = (getattr(ln, "text", "") or "").strip()
        if text:
            try:
                conf = float(getattr(ln, "confidence", 0) or 0)
            except (TypeError, ValueError):
                conf = 0.0
            rows.append((text, conf))
    if not rows:
        return "", ["no region lines to reconstruct"]
    ordered = sorted(
        enumerate(rows),
        key=lambda pair: (_row_key(lines[pair[0]]
                                   if pair[0] < len(lines) else None,
                                   pair[0])))
    frags = [rows[i][0] for i, _ in ordered]
    # Stop at the next labelled section (storage/care/nutrition/...): a
    # crop that overshoots the paragraph end must not absorb the
    # following declaration. Only after content started, and never on
    # another INGREDIENTS opener (continued paragraph edge case).
    stopped: list[str] = []
    kept_frags: list[str] = []
    for frag in frags:
        boundary = match_section_boundary(frag)
        if boundary is not None and boundary != "INGREDIENTS" and kept_frags:
            stopped.append(f"{boundary}: {frag[:48]}")
            break
        kept_frags.append(frag)
    if stopped:
        notes.append("stopped at section boundary: " + "; ".join(stopped))
    frags = kept_frags
    # Rejoin hyphen-broken words across fragments ("pack- aged"->"packaged").
    merged: list[str] = []
    for frag in frags:
        if merged and merged[-1].endswith(("-", "¬")):
            head = merged.pop()
            tail = frag.lstrip()
            if re.match(r"^[A-Za-z]+$", tail.split(" ")[0] if tail else ""):
                merged.append(head[:-1] + tail)
                notes.append(f"rejoined hyphen break: {head[-12:]}+{tail[:12]}")
                continue
        merged.append(frag)
    text = " ".join(merged)
    # Normalise INS tokens: "INS 5O1" -> "INS 501". A parenthetical
    # suffix is repaired only onto a known-good form ("(il)" -> "(ii)");
    # anything else is left verbatim, never invented.
    def _ins_fix(m: re.Match) -> str:
        num = _repair_ins_token(m.group(1)).replace(" ", "")
        suffix_raw = m.group(2) or ""
        if not num.isdigit():
            return m.group(0)  # not actually numeric: leave alone
        if not suffix_raw:
            fixed = "INS " + num
        else:
            suffix, suffix_ok = _repair_ins_suffix(suffix_raw)
            fixed = "INS " + num + (f"({suffix})" if suffix_ok
                                    else f"({suffix_raw})")
        if fixed != m.group(0):
            notes.append(f"normalised INS token: {m.group(0)}->{fixed}")
        return fixed
    text = _INS_TOKEN.sub(_ins_fix, text)

    def _bare_fix(m: re.Match) -> str:
        fixed = "(" + _repair_ins_token(m.group(1)).replace(" ", "") + ")"
        if fixed != m.group(0):
            notes.append(f"normalised additive number: {m.group(0)}->{fixed}")
        return fixed
    text = _INS_BARE_PAREN.sub(_bare_fix, text)

    def _e_fix(m: re.Match) -> str:
        fixed = "E" + _repair_ins_token(m.group(1))
        if fixed != m.group(0):
            notes.append(f"normalised E-number: {m.group(0)}->{fixed}")
        return fixed
    text = _E_TOKEN.sub(_e_fix, text)
    before = text
    text = _squeeze_paren_numbers(text)
    if text != before:
        notes.append("collapsed spaces inside parenthesised numbers")
    text = _DUP_SPACE.sub(" ", text).strip()
    return text, notes


def _row_key(line: Any, fallback: int) -> tuple[float, float]:
    """Sort key for region lines: row (top) then column (left)."""
    try:
        box = getattr(line, "box", None)
        if box:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            return (min(ys), min(xs))
    except (TypeError, IndexError, ValueError):
        pass
    return (float(fallback), 0.0)


def _line_rect(line: Any) -> tuple[float, float, float, float] | None:
    """(x0, y0, x1, y1) for a line's box, or None when unusable."""
    try:
        box = getattr(line, "box", None)
        if not box:
            return None
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        if not xs or not ys:
            return None
        return (min(xs), min(ys), max(xs), max(ys))
    except (TypeError, IndexError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v > 0)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def cluster_text_columns(lines: list[Any]) -> list[dict[str, Any]]:
    """Cluster boxed lines into visual columns by x-overlap.

    Each line joins the existing column it overlaps most (by interval
    overlap fraction); otherwise it starts a new column. Lines without
    usable boxes are left unassigned (``column: None``) so callers fail
    open. Returns columns sorted by x0 with member indices, x-range and
    text mass. Pure geometry — no text semantics.
    """
    indexed: list[tuple[int, float, float]] = []
    for i, ln in enumerate(lines or []):
        rect = _line_rect(ln)
        if rect is None:
            continue
        x0, _, x1, _ = rect
        if x1 <= x0:
            continue
        indexed.append((i, x0, x1))
    columns: list[dict[str, Any]] = []
    for i, x0, x1 in sorted(indexed, key=lambda t: (t[1], t[2])):
        best, best_overlap = None, 0.0
        width = x1 - x0
        for col in columns:
            inter = max(0.0, min(x1, col["x1"]) - max(x0, col["x0"]))
            overlap = inter / min(width, col["x1"] - col["x0"])
            if overlap > best_overlap:
                best, best_overlap = col, overlap
        if best is not None and best_overlap >= 0.3:
            best["idxs"].append(i)
            best["x0"] = min(best["x0"], x0)
            best["x1"] = max(best["x1"], x1)
        else:
            columns.append({"x0": x0, "x1": x1, "idxs": [i]})
    columns.sort(key=lambda c: (c["x0"], c["x1"]))
    return columns


# Marketing vocabulary: a NEGATIVE signal only. A line is never rejected
# for marketing words alone — structural evidence (column, continuity,
# boundaries) must also fail. Marketing-heavy lines inside the ingredient
# column are kept (they may be flavour names, not contamination).
_MARKETING_WORDS = frozenset(
    "tasty yummy delicious crunchy munchy new offer free prize win "
    "cartoon fun magic smile love best choice premium gold".split())


def column_membership(line: Any, column: dict[str, Any] | None,
                      line_height: float | None = None) -> dict[str, Any]:
    """Structural membership of one line in a text column (0..1 + reasons).

    Signals: x-overlap with the column, y-proximity is handled by the
    caller (reading order); marketing words contribute only a small
    negative weight and can never alone reject a line.
    """
    reasons: list[str] = []
    if column is None:
        return {"score": 1.0, "reasons": ["no column resort: kept"]}
    rect = _line_rect(line)
    if rect is None:
        return {"score": 1.0, "reasons": ["no box: kept (fail-open)"]}
    x0, _, x1, _ = rect
    width = max(x1 - x0, 1.0)
    col_w = max(column["x1"] - column["x0"], 1.0)
    inter = max(0.0, min(x1, column["x1"]) - max(x0, column["x0"]))
    overlap = inter / min(width, col_w)
    score = min(1.0, overlap / 0.5)  # >=50% overlap counts as full member
    reasons.append(f"x-overlap {overlap:.2f}")
    text = str(getattr(line, "text", "") or "").lower()
    words = set(re.findall(r"[a-z]+", text))
    marketing = sorted(words & _MARKETING_WORDS)
    if marketing:
        score = max(0.0, score - 0.2)
        reasons.append("marketing words (weak negative): "
                       + ",".join(marketing[:4]))
    return {"score": round(score, 3), "reasons": reasons}


def _majority_column(lines: list[Any]) -> dict[str, Any] | None:
    """Infer the dominant text column: most text mass wins, ties broken
    by topmost start (the ingredient body begins at the crop top, right
    below the heading row, while side columns usually start lower)."""
    columns = cluster_text_columns(lines)
    if not columns:
        return None
    scored = []
    for col in columns:
        mass = sum(len(str(getattr(lines[i], "text", "") or ""))
                   for i in col["idxs"])
        tops = []
        for i in col["idxs"]:
            rect = _line_rect(lines[i])
            if rect is not None:
                tops.append(rect[1])
        scored.append((mass, -min(tops) if tops else 0.0, col))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


def filter_offcolumn_lines(lines: list[Any],
                           column: dict[str, Any] | None = None,
                           threshold: float = 0.3
                           ) -> tuple[list[Any], list[dict[str, Any]]]:
    """Split lines into (kept, rejected) by column membership.

    With ``column=None`` the dominant column is inferred (most text mass,
    topmost wins ties) — used for post-crop filtering where the crop
    itself defines the frame. Fail-open by design: with no resolvable
    column, no boxes, or a single line, everything is kept. Rejections
    carry reasons for evidence/audit.
    """
    items = list(lines or [])
    if len(items) <= 1:
        return items, []
    if column is None:
        column = _majority_column(items)
    if column is None:
        return items, []
    if not any(_line_rect(ln) is not None for ln in items):
        return items, []
    kept: list[Any] = []
    rejected: list[dict[str, Any]] = []
    for ln in items:
        verdict = column_membership(ln, column)
        if verdict["score"] >= threshold:
            kept.append(ln)
        else:
            rejected.append({"text": str(getattr(ln, "text", "") or "")[:80],
                             "score": verdict["score"],
                             "reasons": verdict["reasons"]})
    if kept:
        return kept, rejected
    # Everything failed: keep all (a wrong column must not nuke evidence).
    return items, [{"text": "(column filter abstained: kept all)",
                    "score": 0.0,
                    "reasons": ["fail-open: no line passed threshold"]}]


# Common food words for coherence scoring ONLY (never used to invent
# ingredients — recognition signal, not a hallucination source).
_COMMON_FOOD_WORDS = frozenset(
    "wheat flour maida refined palm oil salt sugar spices mixed herbs "
    "tomato onion milk powder water gluten rice corn soya soy starch "
    "dextrose maltodextrin cocoa chocolate vanilla flavours flavour "
    "colour colours preservative acidity regulator raising agent "
    "emulsifier stabilizer antioxidant thickener humectant sweetener "
    "lecithin citric acid tartaric malic sodium potassium calcium "
    "carbonate bicarbonate phosphate sulphite sorbate benzoate "
    "tocopherol ascorbic niacin iron zinc noodle noodles pasta atta "
    "rava semolina besan dal lentil peanut groundnut mustard cumin "
    "coriander turmeric chilli pepper ginger garlic onion".split())


def _registry_vocab() -> set[str]:
    """Token vocabulary from configured registry names (scoring only)."""
    try:
        from app.services.ingredient_analysis import load_registry
        words: set[str] = set()
        for name in load_registry().get("entries", {}):
            words.update(re.findall(r"[a-z]+", str(name).lower()))
        return words
    except Exception:
        return set()


def score_ingredient_coherence(text: str | None,
                               confs: list[float] | None = None
                               ) -> dict[str, Any]:
    """Coherence score 0..1 for an ingredient paragraph + reasons.

    Signals: alphabetic-word ratio, impossible sequences (long vowelless
    tokens, stray symbols), registry/common-vocabulary hits, INS/additive
    hits, parenthesis balance, comma structure, repeated fragments, mean
    OCR confidence. Garbage like "NodsWhfuEdibiegbieil..." scores near 0;
    a clean declaration scores near 1.
    """
    reasons: list[str] = []
    if not text or not text.strip():
        return {"score": 0.0, "reasons": ["empty text"]}
    toks = text.split()
    if not toks:
        return {"score": 0.0, "reasons": ["empty text"]}
    alpha = sum(1 for t in toks
                if sum(c.isalpha() for c in t) >= max(2, len(t) // 2))
    alpha_ratio = alpha / len(toks)
    reasons.append(f"alpha-word ratio {alpha_ratio:.2f}")
    bad = 0
    for t in toks:
        core = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", t)
        if len(core) >= 6 and re.search(r"[A-Za-z]", core) \
                and not re.search(r"[aeiouAEIOU]", core) \
                and not re.match(r"^(?:INS|E\d|Mc|De|Le)[A-Za-z]*$", core):
            bad += 1  # vowelless blob: "NdsWhf", "gbieil"
        if re.search(r"[@#$%^*_+=~`|\\]", core):
            bad += 1
        if re.search(r"(.)\1{3,}", core):  # "aaaa", "1111" runs
            bad += 1
    bad_ratio = bad / len(toks)
    if bad_ratio:
        reasons.append(f"impossible-sequence ratio {bad_ratio:.2f}")
    vocab = _COMMON_FOOD_WORDS | _registry_vocab()
    words = re.findall(r"[a-z]{3,}", text.lower())
    hits = sum(1 for w in words if w in vocab)
    vocab_ratio = (hits / len(words)) if words else 0.0
    reasons.append(f"vocabulary-hit ratio {vocab_ratio:.2f}")
    ins_hits = len(_INS_TOKEN.findall(text)) + len(_E_TOKEN.findall(text)) \
        + len(_INS_BARE_PAREN.findall(text))
    if ins_hits:
        reasons.append(f"INS/additive tokens {ins_hits}")
    paren_ok = text.count("(") == text.count(")")
    if not paren_ok:
        reasons.append("unbalanced parentheses")
    has_commas = "," in text
    if not has_commas and len(toks) > 6:
        reasons.append("no comma structure")
    lowered = text.lower()
    dup = len(re.findall(r"(\b.{8,}?\b)(?=.*\1)", lowered))
    if dup:
        reasons.append(f"repeated fragments {dup}")
    mean_conf = 0.0
    if confs:
        vals = [c for c in confs if c]
        mean_conf = sum(vals) / len(vals) if vals else 0.0
        reasons.append(f"mean OCR confidence {mean_conf:.2f}")
    score = (0.30 * alpha_ratio
             + 0.30 * vocab_ratio
             + min(0.12, 0.04 * ins_hits)
             + (0.08 if paren_ok else 0.0)
             + (0.05 if has_commas else 0.0)
             + 0.15 * mean_conf
             - 0.35 * min(1.0, bad_ratio * 2)
             - min(0.10, 0.02 * dup))
    return {"score": round(max(0.0, min(1.0, score)), 3),
            "reasons": reasons}


_SCAN_DECL_MARKERS = (
    re.compile(r"fssai|lic\.?\s*no|licence|license", re.I),
    re.compile(r"\bm\.?\s*r\.?\s*p\b|maximum\s+retail\s+price", re.I),
    re.compile(r"\bmfd\b|\bmfg\b|\bpkd\b|manufactured\s+by", re.I),
    re.compile(r"\d[\d\s]{12,20}\d"),  # 14-digit style runs
)


def scan_result_usable(text: str | None,
                       coherence: dict[str, Any] | None) -> bool:
    """Whether a no-heading dense-scan result may join the evidence.

    A scan that mostly captured a declaration block (FSSAI/MRP/dates +
    long digit runs) without coherent ingredient content is discarded so
    it cannot poison the pooled text. Returns True only when the text
    carries real ingredient signal.
    """
    if not text or not text.strip():
        return False
    score = (coherence or {}).get("score", 0.0) or 0.0
    markers = sum(1 for rx in _SCAN_DECL_MARKERS if rx.search(text))
    words = re.findall(r"[a-z]{3,}", text.lower())
    vocab = _COMMON_FOOD_WORDS | _registry_vocab()
    if sum(1 for w in words if w in vocab) == 0:
        return False  # no recognisable food content at all
    if markers >= 2 and score < COHERENCE_COHERENT:
        return False  # declaration block, not ingredients
    return True


def _split_top_level(text: str) -> list[str]:
    """Split on commas/semicolons, respecting (nested) parentheses.

    OCR routinely drops a parenthesis ("(70%" / "(INS 500 (ii)"), which
    would leave the depth counter stuck and collapse the whole list into
    one item. When parens are unbalanced, fall back to plain comma
    splitting — a split-apart subgroup is reviewable, a merged blob is
    useless.
    """
    if text.count("(") != text.count(")"):
        return [s.strip(" ,;") for s in re.split(r"[;,]", text)
                if s.strip(" ,;")]
    depth, buf, segments = 0, "", []
    for ch in text:
        if ch == "(":
            depth += 1
            buf += ch
        elif ch == ")":
            depth = max(0, depth - 1)
            buf += ch
        elif ch in (",", ";") and depth == 0:
            segments.append(buf.strip(" ,;"))
            buf = ""
        else:
            buf += ch
    if buf.strip():
        segments.append(buf.strip(" ,;"))
    return [s for s in segments if s]


def _parse_sub_components(segment: str) -> list[dict[str, Any]]:
    """Recursively parse parenthetical compound-ingredient groups."""
    subs: list[dict[str, Any]] = []
    for m in re.finditer(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)", segment):
        inner = m.group(1)
        for part in _split_top_level(inner):
            pct = re.search(r"(\d+(?:\.\d+)?\s*%)", part)
            name = re.sub(r"\s*\(.*?\)\s*", " ", part)
            name = re.sub(r"\s*\d+(?:\.\d+)?\s*%.*$", "",
                          name).strip(" -–—:,. ")
            subs.append({
                "name": name or part[:80],
                "percentage": pct.group(1) if pct else None,
            })
    return subs


def parse_ingredients(raw: str | None) -> list[dict[str, Any]]:
    """Split an ingredient declaration preserving percentages/INS/brackets.

    Each record carries name/percentage/ins_number/category plus source
    and (record-level) provenance. Legacy ``ingredient`` key is kept as an
    alias of ``name``. Classification into legal statuses happens only in
    ingredient_analysis.py against the configured registry — never here.
    """
    if not raw:
        return []
    text = re.sub(r"\s+", " ", raw).strip()
    items: list[dict[str, Any]] = []
    for seg in _split_top_level(text):
        pct = re.search(r"(\d+(?:\.\d+)?\s*%)", seg)
        ins_text = _ins_number_for_segment(seg)
        # Strip parenthetical groups iteratively so nested forms like
        # "(INS 500(ii))" leave no stray ")".
        name = seg
        for _ in range(3):
            reduced = re.sub(r"\s*\([^()]*\)\s*", " ", name)
            if reduced == name:
                break
            name = reduced
        name = re.sub(r"\s*\d+(?:\.\d+)?\s*%.*$", "", name).strip(" -–—:,. ")
        items.append({
            "name": name or seg[:80],
            "ingredient": name or seg[:80],  # legacy alias
            "raw_segment": seg[:280],
            "percentage": pct.group(1) if pct else None,
            "ins_number": (re.sub(r"\s+", " ", ins_text).strip()
                           if ins_text else None),
            "category": _additive_category(seg),
            "source": "OCR",
            "confidence": None,  # filled by the caller from line confidences
            "sub_components": _parse_sub_components(seg),
        })
    return items
