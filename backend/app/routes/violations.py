"""Violation endpoints — engine FAILs start PENDING; inspector decides."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, require_roles
from app.models.schemas import Verification
from app.repositories.base import Repo

router = APIRouter(tags=["violations"])

ALLOWED = {"PENDING", "CONFIRMED", "REJECTED", "REQUIRES_REVIEW"}


def _repo(request: Request) -> Repo:
    return request.app.state.repo


@router.get("/inspections/{inspection_id}/violations")
def list_violations(inspection_id: str, request: Request):
    repo = _repo(request)
    try:
        uuid.UUID(inspection_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid inspection_id")
    if not repo.get_inspection(inspection_id):
        raise HTTPException(status_code=404, detail="inspection not found")
    return {"items": repo.violations_for(inspection_id)}


@router.post("/violations/{violation_id}/verify")
def verify_violation(violation_id: str, body: Verification, request: Request,
                     principal: Principal = Depends(require_roles("officer"))):
    repo = _repo(request)
    try:
        uuid.UUID(violation_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid violation_id")
    if body.decision not in ALLOWED:
        raise HTTPException(status_code=422,
                            detail=f"decision must be one of {sorted(ALLOWED)}")
    if not body.inspector_id:
        raise HTTPException(status_code=422, detail="inspector_id is required")
    try:
        row = repo.verify_violation(violation_id, body.decision,
                                    body.inspector_id,
                                    body.verification_notes or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not row:
        raise HTTPException(status_code=404, detail="violation not found")
    repo.audit("inspector", "verify_violation", "violation", violation_id,
               {"decision": body.decision, "inspector_id": body.inspector_id})
    return row
