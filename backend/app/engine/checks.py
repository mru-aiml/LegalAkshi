"""Generic check handlers — dispatched by engine_check_registry.check_type.

No legal knowledge lives here: the field under test comes from
registry.field_name. The authoritative registry has no per-check params
column, so handlers degrade honestly when a check type needs configuration
it was not given: NEEDS_REVIEW, never an invented PASS/FAIL.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.engine.facts import parse_date

PASS, FAIL, NA, REVIEW = "PASS", "FAIL", "NOT_APPLICABLE", "NEEDS_REVIEW"

STANDARD_UNITS = {"g", "kg", "mg", "ml", "l", "litre", "liter", "cm", "m",
                  "mm", "pcs", "pc", "nos", "number"}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip() not in ("", "NOT_DETECTED")
    return True


def _num(value: Any) -> float | None:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def h_field_present(values, field, params, **_) -> tuple:
    v = values.get(field)
    if _present(v):
        return PASS, str(v), "declared."
    return FAIL, "" if v is None else str(v), "missing."


def h_field_absent(values, field, params, **_) -> tuple:
    v = values.get(field)
    if not _present(v):
        return PASS, "", "correctly absent."
    return FAIL, str(v), "must be absent."


def h_text_match(values, field, params, **_) -> tuple:
    expected = params.get("value", params.get("expected"))
    if expected is None:
        return REVIEW, str(values.get(field) or ""), \
            "no expected text configured; needs review."
    return h_field_present(values, field, params) \
        if str(values.get(field)).strip() == str(expected).strip() else \
        (FAIL, str(values.get(field)), "does not match expected text.")


def h_text_contains(values, field, params, **_) -> tuple:
    needle = params.get("value", params.get("contains"))
    if needle is None:
        return REVIEW, str(values.get(field) or ""), \
            "no required substring configured; needs review."
    v = values.get(field)
    if not _present(v):
        return FAIL, "", "missing."
    if str(needle).lower() in str(v).lower():
        return PASS, str(v), "contains required text."
    return FAIL, str(v), f"missing required text '{needle}'."


def h_regex_match(values, field, params, **_) -> tuple:
    pattern = params.get("pattern", params.get("format_pattern", ""))
    if params.get("format") == "fssai14" and not pattern:
        pattern = r"^\d{14}$"
    v = values.get(field)
    if not pattern:
        return REVIEW, str(v or ""), "no pattern configured; needs review."
    if not _present(v):
        return FAIL, "", "missing."
    if re.match(pattern, str(v).strip()):
        return PASS, str(v), "matches required format."
    return FAIL, str(v), "does not match required format."


def h_numeric_compare(values, field, params, **_) -> tuple:
    target, op = params.get("value"), params.get("op", "gt")
    v = _num(values.get(field))
    if v is None:
        return FAIL, ("" if values.get(field) is None else str(values.get(field))), \
            "missing or not numeric."
    if target is None:  # registry carries no comparison target -> presence check
        return PASS, str(v), "declared numeric value."
    try:
        t = float(target)
    except (TypeError, ValueError):
        return REVIEW, str(v), "numeric target misconfigured; needs review."
    ok = {"gt": v > t, "gte": v >= t, "lt": v < t, "lte": v <= t,
          "eq": v == t, "neq": v != t}.get(op)
    if ok is None:
        return REVIEW, str(v), f"unknown numeric op '{op}'; needs review."
    if ok:
        return PASS, str(v), f"{v} satisfies {op} {t}."
    return FAIL, str(v), f"{v} does not satisfy {op} {t}."


def h_unit_normalization(values, field, params, **_) -> tuple:
    v = _num(values.get(field))
    unit_field = params.get("unit_field", "quantity_unit")
    unit = str(values.get(unit_field) or values.get("net_quantity_unit") or ""
               ).strip().lower()
    allowed = {u.lower() for u in params.get("allowed_units", [])} or STANDARD_UNITS
    if v is None:
        return FAIL, "", "quantity missing."
    if not unit:
        return FAIL, str(v), "quantity present but unit missing."
    if unit not in allowed:
        return FAIL, f"{v} {unit}", f"unit '{unit}' is not a standard unit."
    return PASS, f"{v} {unit}", f"standard unit '{unit}'."


def h_date_valid(values, field, params, **_) -> tuple:
    v = values.get(field)
    if not _present(v):
        return FAIL, "", "date missing."
    if parse_date(v) is None:
        return FAIL, str(v), f"date '{v}' not parseable."
    return PASS, str(v), "date valid."


def _required_when(values, field, params) -> bool | None:
    from app.engine.conditions import evaluate

    when = params.get("when")
    if when is None:  # registry default for perishable-date checks
        if field in ("best_before", "use_by"):
            when = {"field": "may_become_unfit", "eq": True}
        else:
            return None
    return evaluate(when, values)


def h_date_required_if(values, field, params, **_) -> tuple:
    verdict = _required_when(values, field, params)
    if verdict is not True:
        if verdict is None:
            return REVIEW, str(values.get(field) or ""), \
                "applicability uncertain; needs review."
        return NA, "", "not required for this product."
    return h_date_valid(values, field, params)


def h_conditional_field_present(values, field, params, **_) -> tuple:
    from app.engine.conditions import evaluate

    when = params.get("when")
    if when is not None:
        verdict = evaluate(when, values)
        if verdict is not True:
            if verdict is None:
                return REVIEW, str(values.get(field) or ""), \
                    "applicability uncertain; needs review."
            return NA, "", "not required for this product."
    # no `when`: the applicability engine already gated this check
    return h_field_present(values, field, params)


def _qr_payload(values) -> Any:
    for candidate in ("qr_data", "qr_payload", "qr_code", "barcode"):
        if _present(values.get(candidate)) and candidate != "qr_code":
            return values.get(candidate)
    if _present(values.get("qr_code")):
        return values.get("qr_code")
    return None


def h_qr_data_present(values, field, params, **_) -> tuple:
    payload = _qr_payload(values)
    if payload is not None:
        return PASS, str(payload), "machine-readable data present."
    return REVIEW, "", "no QR/barcode payload in evidence; needs review."


def h_qr_or_on_package(values, field, params, **_) -> tuple:
    if _present(values.get(field)):
        return PASS, str(values.get(field)), "declared on package."
    payload = _qr_payload(values)
    if payload is not None and values.get("package_qr_notice_present") is True:
        return PASS, str(payload), "conveyed via compliant QR code."
    if payload is not None:
        return REVIEW, str(payload), \
            "QR payload present but package QR notice unconfirmed; needs review."
    return FAIL, "", "missing on package and via QR."


def h_cross_field_compare(values, field, params, **_) -> tuple:
    other, op = params.get("other_field", ""), params.get("op", "eq")
    if not other:
        return REVIEW, str(values.get(field) or ""), \
            "cross-field target misconfigured; needs review."
    a, b = values.get(field), values.get(other)
    if not _present(a) or not _present(b):
        return REVIEW, str(a or ""), f"cannot compare with {other}; value missing."
    try:
        ok = {"eq": str(a) == str(b), "neq": str(a) != str(b),
              "gt": float(a) > float(b), "gte": float(a) >= float(b),  # type: ignore[arg-type]
              "lt": float(a) < float(b), "lte": float(a) <= float(b)}.get(op)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return REVIEW, str(a), "values not comparable; needs review."
    if ok is None:
        return REVIEW, str(a), f"unknown compare op '{op}'."
    if ok:
        return PASS, str(a), f"consistent with {other}."
    return FAIL, str(a), f"inconsistent with {other}."


def h_platform_filter_present(values, field, params, **_) -> tuple:
    # E-commerce declarations (Rule 6(10)/6(10A)) concern the ONLINE listing,
    # not the physical package. A package photograph can never establish
    # whether the seller's online listing contains or lacks declarations,
    # so absence of online evidence must NEVER be reported as FAIL.
    if not values.get("ecommerce"):
        return NA, "", "not an e-commerce listing."
    ctx = str(values.get("inspection_context") or "").strip().upper()
    if ctx == "PACKAGE_ONLY":
        return REVIEW, str(values.get(field) or ""), \
            ("no e-commerce listing evidence was provided (physical package "
             "inspection only). Physical package images cannot establish "
             "online listing compliance; not checked.")
    v = values.get(field)
    if _present(v) and v is not False:
        return PASS, str(v), "platform declaration present."
    if not _online_listing_evidence(values, field):
        return REVIEW, "" if v is None else str(v), \
            ("no e-commerce listing evidence was provided (no listing URL, "
             "listing screenshot, or online-listing context). Physical "
             "package images cannot establish online listing compliance; "
             "not checked.")
    return FAIL, "" if v is None else str(v), \
        "required e-commerce declaration missing."


def _online_listing_evidence(values, field: str) -> bool:
    """True when the officer supplied online-listing context.

    Accepts (existing infrastructure, no schema change): source_listing_url
    (authoritative column), online_listing_url / listing_url /
    online_listing_evidence declaration extras, or an explicit
    inspection_context of ONLINE_LISTING / PACKAGE_AND_ONLINE_LISTING.
    A present declaration value itself is evidence for its own check.
    """
    if _present(values.get(field)) and values.get(field) is not False:
        return True
    for key in ("source_listing_url", "online_listing_url", "listing_url",
                "online_listing_evidence", "ecommerce_evidence",
                "listing_screenshot", "online_evidence",
                # facts.py maps the authoritative source_listing_url column
                # onto ecommerce_declarations, so a stored listing URL
                # surfaces under that field name.
                "ecommerce_declarations"):
        if key == field:
            continue  # own value handled by the caller via _present()
        candidate = values.get(key)
        if _present(candidate) and candidate is not False:
            return True
    ctx = str(values.get("inspection_context") or "").strip().upper()
    if ctx in ("ONLINE_LISTING", "PACKAGE_AND_ONLINE_LISTING",
               "ONLINE", "PACKAGE_AND_ONLINE"):
        return True
    return False


def h_manual_review(values, field, params, **_) -> tuple:
    return REVIEW, str(values.get(field) or ""), \
        "check requires human review (no automatable configuration)."


def h_ingredient_screen(values, field, params, **_) -> tuple:
    """Advisory ingredient screening — registry-driven, never a prohibition
    by default. An explicit configured outcome (params.decision_map entry
    with status PROHIBITED/RESTRICTED) is required before any restrictive
    conclusion; otherwise NEEDS_REVIEW / UNKNOWN-style review routing."""
    v = values.get(field)
    if not _present(v):
        return REVIEW, str(v or ""), \
            "no ingredient list supplied; nothing screened."
    decision_map = params.get("decision_map") or {}
    text = str(v).lower()
    for needle, outcome in decision_map.items():
        if str(needle).lower() in text and isinstance(outcome, dict):
            status = str(outcome.get("status", "")).upper()
            if status in ("PROHIBITED", "RESTRICTED", "CONDITIONAL"):
                return (FAIL if status == "PROHIBITED" else REVIEW), str(v), \
                    f"{outcome.get('reason', 'configured ingredient rule.')}"
    return REVIEW, str(v), \
        ("ingredient list screened against the configured registry; no "
         "configured prohibition matched — inspector review decides. "
         "Unknown ingredients are not offences.")


CHECK_HANDLERS = {
    "FIELD_PRESENT": h_field_present,
    "FIELD_ABSENT": h_field_absent,
    "TEXT_MATCH": h_text_match,
    "TEXT_CONTAINS": h_text_contains,
    "REGEX_MATCH": h_regex_match,
    "NUMERIC_COMPARE": h_numeric_compare,
    "UNIT_NORMALIZATION": h_unit_normalization,
    "DATE_VALID": h_date_valid,
    "DATE_REQUIRED_IF": h_date_required_if,
    "CONDITIONAL_FIELD_PRESENT": h_conditional_field_present,
    "QR_DATA_PRESENT": h_qr_data_present,
    "QR_OR_ON_PACKAGE": h_qr_or_on_package,
    "FORMAT_VALID": h_regex_match,
    "CROSS_FIELD_COMPARE": h_cross_field_compare,
    "PLATFORM_FILTER_PRESENT": h_platform_filter_present,
    "MANUAL_REVIEW": h_manual_review,
    "INGREDIENT_SCREEN": h_ingredient_screen,
}


def run_check(check: dict, values: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one registry check against product values."""
    handler = CHECK_HANDLERS.get(check.get("check_type", ""))
    field = check.get("field_name", "")
    params = check.get("params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except ValueError:
            params = {}
    if handler is None:
        return {"result": REVIEW, "detected": str(values.get(field) or ""),
                "explanation": f"unknown check type '{check.get('check_type')}';"
                               " needs review."}
    result, detected, note = handler(values, field, params)
    return {"result": result, "detected": detected, "explanation": note}
