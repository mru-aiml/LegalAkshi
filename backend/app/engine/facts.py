"""Product-fact builder: maps inspected_products columns + extracted
declarations onto engine_check_registry.field_name values.

Every fact carries (value, confidence, origin) where origin is one of:
  OCR      — measured OCR/extraction confidence from extracted_declarations
  MANUAL   — manually supplied declaration (confidence NULL, never fabricated)
  ASSUMED  — test/demo assumption (unit tests pass default_origin="ASSUMED")

Only OCR confidence below threshold triggers automatic NEEDS_REVIEW.
MANUAL/ASSUMED values are evaluated as stated and the origin is exposed
on every finding, so a manual value is never misrepresented as OCR evidence.

Documented column -> registry-field mappings (authoritative
inspected_products has no dedicated columns for these):
  product_name        -> common_generic_name (the declared name IS the
                         common/generic name for check purposes)
  quantity            -> net_quantity   (unit from quantity_unit)
  manufacturing_date  -> mfg_month_year
  source_listing_url  -> ecommerce_declarations (listing carries declarations)
  country_of_origin_filter -> ecommerce_coo_filter
  qr_code present     -> qr_declared = True
  quantity+quantity_unit -> quantity_kg / quantity_litre (Rule 3 exclusion)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

OCR, MANUAL, ASSUMED = "OCR", "MANUAL", "ASSUMED"

# product column -> registry fact field (same name omitted)
COLUMN_MAP = {
    "manufacturer": "manufacturer",
    "product_name": "common_generic_name",
    "quantity": "net_quantity",
    "manufacturing_date": "mfg_month_year",
    "best_before": "best_before",
    "country_of_origin": "country_of_origin",
    "qr_code": "qr_code",
    "source_listing_url": "ecommerce_declarations",
    "country_of_origin_filter": "ecommerce_coo_filter",
}

# flags that pass through under their own name (from columns or declarations)
PASSTHROUGH = {
    "imported", "ecommerce", "category", "subcategory", "quantity_type",
    "electronic_product", "food", "cosmetic", "may_become_unfit",
    "genetically_modified", "combination_package", "group_package",
    "multi_piece_package", "package_qr_notice_present", "medical_device",
    "is_prepackaged", "quantity_unit",
    # registry fields supplied as manual/OCR declarations
    "mrp", "consumer_care", "gm_label", "veg_nonveg_dot", "unit_sale_price",
    "dimensions", "other_declarations", "common_generic_name", "mfg_month_year",
    "net_quantity", "manufacturer", "best_before", "country_of_origin",
    "ecommerce_declarations", "ecommerce_coo_filter", "qr_code",
}


def _coerce(value: Any) -> Any:
    if isinstance(value, str):
        low = value.strip().lower()
        if low == "true":
            return True
        if low == "false":
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
    return value


def _origin_of(ocr_engine: Any) -> str:
    eng = str(ocr_engine or "").lower()
    if eng in ("manual-entry", "manual"):
        return MANUAL
    if eng.startswith(("test", "assumed")):
        return ASSUMED
    if eng:
        return OCR
    return MANUAL


def build_facts(product: dict[str, Any],
                declarations: list[dict[str, Any]] | None = None,
                default_origin: str = MANUAL) -> dict[str, dict[str, Any]]:
    """Return {field: {"value", "confidence", "origin", "evidence_id"}}."""
    facts: dict[str, dict[str, Any]] = {}

    def put(field: str, value: Any, confidence: Any = None,
            origin: str = MANUAL, evidence_id: Any = None) -> None:
        if value is None or (isinstance(value, str) and value == ""):
            # keep explicit empty (missing) only if nothing set yet
            facts.setdefault(field, {"value": None, "confidence": confidence,
                                     "origin": origin, "evidence_id": evidence_id})
            return
        facts[field] = {"value": _coerce(value), "confidence": confidence,
                        "origin": origin, "evidence_id": evidence_id}

    for col, field in COLUMN_MAP.items():
        if col in product and product[col] is not None:
            put(field, product[col], None, default_origin)
    for flag in PASSTHROUGH:
        if flag in product and product[flag] is not None and flag not in facts:
            put(flag, product[flag], None, default_origin)

    for d in declarations or []:
        field = d.get("field_name")
        if not field:
            continue
        raw = d.get("normalized_value")
        if raw is None:
            raw = d.get("extracted_value")
        conf = d.get("confidence")
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        put(field, raw, conf, _origin_of(d.get("ocr_engine")),
            d.get("evidence_id"))

    # derived flags
    qr = facts.get("qr_code", {}).get("value")
    facts.setdefault("qr_declared",
                     {"value": bool(qr), "confidence": None,
                      "origin": facts.get("qr_code", {}).get("origin", default_origin),
                      "evidence_id": None})
    qty = facts.get("net_quantity", {}).get("value")
    unit = str(facts.get("quantity_unit", {}).get("value") or "").lower()
    try:
        q = float(qty) if qty is not None else None
    except (TypeError, ValueError):
        q = None
    kg = litre = None
    if q is not None:
        if unit in ("kg",):
            kg = q
        elif unit in ("g", "gm", "gram", "grams"):
            kg = q / 1000.0
        elif unit in ("l", "litre", "liter"):
            litre = q
        elif unit in ("ml",):
            litre = q / 1000.0
    facts["quantity_kg"] = {"value": kg, "confidence": None,
                            "origin": default_origin, "evidence_id": None}
    facts["quantity_litre"] = {"value": litre, "confidence": None,
                               "origin": default_origin, "evidence_id": None}
    # manufacturing_date may be a date object -> ISO string for DATE_VALID
    mfg = facts.get("mfg_month_year", {}).get("value")
    if hasattr(mfg, "isoformat"):
        facts["mfg_month_year"]["value"] = mfg.isoformat()  # type: ignore[index]
    bb = facts.get("best_before", {}).get("value")
    if hasattr(bb, "isoformat"):
        facts["best_before"]["value"] = bb.isoformat()  # type: ignore[index]
    return facts


def values_of(facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {k: v["value"] for k, v in facts.items()}


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if hasattr(value, "year"):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%Y", "%m-%Y", "%Y/%m",
                "%b %Y", "%B %Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_date(value: Any) -> datetime | None:
    return _dt(value)
