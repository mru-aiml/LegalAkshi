"""Consumer complaint intake + lifecycle (migration 001).

Consumers create complaints and read their own; status transitions are
officer/admin-only and validated against the lifecycle map. Every transition
writes a complaint_events timeline row.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, admin_only, get_principal, require_roles
from app.models.schemas import ComplaintCreate
from app.repositories.base import Repo

router = APIRouter(tags=["complaints"])


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid {name}: {value}")


@router.post("/complaints", status_code=201)
def create_complaint(body: ComplaintCreate, request: Request,
                     principal: Principal = Depends(get_principal)):
    repo = _repo(request)
    data = body.model_dump(exclude_none=True, mode="json")
    if not data.get("reporter_id"):
        data["reporter_id"] = principal.user_id or "anonymous-consumer"
    try:
        saved = repo.create_complaint(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    repo.create_notification({
        "audience": "officer", "type": "complaint_submitted",
        "title": f"Complaint submitted: {saved.get('product_name', 'product')}",
        "body": f"{saved.get('retailer', '')} · {saved.get('city', '')}".strip(" ·"),
        "link": f"/complaints/{saved.get('complaint_id', '')}"})
    return saved


@router.get("/complaints")
def list_complaints(request: Request,
                    principal: Principal = Depends(get_principal)):
    repo = _repo(request)
    # Officers/admins see the full intake; consumers see only their own.
    if principal.can("officer"):
        return repo.list_complaints(None)
    if not principal.user_id:
        return repo.list_complaints("__anonymous__")
    return repo.list_complaints(principal.user_id)


@router.get("/complaints/{complaint_id}")
def get_complaint(complaint_id: str, request: Request,
                  principal: Principal = Depends(get_principal)):
    repo = _repo(request)
    _uuid(complaint_id, "complaint_id")
    row = repo.get_complaint(complaint_id)
    if not row:
        raise HTTPException(status_code=404, detail="complaint not found")
    if not principal.can("officer") and row.get("reporter_id") != principal.user_id:
        raise HTTPException(status_code=403, detail="not your complaint")
    return {**row, "timeline": repo.complaint_timeline(complaint_id)}


@router.post("/complaints/{complaint_id}/transitions")
def transition_complaint(complaint_id: str, body: dict, request: Request,
                         principal: Principal = Depends(require_roles("officer"))):
    repo = _repo(request)
    _uuid(complaint_id, "complaint_id")
    to_status = (body.get("to_status") or "").upper()
    if not to_status:
        raise HTTPException(status_code=422, detail="to_status is required")
    try:
        row = repo.transition_complaint(complaint_id, to_status,
                                        principal.user_id,
                                        body.get("note", ""))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not row:
        raise HTTPException(status_code=404, detail="complaint not found")
    reporter = row.get("reporter_id", "")
    if reporter:
        repo.create_notification({
            "audience": f"user:{reporter}", "type": "complaint_status_changed",
            "title": f"Complaint update: {to_status}",
            "body": row.get("product_name", ""),
            "link": f"/complaints/{complaint_id}"})
    repo.audit(principal.user_id or principal.role, "STATUS_CHANGE",
               "complaint", complaint_id,
               {"to_status": to_status, "note": body.get("note", "")})
    return {**row, "timeline": repo.complaint_timeline(complaint_id)}
