"""Rules / check-registry endpoints (authoritative openapi.json)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.repositories.base import Repo

router = APIRouter(tags=["rules"])


def _repo(request: Request) -> Repo:
    return request.app.state.repo


@router.get("/rules")
def list_rules(request: Request):
    """Bare array of Rule objects (authoritative shape)."""
    out = []
    for c in _repo(request).list_checks():
        out.append({
            "rule_id": c["check_id"], "rule_number": c.get("rule_number", "6"),
            "title": c.get("title", ""), "field": c.get("field_name", ""),
            "check_type": c.get("check_type", ""),
            "mandatory_default": c.get("mandatory_default", True),
            "requirement": c.get("description", ""),
            "source_reference": c.get("source_reference")
            or f"Rule {c.get('rule_number', '6')}({c.get('sub_rule', '')})"
               f"{c.get('clause') or ''}"})
    return out


@router.get("/rules/{check_id}")
def rule_detail(check_id: str, request: Request):
    detail = _repo(request).rule_detail(check_id)
    if not detail:
        raise HTTPException(status_code=404, detail="check not found")
    return detail


@router.get("/scoring-policy")
def scoring_policy(request: Request):
    return _repo(request).scoring_policy(get_settings().SCORING_POLICY_CODE)
