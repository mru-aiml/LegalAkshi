"""Admin rule management — authoritative import/sync plus exceptional
manual operations. All routes require the admin role.

Primary workflow is Import/Sync from legalakshi_master_v4.json (preview,
then apply). Manual check/version/source creation exists for exceptional
cases (e.g. a brand-new lineage the manifest cannot express) and always
preserves history: new temporal versions, never overwrites.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import Principal, require_roles
from app.repositories.base import Repo
from app.services import rule_sync as sync

router = APIRouter(tags=["admin"])

admin = Depends(require_roles("admin"))


def _repo(request: Request) -> Repo:
    return request.app.state.repo


@router.get("/admin/rules/sync/preview")
def sync_preview(request: Request, principal: Principal = admin):
    manifest = sync.load_manifest()
    errors = sync.validate_manifest(manifest)
    if errors:
        raise HTTPException(status_code=422,
                            detail={"validation_errors": errors})
    return sync.apply_sync(_repo(request), manifest, principal.user_id,
                           dry_run=True)


@router.post("/admin/rules/sync/apply")
def sync_apply(body: dict, request: Request, principal: Principal = admin):
    if body.get("confirm") is not True:
        raise HTTPException(status_code=422,
                            detail="pass {confirm: true} to apply the sync")
    manifest = sync.load_manifest()
    errors = sync.validate_manifest(manifest)
    if errors:
        raise HTTPException(status_code=422,
                            detail={"validation_errors": errors})
    result = sync.apply_sync(_repo(request), manifest,
                             principal.user_id or "admin", dry_run=False)
    _repo(request).create_notification({
        "audience": "officer", "type": "rule_sync",
        "title": "Authoritative rules synced",
        "body": "Rule configuration updated from the legal manifest.",
        "link": "/rules"})
    return result


@router.post("/admin/sources", status_code=201)
def create_source(body: dict, request: Request, principal: Principal = admin):
    repo = _repo(request)
    for key in ("source_type", "title", "issuing_authority",
                "authenticity_status"):
        if not body.get(key):
            raise HTTPException(status_code=422, detail=f"{key} is required")
    try:
        saved = repo.create_legal_source(body)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    repo.audit(principal.user_id or "admin", "ADMIN_CREATE_SOURCE",
               "legal_sources", saved.get("source_id", ""), body)
    return saved


@router.post("/admin/rules/checks", status_code=201)
def create_check(body: dict, request: Request, principal: Principal = admin):
    repo = _repo(request)
    for key in ("check_id", "rule_number", "title", "field_name", "check_type"):
        if not body.get(key):
            raise HTTPException(status_code=422, detail=f"{key} is required")
    if body["check_type"] not in sync.CHECK_TYPES:
        raise HTTPException(status_code=422, detail="unknown check_type")
    rule_row = repo.get_rule_by_number(body["rule_number"])
    if rule_row is None:
        raise HTTPException(status_code=422, detail="unknown rule_number catalogue entry")
    try:
        saved = repo.insert_registry_entry({**body, "rule_id": rule_row["rule_id"]})
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    repo.audit(principal.user_id or "admin", "ADMIN_CREATE_CHECK",
               "engine_check_registry", saved["check_id"], body)
    return saved


@router.post("/admin/rules/versions", status_code=201)
def create_version(body: dict, request: Request, principal: Principal = admin):
    repo = _repo(request)
    for key in ("check_id", "requirement", "legal_text_or_paraphrase",
                "requirement_type", "effective_from", "source_id", "status"):
        if not body.get(key):
            raise HTTPException(status_code=422, detail=f"{key} is required")
    if body["status"] not in sync.STATUSES:
        raise HTTPException(status_code=422, detail="unknown status")
    detail = repo.rule_detail(body["check_id"])
    if not detail:
        raise HTTPException(status_code=404, detail="check not found")
    rule_id = None
    for row in [detail["check"]]:
        rule_id = row.get("rule_id_hint") or row.get("rule_id")
    # resolve catalogue rule_id via an existing sibling version when present
    versions = detail.get("versions", [])
    rule_id = (versions[0].get("rule_id") if versions and versions[0].get("rule_id")
               else None)
    if rule_id is None:
        rule_row = repo.get_rule_by_number(detail["check"].get("rule_number", "6"))
        if rule_row is None:
            raise HTTPException(status_code=422, detail="unresolvable rule catalogue entry")
        rule_id = rule_row["rule_id"]
    data = {**body, "rule_id": rule_id,
            "sub_rule": detail["check"].get("sub_rule"),
            "clause": detail["check"].get("clause")}
    try:
        saved = repo.insert_rule_version(data)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if body.get("supersedes"):
        repo.supersede_version(body["supersedes"], body["effective_from"])
    repo.audit(principal.user_id or "admin", "ADMIN_CREATE_VERSION",
               "rule_versions", saved.get("rule_version_id", ""), body)
    return saved
