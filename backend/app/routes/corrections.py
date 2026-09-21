"""Declaration correction API (Stage 2, Part J).

POST /api/v1/inspections/{inspection_id}/products/{product_id}/corrections
GET  /api/v1/inspections/{inspection_id}/products/{product_id}/corrections

Officer-only. POST records field + original/corrected values + evidence
snapshot + officer identity + timestamp + reason. Append-only: originals
are never overwritten. Consumers cannot create corrections (403).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, require_roles
from app.repositories.base import Repo
from app.services.learning.corrections import build_correction

router = APIRouter(tags=["corrections"])

officer = Depends(require_roles("officer"))


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid {name}: {value}")


@router.post("/inspections/{inspection_id}/products/{product_id}/corrections",
             status_code=201)
def create_correction(inspection_id: str, product_id: str, body: dict,
                      request: Request,
                      principal: Principal = officer):
    repo = _repo(request)
    _uuid(inspection_id, "inspection_id")
    _uuid(product_id, "product_id")
    if not repo.get_inspection(inspection_id):
        raise HTTPException(status_code=404, detail="inspection not found")
    product = repo.get_product(product_id)
    if not product or product.get("inspection_id") != inspection_id:
        raise HTTPException(status_code=404, detail="product not found")
    field_key = str(body.get("field_key") or body.get("field") or "")
    if not field_key.strip():
        raise HTTPException(status_code=422, detail="field_key is required")
    try:
        payload = build_correction(
            inspection_id, product_id, field_key,
            body.get("original_value"), body.get("corrected_value"),
            officer_user_id=principal.user_id or principal.role,
            original_status=str(body.get("original_status")
                                or "NEEDS_REVIEW"),
            original_confidence=body.get("original_confidence"),
            source=str(body.get("source") or "officer-review"),
            evidence_snapshot=body.get("evidence_snapshot") or {},
            correction_reason=str(body.get("correction_reason")
                                  or body.get("reason") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        row = repo.create_correction(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    repo.audit(principal.user_id or principal.role, "DECLARATION_CORRECTED",
               "product", product_id,
               {"field": row.get("field_key"),
                "correction_id": row.get("id")})
    return row


@router.get("/inspections/{inspection_id}/products/{product_id}/corrections")
def list_corrections(inspection_id: str, product_id: str, request: Request,
                     principal: Principal = officer):
    repo = _repo(request)
    _uuid(inspection_id, "inspection_id")
    _uuid(product_id, "product_id")
    if not repo.get_inspection(inspection_id):
        raise HTTPException(status_code=404, detail="inspection not found")
    try:
        return repo.list_corrections(inspection_id, product_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
