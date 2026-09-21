"""Officer console — queue, stats, profile, case detail, and actions.

All routes require the officer role (admins included via hierarchy).
Every officer action persists to violations + enforcement_actions +
audit_logs: decisions are never UI-only.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, require_roles
from app.repositories.base import Repo

router = APIRouter(tags=["officer"])

officer = Depends(require_roles("officer"))


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid {name}: {value}")


PERMISSIONS = {
    "consumer": ["inspections.create", "analysis.run", "reports.read",
                 "complaints.create", "complaints.read.own",
                 "suggestions.create", "suggestions.read.own"],
    "officer": ["inspections.create", "analysis.run", "reports.read",
                "complaints.create", "complaints.read.all",
                "violations.verify", "cases.read", "cases.act",
                "enforcement.record", "suggestions.read.all",
                "suggestions.review"],
    "admin": ["*"],
}


def _mask_user(user_id: str | None) -> str:
    """Privacy-safe consumer identifier for officer views."""
    text = (user_id or "").strip() or "anonymous"
    return text[:3] + "***" if len(text) > 3 else "***"


def _officer_suggestion(row: dict) -> dict:
    return {
        "suggestion_id": row.get("suggestion_id"),
        "title": row.get("title"),
        "category": row.get("category"),
        "description": row.get("description"),
        "context": row.get("context"),
        "location": row.get("location"),
        "status": row.get("status"),
        "consumer": _mask_user(row.get("consumer_user_id")),
        "reviewed_by": row.get("reviewed_by"),
        "officer_note": row.get("officer_note"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/officer/queue")
def queue(request: Request, status: str | None = None,
          severity: str | None = None, violation_type: str | None = None,
          check_id: str | None = None, category: str | None = None,
          state: str | None = None, date_from: str | None = None,
          date_to: str | None = None,
          principal: Principal = officer):
    return _repo(request).violations_queue({
        "status": status, "severity": severity,
        "violation_type": violation_type, "check_id": check_id,
        "category": category, "state": state,
        "date_from": date_from, "date_to": date_to})


@router.get("/officer/stats")
def stats(request: Request, principal: Principal = officer):
    return _repo(request).officer_stats()


@router.get("/officer/profile")
def profile(request: Request, principal: Principal = officer):
    repo = _repo(request)
    verifications = 0
    last_active = None
    try:
        rows = repo.violations_queue({})
        mine = [r for r in rows if r.get("inspector_id") == principal.user_id]
        verifications = len([r for r in mine
                             if r.get("inspector_status") != "PENDING"])
        dates = sorted((r.get("verification_date") or "") for r in mine
                       if r.get("verification_date"))
        last_active = dates[-1] if dates else None
    except Exception:
        pass
    return {
        "user_id": principal.user_id or "(dev identity — set X-LegalAkshi-User)",
        "role": principal.role,
        "role_source": principal.source,
        "email": principal.email,
        "name": principal.name,
        "permissions": PERMISSIONS.get(principal.role, []),
        "activity": {"verifications_recorded": verifications,
                     "last_active": last_active},
        "note": "Role and permissions come from the authenticated principal; "
                "users cannot self-promote.",
    }


@router.get("/officer/cases/{violation_id}")
def case_detail(violation_id: str, request: Request,
                principal: Principal = officer):
    _uuid(violation_id, "violation_id")
    detail = _repo(request).case_detail(violation_id)
    if not detail:
        raise HTTPException(status_code=404, detail="case not found")
    return detail


OFFICER_ACTIONS = ("confirm", "reject", "request_evidence", "under_review",
                   "action_taken", "close")

_ACTION_STATUS = {"confirm": "CONFIRMED", "reject": "REJECTED",
                  "request_evidence": "REQUIRES_REVIEW",
                  "action_taken": "CONFIRMED"}


@router.post("/officer/cases/{violation_id}/actions")
def officer_action(violation_id: str, body: dict, request: Request,
                   principal: Principal = Depends(require_roles("officer"))):
    repo = _repo(request)
    _uuid(violation_id, "violation_id")
    action = (body.get("action") or "").lower()
    if action not in OFFICER_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"action must be one of {sorted(OFFICER_ACTIONS)}")
    notes = body.get("notes", "")
    case = repo.case_detail(violation_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
    inspector = body.get("inspector_id") or principal.user_id or "officer"

    if action in _ACTION_STATUS:
        updated = repo.verify_violation(violation_id, _ACTION_STATUS[action],
                                        inspector, notes)
        repo.audit(inspector, "OFFICER_" + action.upper(), "violation",
                   violation_id, {"notes": notes})
    elif action == "under_review":
        repo.audit(inspector, "OFFICER_UNDER_REVIEW", "violation",
                   violation_id, {"notes": notes})
        updated = repo.get_violation(violation_id)
    else:  # close
        updated = repo.get_violation(violation_id)

    if action in ("action_taken", "close"):
        action_type = body.get("action_type", "")
        legal_basis = body.get("legal_basis", "")
        if not action_type or not legal_basis:
            raise HTTPException(
                status_code=422,
                detail="action_taken/close require action_type and legal_basis")
        enforcement = repo.record_enforcement_action({
            "violation_id": violation_id,
            "inspection_id": case["violation"]["inspection_id"],
            "action_type": action_type, "legal_basis": legal_basis,
            "enforcement_provision_id": body.get("enforcement_provision_id"),
            "authority": body.get("authority", ""),
            "action_status": "CLOSED" if action == "close" else "INITIATED",
            "notice_number": body.get("notice_number"),
            "remarks": notes, "created_by": inspector})
        repo.audit(inspector, "ENFORCEMENT_" + action.upper(), "violation",
                   violation_id, {"action_id": enforcement.get("action_id"),
                                  "action_type": action_type})
    return repo.case_detail(violation_id)


@router.get("/officer/suggestions")
def list_officer_suggestions(
        request: Request, status: str | None = None,
        category: str | None = None, date_from: str | None = None,
        date_to: str | None = None, q: str | None = None,
        principal: Principal = officer):
    """All consumer suggestions with officer-side filters (single query).

    Consumer identity is masked; the original suggestion text is never
    altered here — only status/note change via PATCH.
    """
    try:
        rows = _repo(request).list_suggestions(None)
    except HTTPException:
        raise
    except Exception as exc:
        from app.routes.consumer import _suggestions_unavailable

        raise _suggestions_unavailable(exc)
    needle = (q or "").strip().lower()
    out = []
    for row in rows:
        if status and row.get("status") != status:
            continue
        if category and row.get("category") != category:
            continue
        day = str(row.get("created_at", ""))[:10]
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        if needle and needle not in " ".join([
                str(row.get("suggestion_id", "")),
                str(row.get("title", "")),
                str(row.get("description", ""))]).lower():
            continue
        out.append(_officer_suggestion(row))
    return out


@router.get("/officer/suggestions/{suggestion_id}")
def get_officer_suggestion(suggestion_id: str, request: Request,
                           principal: Principal = officer):
    from app.routes.consumer import _suggestions_unavailable

    repo = _repo(request)
    _uuid(suggestion_id, "suggestion_id")
    try:
        row = repo.get_suggestion(suggestion_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)
    if not row:
        raise HTTPException(status_code=404, detail="suggestion not found")
    try:
        timeline = repo.suggestion_timeline(suggestion_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)
    return {**_officer_suggestion(row), "timeline": timeline}


@router.patch("/officer/suggestions/{suggestion_id}")
def review_suggestion(suggestion_id: str, body: dict, request: Request,
                      principal: Principal = Depends(require_roles("officer"))):
    """Officer status transition + optional note (lifecycle-validated)."""
    from app.routes.consumer import _suggestions_unavailable

    repo = _repo(request)
    _uuid(suggestion_id, "suggestion_id")
    to_status = str(body.get("to_status", "")).upper()
    if not to_status:
        raise HTTPException(status_code=422, detail="to_status is required")
    note = str(body.get("note", ""))
    try:
        row = repo.update_suggestion(suggestion_id, to_status,
                                     principal.user_id or principal.role,
                                     note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)
    if not row:
        raise HTTPException(status_code=404, detail="suggestion not found")
    repo.audit(principal.user_id or principal.role, "STATUS_CHANGE",
               "suggestion", suggestion_id,
               {"to_status": to_status, "note": note})
    try:
        timeline = repo.suggestion_timeline(suggestion_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _suggestions_unavailable(exc)
    return {**_officer_suggestion(row), "timeline": timeline}
