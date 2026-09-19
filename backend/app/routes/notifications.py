"""Notification inbox — real application events only.

Rows are written by event producers (rule engine, complaint lifecycle,
rule sync), never fabricated. Listing is scoped to the caller's principal:
officers see the officer inbox, consumers see broadcasts plus their own
user notices. Marking read is scoped the same way.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, get_principal
from app.repositories.base import Repo

router = APIRouter(tags=["notifications"])


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _audiences(principal: Principal) -> list[str]:
    if principal.can("officer"):
        return ["officer"]
    if principal.user_id:
        return ["consumer", f"user:{principal.user_id}"]
    return []


@router.get("/notifications")
def list_notifications(request: Request,
                       principal: Principal = Depends(get_principal)):
    return _repo(request).list_notifications(_audiences(principal))


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, request: Request,
              principal: Principal = Depends(get_principal)):
    try:
        uuid.UUID(notification_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid notification_id")
    row = _repo(request).mark_notification_read(notification_id,
                                                _audiences(principal))
    if not row:
        raise HTTPException(status_code=404, detail="notification not found")
    return row


@router.post("/notifications/read-all")
def mark_all_read(request: Request,
                  principal: Principal = Depends(get_principal)):
    marked = _repo(request).mark_notifications_read(_audiences(principal))
    return {"marked": marked}
