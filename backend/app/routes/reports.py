"""Report endpoint — FinalReport per inspection.

The authoritative schema has no standalone reports table: a v1 report is
the persisted analysis for one inspection (report_id = inspection_id),
rebuilt from compliance_results + violations with full legal provenance.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.repositories.base import Repo

router = APIRouter(tags=["reports"])


def _repo(request: Request) -> Repo:
    return request.app.state.repo


@router.get("/reports/{report_id}")
def get_report(report_id: str, request: Request):
    repo = _repo(request)
    try:
        uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid report_id")
    inspection = repo.get_inspection(report_id)  # v1: report_id = inspection_id
    if not inspection:
        raise HTTPException(status_code=404, detail="report not found")
    results = repo.results_for(report_id)
    if not results:
        raise HTTPException(status_code=404,
                            detail="no analysis persisted for this inspection")
    by_product: dict[str, list] = {}
    for r in results:
        by_product.setdefault(r.get("product_id", ""), []).append(r)
    pid = sorted(by_product)[-1]
    findings = [{
        "rule_id": r.get("check_id", ""), "status": r.get("result", ""),
        "requirement": r.get("requirement", ""),
        "evidence": {"detected_value": r.get("detected_value"),
                     "confidence": r.get("confidence")},
        "explanation": r.get("explanation", ""),
        "source_reference": f"Rule {r.get('rule_number', '')}"
                            f"({r.get('sub_rule', '')}){r.get('clause') or ''}",
        "legal_version": {"rule_version_id": r.get("rule_version_id"),
                          "effective_from": r.get("effective_from"),
                          "effective_to": r.get("effective_to"),
                          "status": r.get("status")},
        "applicability": {}} for r in by_product[pid]]
    passed = sum(1 for f in findings if f["status"] == "PASS")
    return {
        "inspection_id": report_id, "product_id": pid,
        "status": ("NEEDS_REVIEW" if any(f["status"] == "NEEDS_REVIEW"
                                         for f in findings)
                   else "NON_COMPLIANT" if any(f["status"] == "FAIL"
                                               for f in findings)
                   else "COMPLIANT"),
        "score": {"value": None, "out_of": 100.0, "policy": "DEFAULT-2026",
                  "policy_version": "1.1", "finalizable": False},
        "findings": findings, "recommendations": [],
        "report_id": report_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_version": "1.0",
        "summary": {"total": len(findings), "pass": passed,
                    "violations": len(repo.violations_for(report_id))},
    }
