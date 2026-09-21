"""Analysis field requirements, derived from the Rule Engine itself.

Reads the active rule versions + checks + applicability rows and
evaluates applicability with the engine's own ``decide()`` against the
inspection context — no duplicated legal logic, no second rule system.
Read-only: never modifies engine, scoring, or authoritative data.

Output feeds the Scan & Inspect readiness gate: which OCR/reviewed
fields the applicable checks actually need, so the officer reviews
once instead of discovering gaps after clicking Analyze.
"""
from __future__ import annotations

from typing import Any

# Registry fact field -> OCR FIELD_KEYS key (fields.py) used for review.
REGISTRY_TO_OCR: dict[str, str | None] = {
    "common_generic_name": "product_name",
    "net_quantity": "quantity",
    "mfg_month_year": "manufacturing_date",
    "best_before": "best_before",
    "mrp": "mrp",
    "manufacturer": "manufacturer",
    "consumer_care": "consumer_care",
    "country_of_origin": "country_of_origin",
    "unit_sale_price": "unit_sale_price",
    "dimensions": None,  # no OCR/review field collects this
    "other_declarations": None,  # catch-all, no single field
    "gm_label": None,  # GMO context only; no OCR field
    "veg_nonveg_dot": None,  # image-analysis symbol, not a form field
    "ecommerce_declarations": None,  # listing evidence, not OCR
    "ecommerce_coo_filter": None,  # listing evidence, not OCR
    "qr_code": None,  # presence flag, not OCR text
}

# Registry fact field -> Scan & Inspect form key for usability checks.
REGISTRY_TO_FORM: dict[str, str | None] = {
    "common_generic_name": "product_name",
    "net_quantity": "quantity",
    "mfg_month_year": "manufacturing_date",
    "best_before": "best_before",
    "mrp": "mrp",
    "manufacturer": "manufacturer",
    "consumer_care": "consumer_care",
    "country_of_origin": "country_of_origin",
    "unit_sale_price": "unit_sale_price",
    "dimensions": None,
    "other_declarations": None,
    "gm_label": None,
    "veg_nonveg_dot": None,
    "ecommerce_declarations": None,
    "ecommerce_coo_filter": None,
    "qr_code": None,
}

# Context flags the applicability conditions read. Absent keys evaluate
# to unknown -> NEEDS_REVIEW applicability -> treated as required
# (conservative: the officer resolves it, never silently skipped).
CONTEXT_FIELDS = (
    "food", "imported", "ecommerce", "category", "subcategory",
    "quantity_type", "combination_package", "group_package",
    "electronic_product", "is_prepackaged", "genetically_modified",
    "may_become_unfit", "cosmetic",
)


def get_analysis_field_requirements(
        repo: Any,
        context: dict[str, Any] | None = None,
        as_of: str | None = None) -> dict[str, Any]:
    """Required/optional review fields for an inspection context.

    ``context`` carries product flags (food, imported, ecommerce,
    category, quantity_type, ...). Every check of every active version
    is run through the engine's own applicability ``decide()``; a field
    is required when at least one applicable (or
    applicability-unresolved) check needs it. Returns entries with the
    backing checks and reasons — presentation only, no legal content
    invented here.
    """
    from app.engine import applicability as app_mod

    context = dict(context or {})
    as_of = as_of or "2026-09-15"
    values = {k: context.get(k) for k in CONTEXT_FIELDS}
    required: dict[str, dict[str, Any]] = {}
    optional: dict[str, dict[str, Any]] = {}
    versions = repo.active_rule_versions(str(as_of))

    def _note(store: dict[str, dict[str, Any]], field: str,
              check_id: str, title: str, requirement: str,
              reason: str) -> None:
        entry = store.setdefault(field, {"field": field,
                                         "ocr_key": REGISTRY_TO_OCR.get(
                                             field),
                                         "form_key": REGISTRY_TO_FORM.get(
                                             field),
                                         "checks": [], "reasons": []})
        entry["checks"].append({"check_id": check_id, "title": title,
                               "requirement": requirement})
        if reason and reason not in entry["reasons"]:
            entry["reasons"].append(reason)

    for ver in versions:
        try:
            appl_rows = repo.applicability_for(ver["rule_version_id"])
            checks = repo.checks_for(ver["rule_version_id"])
        except Exception:
            continue
        for chk in checks:
            field = chk.get("field_name", "")
            if not field:
                continue
            try:
                decision = app_mod.decide(
                    ver["rule_version_id"], appl_rows, values,
                    bool(chk.get("mandatory_default", True)))
            except Exception:
                decision = {"applicable": None, "status": "NEEDS_REVIEW",
                            "reason": "applicability evaluation failed"}
            title = chk.get("title", "") or chk.get("check_id", "")
            requirement = (ver.get("requirement", "")
                           or chk.get("description", ""))
            mandatory = bool(chk.get("mandatory_default", True))
            if decision.get("applicable"):
                _note(required, field, chk.get("check_id", ""), title,
                      requirement,
                      f"{decision.get('status')}: "
                      f"{decision.get('reason', '')}")
            elif decision.get("status") == "NEEDS_REVIEW" and mandatory:
                # Applicability itself unresolved for a mandatory check:
                # conservative — the officer resolves it, never skipped.
                _note(required, field, chk.get("check_id", ""), title,
                      requirement,
                      "applicability unresolved — officer review: "
                      f"{decision.get('reason', '')}")
            elif decision.get("status") == "NEEDS_REVIEW":
                _note(optional, field, chk.get("check_id", ""), title,
                      requirement,
                      "applicability unresolved (non-mandatory) — "
                      f"review if relevant: {decision.get('reason', '')}")
            else:
                _note(optional, field, chk.get("check_id", ""), title,
                      requirement,
                      f"not applicable here: "
                      f"{decision.get('reason', '')}")
    # A field required anywhere wins over optional elsewhere.
    for field in list(optional):
        if field in required:
            del optional[field]
    return {"required": [required[k] for k in sorted(required)],
            "optional": [optional[k] for k in sorted(optional)],
            "context": {k: values[k] for k in CONTEXT_FIELDS
                        if values[k] is not None},
            "as_of": str(as_of),
            "versions": len(versions)}
