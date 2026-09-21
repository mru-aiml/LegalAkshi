"""Consumer product-verification reads + suggestion intake.

Two-sided architecture: officers inspect packages (photos -> OCR ->
review -> rule engine -> findings -> officer decision -> persisted
inspection). Consumers NEVER run OCR here; they look up products by
barcode / QR / product code / name against persisted inspections and
see a consumer-friendly verification status.

Consumers may also submit suggestions for LegalAkshi review
(consumer_suggestions table, own lifecycle). Consumers see ONLY their
own suggestions; status changes are officer-only (see officer routes).
Suggestions are never sent to any government department — the
confirmation wording states LegalAkshi review only.

Verification rule (existing officer decision lifecycle, no new tables):
  product identity -> inspection -> compliance_results -> violations ->
  officer inspector_status.

  VERIFIED     — compliance results exist, none NEEDS_REVIEW, and no
                 violation is PENDING / REQUIRES_REVIEW / CONFIRMED.
                 (FAIL findings whose violations the officer REJECTED
                 count as officer-cleared.)
  NEEDS_REVIEW — a result is NEEDS_REVIEW, or a violation is PENDING /
                 REQUIRES_REVIEW (officer has not decided yet).
  NOT_VERIFIED — no compliance results for the product ("No verified
                 LegalAkshi inspection was found for this product"),
                 or an officer CONFIRMED a violation. NOT VERIFIED never
                 means illegal or unsafe — it is stated explicitly.

Only consumer-safe fields leave this module: identity, pack size,
manufacturer, dates, declaration check outcomes, officer review note.
Never exposed: rule engine internals, OCR confidence/boxes/raw text,
rule UUIDs / version IDs, applicability IDs, audit rows, violation IDs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, get_principal
from app.repositories.base import Repo

router = APIRouter(tags=["consumer"])

VERIFIED = "VERIFIED"
NEEDS_REVIEW = "NEEDS_REVIEW"
NOT_VERIFIED = "NOT_VERIFIED"

_UNRESOLVED_VIOLATION = {"PENDING", "REQUIRES_REVIEW"}

# compliance check -> consumer declaration row (§8). Checks without a
# persisted result render "Not verified", never an assumed PASS.
_CHECK_LABELS = {
    "CHK-COMMON-NAME": "Product identity",
    "CHK-NET-QTY": "Net quantity",
    "CHK-MRP": "MRP",
    "CHK-MANUFACTURER": "Manufacturer",
    "CHK-MFG-DATE": "Date declaration",
    "CHK-BEST-BEFORE": "Date declaration",
    "CHK-CONSUMER-CARE": "Consumer-care details",
}

# declaration field names (persisted reviewed declarations) backing rows
# that have no dedicated compliance check.
_DECLARATION_LABELS = {
    "fssai_license": "FSSAI licence",
    "ingredients_raw": "Ingredients",
}

# persisted declaration field names surfaced as nutrition (only values
# that exist are returned; nothing is ever invented).
_NUTRIENT_FIELDS = (
    "serving_size", "energy", "calories", "protein", "carbohydrate",
    "carbohydrates", "total_sugars", "added_sugars", "sugars", "total_fat",
    "fat", "saturated_fat", "trans_fat", "sodium", "salt", "dietary_fiber",
    "fiber",
)

NUTRIENT_NOTES = {
    "serving_size": "The reference amount the numbers below describe.",
    "energy": "Calories the product provides; higher means more energy per serving.",
    "calories": "Calories the product provides; higher means more energy per serving.",
    "protein": "Builds and repairs body tissue; a higher value means more protein per serving.",
    "carbohydrate": "The body's main energy source; includes sugars and starch.",
    "carbohydrates": "The body's main energy source; includes sugars and starch.",
    "sugars": "Sweet carbohydrates; high values mean a sweeter product.",
    "total_sugars": "All sugars in the product, natural plus added.",
    "added_sugars": "Sugars added during manufacture; lower is generally preferable.",
    "fat": "Concentrated energy source; needed in small amounts.",
    "total_fat": "All fat in the product per serving.",
    "saturated_fat": "Fat linked to heart health when eaten in excess; compare across brands.",
    "trans_fat": "Best avoided; check whether the label declares zero.",
    "sodium": "A mineral in salt; high intake is linked to blood pressure concerns.",
    "salt": "Seasoning that contains sodium; high values deserve a second look.",
    "dietary_fiber": "Aids digestion; higher is generally better.",
    "fiber": "Aids digestion; higher is generally better.",
}


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _norm_code(value: str | None) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def _product_identity(product: dict) -> dict:
    qty = product.get("quantity")
    unit = product.get("quantity_unit") or ""
    pack = f"{qty} {unit}".strip() if qty not in (None, "") else None
    return {
        "product_id": product.get("product_id")
        or product.get("inspected_product_id"),
        "product_name": product.get("product_name"),
        "brand": product.get("brand"),
        "manufacturer": product.get("manufacturer"),
        "pack_size": pack,
        "barcode": product.get("barcode"),
    }


def _policy_weights(repo: Repo) -> dict | None:
    """Scoring weights, fetched once per request (not per product).

    None when the policy is unavailable — callers then treat every
    NEEDS_REVIEW as blocking (fail-safe).
    """
    try:
        policy = repo.scoring_policy("DEFAULT-2026") or {}
        return {w.get("check_id"): w.get("weight", 0)
                for w in policy.get("weights", [])}
    except Exception:
        return None


def _blocking_review_checks(results: list[dict],
                            weights: dict | None) -> list[dict]:
    """NEEDS_REVIEW results that actually gate consumer verification.

    Zero-weight informational checks (e.g. the CHK-OTHER-MATTERS
    catch-all) never resolve and must not hold a product in review
    forever; only checks carrying scoring weight gate VERIFIED. The
    weights come from the existing scoring policy — no new data, no
    engine change. If the policy is unavailable (weights None), every
    NEEDS_REVIEW blocks (fail-safe).
    """
    if weights is None:
        return [r for r in results if r.get("result") == "NEEDS_REVIEW"]
    try:
        return [r for r in results
                if r.get("result") == "NEEDS_REVIEW"
                and float(weights.get(r.get("check_id"), 0) or 0) != 0]
    except Exception:
        return [r for r in results if r.get("result") == "NEEDS_REVIEW"]


def _status_for(repo: Repo, inspection_id: str,
                product_id: str, weights: dict | None = None,
                results: list[dict] | None = None,
                violations: list[dict] | None = None) -> tuple[str, str]:
    """(status, reason) from persisted results + officer decisions.

    ``weights``/``results``/``violations`` are optional pre-fetched
    inputs so bulk callers (overview) pay a constant number of queries
    instead of N+1. Semantics are unchanged.
    """
    if weights is None:
        weights = _policy_weights(repo)
    if results is None:
        results = repo.results_for(inspection_id, product_id) or []
    else:
        results = [r for r in results
                   if r.get("product_id") == product_id]
    if violations is None:
        violations = [v for v in repo.violations_for(inspection_id)
                      if v.get("product_id") == product_id]
    else:
        violations = [v for v in violations
                      if v.get("product_id") == product_id]
    if not results:
        return (NOT_VERIFIED,
                "No verified LegalAkshi inspection was found for this "
                "product. This does not mean the product is illegal or "
                "unsafe — it means LegalAkshi has no completed inspection "
                "for it yet.")
    if _blocking_review_checks(results, weights):
        return (NEEDS_REVIEW,
                "The latest LegalAkshi inspection contains information "
                "that still requires review.")
    if any(v.get("inspector_status") in _UNRESOLVED_VIOLATION
           for v in violations):
        return (NEEDS_REVIEW,
                "The latest LegalAkshi inspection contains information "
                "that still requires review.")
    if any(v.get("inspector_status") == "CONFIRMED" for v in violations):
        return (NOT_VERIFIED,
                "No verified LegalAkshi inspection was found for this "
                "product. This does not mean the product is illegal or "
                "unsafe.")
    return (VERIFIED,
            "This product has a completed LegalAkshi inspection reviewed "
            "through the officer verification workflow.")


def _declarations_checked(repo: Repo, inspection_id: str,
                          product_id: str) -> list[dict]:
    """Consumer declaration rows from persisted evidence only."""
    by_check: dict[str, str] = {}
    for r in repo.results_for(inspection_id, product_id) or []:
        check = r.get("check_id") or ""
        if check in _CHECK_LABELS:
            # NEEDS_REVIEW dominates PASS; FAIL is officer-routed above.
            prev = by_check.get(check)
            if r.get("result") == "NEEDS_REVIEW" or prev is None:
                by_check[check] = r.get("result") or ""
            elif prev != "NEEDS_REVIEW" and r.get("result") == "PASS":
                by_check[check] = "PASS"
    declared = {d.get("field_name") for d in
                repo.declarations_for(product_id) or []
                if d.get("extracted_value") not in (None, "")}
    rows: list[dict] = []
    seen_labels: set[str] = set()
    for check, label in _CHECK_LABELS.items():
        if label in seen_labels:
            continue
        seen_labels.add(label)
        # Date declaration merges MFG + BEST_BEFORE: review dominates.
        relevant = [by_check[c] for c, lab in _CHECK_LABELS.items()
                    if lab == label and c in by_check]
        if not relevant:
            rows.append({"label": label, "state": "Not verified"})
        elif any(v == "NEEDS_REVIEW" for v in relevant):
            rows.append({"label": label, "state": "Needs review"})
        elif all(v == "PASS" for v in relevant):
            rows.append({"label": label, "state": "Verified"})
        else:
            rows.append({"label": label, "state": "Not verified"})
    for field, label in _DECLARATION_LABELS.items():
        rows.append({"label": label,
                     "state": ("Verified" if field in declared
                               else "Not verified")})
    return rows


def _review_note(repo: Repo, inspection_id: str,
                 product_id: str) -> dict | None:
    """Officer-review evidence (decided violations only, no internals)."""
    decided = [v for v in repo.violations_for(inspection_id)
               if v.get("product_id") == product_id
               and v.get("inspector_status") not in (None, "", "PENDING")]
    if not decided:
        return None
    decided.sort(key=lambda v: v.get("verification_date") or "", reverse=True)
    latest = decided[0]
    action = ("cleared" if latest.get("inspector_status") == "REJECTED"
              else "reviewed")
    return {
        "reviewed": True,
        "action": action,
        "reviewed_by": latest.get("inspector_id") or "Officer",
        "reviewed_on": latest.get("verification_date"),
        "note": (f"Reviewed by an officer ({action})." if not latest.get(
            "verification_date") else
            f"Reviewed by an officer ({action}) on "
            f"{latest.get('verification_date')}."),
    }


def _match_products(repo: Repo, barcode: str | None,
                    product_code: str | None,
                    product_name: str | None) -> list[tuple[dict, dict]]:
    """All (product, inspection) pairs matching the lookup, newest first."""
    want_barcode = _norm_code(barcode or product_code or "")
    want_name = (product_name or "").strip().lower()
    out: list[tuple[dict, dict]] = []
    for insp in repo.list_inspections():
        for p in repo.products_for(insp.get("inspection_id", "")):
            if want_barcode:
                codes = {_norm_code(str(p.get(k) or ""))
                         for k in ("barcode", "qr_code",
                                   "product_identifier")}
                if want_barcode not in codes - {""}:
                    continue
            elif want_name:
                hay = f"{p.get('product_name') or ''} {p.get('brand') or ''}" \
                    .lower()
                if want_name not in hay:
                    continue
            else:
                continue
            prod = repo.get_product(p.get("inspected_product_id")
                                    or p.get("product_id") or "") or p
            if not prod.get("product_id"):
                prod = {**prod, "product_id": p.get("inspected_product_id")
                        or p.get("product_id")}
            out.append((prod, insp))
    out.sort(key=lambda pair: pair[1].get("inspection_date", ""),
             reverse=True)
    return out[:10]


def _summary(repo: Repo, product: dict, inspection: dict,
             weights: dict | None = None) -> dict:
    pid = product.get("product_id") or product.get("inspected_product_id")
    iid = inspection.get("inspection_id")
    status, reason = _status_for(repo, iid, pid, weights=weights)
    identity = _product_identity(product)
    return {
        **identity,
        "status": status,
        "reason": reason,
        "inspection_date": inspection.get("inspection_date"),
        "inspection_type": inspection.get("inspection_type") or "PHYSICAL",
        "inspection_id": iid,
        "manufacturer": product.get("manufacturer"),
    }


@router.get("/consumer/products/lookup")
def lookup_products(request: Request, barcode: str | None = None,
                    product_code: str | None = None,
                    product_name: str | None = None,
                    principal: Principal = Depends(get_principal)):
    """Match persisted inspected products; each carries its verification."""
    _ = principal  # any caller may look up; results are verification facts
    if not (barcode or product_code or product_name):
        raise HTTPException(
            status_code=422,
            detail="at least one of barcode, product_code, product_name "
                   "is required")
    repo = _repo(request)
    matches = _match_products(repo, barcode, product_code, product_name)
    weights = _policy_weights(repo)  # once per request, not per match
    return {"matches": [_summary(repo, p, i, weights=weights)
                        for p, i in matches],
            "count": len(matches)}


@router.get("/consumer/products/{product_id}/verification")
def product_verification(product_id: str, request: Request,
                         principal: Principal = Depends(get_principal)):
    """Consumer-friendly verification detail for one inspected product."""
    _ = principal
    repo = _repo(request)
    product = repo.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="product not found")
    inspection = repo.get_inspection(product.get("inspection_id", ""))
    if not inspection:
        raise HTTPException(status_code=404, detail="inspection not found")
    pid = product.get("product_id") or product_id
    iid = inspection.get("inspection_id")
    status, reason = _status_for(repo, iid, pid)
    identity = _product_identity(product)
    batch_lot = None
    for d in repo.declarations_for(pid) or []:
        if str(d.get("field_name") or "") in ("batch_lot", "batch",
                                              "lot_no", "lot"):
            if d.get("extracted_value") not in (None, ""):
                batch_lot = str(d.get("extracted_value"))
                break
    return {
        **identity,
        "batch_lot": batch_lot,
        "status": status,
        "reason": reason,
        "not_verified_note": (
            "NOT VERIFIED does not mean illegal or unsafe — it means no "
            "verified LegalAkshi inspection was found, or a labelling "
            "issue was confirmed and is being handled.") if status
        == NOT_VERIFIED else None,
        "inspection_date": inspection.get("inspection_date"),
        "inspection_type": inspection.get("inspection_type") or "PHYSICAL",
        "manufacturer": product.get("manufacturer"),
        "pack_size": identity["pack_size"],
        "declarations": _declarations_checked(repo, iid, pid),
        "review": _review_note(repo, iid, pid),
    }


@router.get("/consumer/products/{product_id}/nutrition")
def product_nutrition(product_id: str, request: Request,
                      principal: Principal = Depends(get_principal)):
    """Persisted nutrition values only; never invented."""
    _ = principal
    repo = _repo(request)
    product = repo.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="product not found")
    pid = product.get("product_id") or product_id
    found: dict[str, str] = {}
    for d in repo.declarations_for(pid) or []:
        field = str(d.get("field_name") or "")
        value = d.get("extracted_value")
        if field in _NUTRIENT_FIELDS and value not in (None, ""):
            found.setdefault(field, str(value))
    nutrients = [{"name": name.replace("_", " ").title(),
                  "value": value,
                  "about": NUTRIENT_NOTES.get(name, "")}
                 for name, value in found.items()]
    return {
        "product_id": pid,
        "product_name": product.get("product_name"),
        "available": bool(nutrients),
        "nutrients": nutrients,
        "note": (None if nutrients else
                 "Nutrition information has not been verified for this "
                 "product yet."),
    }


def _report_for_complaint(repo: Repo, complaint: dict,
                            prod_index: dict[str, tuple[dict, dict]],
                            res_by_key: dict, viol_by_key: dict,
                            weights: dict | None) -> dict:
    """One consumer report: own complaint -> product -> related inspection.

    Never exposes unrelated inspections. A complaint with no matching
    inspection yet renders "Submitted — awaiting review", never
    "Not analyzed".
    """
    match = prod_index.get(
        (complaint.get("product_name") or "").strip().lower())
    related = None
    if match is not None:
        prod, insp = match
        iid = insp.get("inspection_id", "")
        fpid = prod.get("product_id") or prod.get("inspected_product_id")
        results = res_by_key.get((iid, fpid), [])
        violations = viol_by_key.get((iid, fpid), [])
        status, _ = _status_for(repo, iid, fpid, weights=weights,
                                results=results, violations=violations)
        outcome = {"pass": sum(1 for r in results
                               if r.get("result") == "PASS"),
                   "fail": sum(1 for r in results
                               if r.get("result") == "FAIL"),
                   "review": sum(1 for r in results
                                 if r.get("result") == "NEEDS_REVIEW")}
        related = {
            "inspection_id": iid,
            "inspection_date": insp.get("inspection_date"),
            "inspection_type": insp.get("inspection_type") or "PHYSICAL",
            "status": status,
            "report_available": bool(results),
            "outcome": outcome,
            "product": _product_identity({**prod, "product_id": fpid}),
        }
    return {
        "complaint_id": complaint.get("complaint_id"),
        "product_name": complaint.get("product_name"),
        "barcode": None,  # complaints carry product_name; no barcode column
        "date_submitted": complaint.get("created_at"),
        "last_update": complaint.get("updated_at"),
        "status": complaint.get("status"),
        "description": complaint.get("description"),
        "related_inspection": related,
        "state_note": ("Submitted — awaiting review." if related is None
                       else None),
    }


def _consumer_report_bundle(repo: Repo, who: str):
    """Shared batch load for the report endpoints (constant queries)."""
    weights = _policy_weights(repo)
    complaints = repo.list_complaints(who)
    inspections = repo.list_inspections()
    iids = [i.get("inspection_id", "") for i in inspections]
    pmap = repo.products_for_many(iids)
    res_by_key: dict[tuple[str, str], list[dict]] = {}
    for r in repo.results_for_many(iids):
        res_by_key.setdefault(
            (r.get("inspection_id", ""), r.get("product_id", "")),
            []).append(r)
    viol_by_key: dict[tuple[str, str], list[dict]] = {}
    for v in repo.violations_for_many(iids):
        viol_by_key.setdefault(
            (v.get("inspection_id", ""), v.get("product_id", "")),
            []).append(v)
    # Latest inspection per normalized product name.
    prod_index: dict[str, tuple[dict, dict]] = {}
    for insp in inspections:
        for p in pmap.get(insp.get("inspection_id", ""), []):
            key = (p.get("product_name") or "").strip().lower()
            if not key:
                continue
            prev = prod_index.get(key)
            if prev is None or (insp.get("inspection_date", "")
                                >= prev[1].get("inspection_date", "")):
                prod_index[key] = (p, insp)
    return weights, complaints, res_by_key, viol_by_key, prod_index


@router.get("/consumer/reports")
def list_consumer_reports(request: Request,
                          principal: Principal = Depends(get_principal)):
    """The caller's own complaint-derived reports (never all inspections)."""
    repo = _repo(request)
    who = principal.user_id or "anonymous-consumer"
    weights, complaints, res_by_key, viol_by_key, prod_index = \
        _consumer_report_bundle(repo, who)
    return {"reports": [_report_for_complaint(
        repo, c, prod_index, res_by_key, viol_by_key, weights)
        for c in complaints],
        "count": len(complaints)}


@router.get("/consumer/reports/{complaint_id}")
def get_consumer_report(complaint_id: str, request: Request,
                        principal: Principal = Depends(get_principal)):
    """REPORT DETAILS for one own complaint (+timeline, +related data)."""
    repo = _repo(request)
    try:
        import uuid as _uuid
        _uuid.UUID(complaint_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid complaint_id")
    row = repo.get_complaint(complaint_id)
    if not row:
        raise HTTPException(status_code=404, detail="report not found")
    who = principal.user_id or "anonymous-consumer"
    if row.get("reporter_id") != who:
        raise HTTPException(status_code=403, detail="not your report")
    weights, _all, res_by_key, viol_by_key, prod_index = \
        _consumer_report_bundle(repo, who)
    report = _report_for_complaint(repo, row, prod_index, res_by_key,
                                   viol_by_key, weights)
    declarations = []
    related = report.get("related_inspection")
    if related is not None:
        declarations = [
            {"label": d["label"], "state": d["state"]}
            for d in _declarations_checked(
                repo, related["inspection_id"],
                related["product"]["product_id"])]
    return {**report,
            "timeline": repo.complaint_timeline(complaint_id),
            "declarations": declarations}


def _public_suggestion(row: dict) -> dict:
    """Consumer-safe suggestion shape (own rows only, no internals)."""
    return {
        "suggestion_id": row.get("suggestion_id"),
        "title": row.get("title"),
        "category": row.get("category"),
        "description": row.get("description"),
        "context": row.get("context"),
        "location": row.get("location"),
        "status": row.get("status"),
        "officer_note": row.get("officer_note"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _suggestions_unavailable(exc: Exception) -> HTTPException:
    """503 with an actionable hint (never a bare 500).

    A missing/unmigrated consumer_suggestions table is a deployment
    issue, not a client error: name the migration and keep the original
    detail for operators. Run scripts/verify_suggestions_schema.py to
    confirm against the configured database.
    """
    return HTTPException(
        status_code=503,
        detail="suggestions store unavailable "
               "(migration 004_consumer_suggestions.sql may not be applied; "
               f"see backend/scripts/verify_suggestions_schema.py): {exc}")


@router.post("/consumer/suggestions", status_code=201)
def create_suggestion(body: dict, request: Request,
                      principal: Principal = Depends(get_principal)):
    """Submit a suggestion for LegalAkshi review.

    Frontend-only demo mode never calls this; nothing here claims
    government delivery — confirmation wording states LegalAkshi
    review only.
    """
    repo = _repo(request)
    title = str(body.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=422, detail="title is required")
    from app.models.lifecycle import SUGGESTION_CATEGORIES

    category = str(body.get("category", "Other")).strip() or "Other"
    if category not in SUGGESTION_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of "
                   f"{sorted(SUGGESTION_CATEGORIES)}")
    try:
        saved = repo.create_suggestion({
            "consumer_user_id": principal.user_id or "anonymous-consumer",
            "title": title, "category": category,
            "description": str(body.get("description", "")),
            "context": str(body.get("context", "")),
            "location": str(body.get("location", ""))})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)
    return _public_suggestion(saved)


@router.get("/consumer/suggestions")
def list_suggestions(request: Request,
                     principal: Principal = Depends(get_principal)):
    """The caller's own suggestions only (single bounded query)."""
    repo = _repo(request)
    who = principal.user_id or "anonymous-consumer"
    try:
        return [_public_suggestion(r) for r in repo.list_suggestions(who)]
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)


@router.get("/consumer/suggestions/{suggestion_id}")
def get_suggestion(suggestion_id: str, request: Request,
                   principal: Principal = Depends(get_principal)):
    repo = _repo(request)
    try:
        import uuid as _uuid
        _uuid.UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid suggestion_id")
    try:
        row = repo.get_suggestion(suggestion_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)
    if not row:
        raise HTTPException(status_code=404, detail="suggestion not found")
    who = principal.user_id or "anonymous-consumer"
    if row.get("consumer_user_id") != who:
        raise HTTPException(status_code=403,
                            detail="not your suggestion")
    try:
        timeline = repo.suggestion_timeline(suggestion_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)
    return {**_public_suggestion(row), "timeline": timeline}


@router.get("/consumer/overview")
def consumer_overview(request: Request,
                      principal: Principal = Depends(get_principal)):
    """Real backend counts for the consumer dashboard (no fabrication).

    Performance: a constant number of queries per request regardless of
    data size — inspections + batched products/results/violations +
    policy + own complaints. The batched rows carry the identity columns
    the overview needs, so no per-product get_product is required.
    Returned meaning is unchanged.
    """
    repo = _repo(request)
    weights = _policy_weights(repo)
    inspections = repo.list_inspections()
    iids = [i.get("inspection_id", "") for i in inspections]
    pmap = repo.products_for_many(iids)
    all_results = repo.results_for_many(iids)
    all_violations = repo.violations_for_many(iids)
    res_by_key: dict[tuple[str, str], list[dict]] = {}
    for r in all_results:
        res_by_key.setdefault(
            (r.get("inspection_id", ""), r.get("product_id", "")),
            []).append(r)
    viol_by_key: dict[tuple[str, str], list[dict]] = {}
    for v in all_violations:
        viol_by_key.setdefault(
            (v.get("inspection_id", ""), v.get("product_id", "")),
            []).append(v)
    # Latest product row per identity (barcode wins, else name+brand).
    latest: dict[str, tuple[dict, dict]] = {}
    for insp in inspections:
        iid = insp.get("inspection_id", "")
        for p in pmap.get(iid, []):
            key = _norm_code(str(p.get("barcode") or "")) or \
                f"{(p.get('product_name') or '').strip().lower()}|" \
                f"{(p.get('brand') or '').strip().lower()}"
            prev = latest.get(key)
            if prev is None or (insp.get("inspection_date", "")
                                >= prev[1].get("inspection_date", "")):
                latest[key] = (p, insp)
    verified = with_results = 0
    recent: list[dict] = []
    for prod, insp in latest.values():
        iid = insp.get("inspection_id", "")
        fpid = prod.get("product_id") or prod.get("inspected_product_id")
        results = res_by_key.get((iid, fpid), [])
        violations = viol_by_key.get((iid, fpid), [])
        status, _ = _status_for(repo, iid, fpid, weights=weights,
                                results=results, violations=violations)
        if results:
            with_results += 1
        if status == VERIFIED:
            verified += 1
        recent.append({**_product_identity({**prod, "product_id": fpid}),
                       "status": status,
                       "inspection_date": insp.get("inspection_date")})
    recent.sort(key=lambda r: r.get("inspection_date") or "", reverse=True)
    mine = (repo.list_complaints(principal.user_id)
            if principal.user_id else [])
    return {
        "products_checked": with_results,
        "verified_products": verified,
        "complaints_raised": len(mine),
        "open_complaints": len([c for c in mine
                                if c.get("status") not in ("CLOSED",
                                                           "RESOLVED")]),
        "recent_checks": recent[:5],
    }
