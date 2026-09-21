"""Officer learning endpoints (Stage 2, Parts L/M/N). Officer/admin only.

GET /api/v1/officer/learning/error-patterns — deterministic failure
    classification over VERIFIED corrections (counts + recent trend, never
    presented as "AI accuracy").
GET /api/v1/officer/learning/review-queue — explainable prioritised
    unverified corrections for evaluation/review.
GET /api/v1/officer/learning/dataset — verified-only trusted evaluation
    dataset export (unverified rows are feedback only, excluded).
POST /api/v1/officer/learning/corrections/{correction_id}/verify —
    mark a correction verified (trusted dataset entry) or not.

No automatic training happens anywhere; verification only admits rows
into the evaluation dataset.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, require_roles
from app.repositories.base import Repo
from app.services.learning.error_patterns import aggregate_error_patterns
from app.services.learning.evaluation import (
    export_verified_dataset,
    review_queue,
)

router = APIRouter(tags=["learning"])

officer = Depends(require_roles("officer"))


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _all_corrections(repo: Repo) -> list[dict]:
    get = getattr(repo, "all_corrections", None)
    if callable(get):
        return list(get())
    return []


@router.get("/officer/learning/error-patterns")
def error_patterns(request: Request, principal: Principal = officer):
    try:
        rows = _all_corrections(_repo(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"patterns": aggregate_error_patterns(rows),
            "note": "Counts over verified officer corrections only; "
            "not an AI accuracy claim."}


@router.get("/officer/learning/review-queue")
def learning_queue(request: Request, limit: int = 50,
                   principal: Principal = officer):
    repo = _repo(request)
    try:
        rows = _all_corrections(repo)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    required: set[str] = set()
    try:
        from app.services import analysis_requirements as req_mod

        reqs = req_mod.get_analysis_field_requirements(repo, {})
        for entry in reqs.get("required", []):
            if entry.get("ocr_key"):
                required.add(entry["ocr_key"])
    except Exception:
        pass
    return {"queue": review_queue(rows, required,
                                  limit=max(1, min(limit, 200))),
            "note": "Prioritised unverified corrections for human "
            "evaluation. Nothing here retrains any model."}


@router.get("/officer/learning/dataset")
def learning_dataset(request: Request, principal: Principal = officer):
    try:
        rows = _all_corrections(_repo(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    examples = export_verified_dataset(rows)
    return {"examples": examples, "count": len(examples),
            "note": "Verified corrections only; unverified feedback "
            "is excluded from the trusted dataset."}


@router.post("/officer/learning/corrections/{correction_id}/verify")
def verify_learning_correction(correction_id: str, body: dict,
                               request: Request,
                               principal: Principal = officer):
    repo = _repo(request)
    verified = body.get("verified", True)
    if not isinstance(verified, bool):
        raise HTTPException(status_code=422,
                            detail="verified must be boolean")
    row = repo.verify_correction(
        correction_id, verified, principal.user_id or principal.role)
    if not row:
        raise HTTPException(status_code=404, detail="correction not found")
    repo.audit(principal.user_id or principal.role, "CORRECTION_VERIFIED",
               "correction", correction_id, {"verified": verified})
    return row
