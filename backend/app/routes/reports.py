"""Report endpoint — FinalReport per inspection.

One backend service (app.services.reports) renders HTML, PDF and JSON from
the SAME persisted analysis rows. format=json is the default (existing
OpenAPI contract); format=html previews in browser; format=pdf downloads
the official report.
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.core.config import get_settings
from app.core import dbmetrics as dbmetrics_mod
from app.engine import scoring as scoring_mod
from app.repositories.base import Repo
from app.services import reports as report_svc

router = APIRouter(tags=["reports"])

log = logging.getLogger("legalakshi.reports")


def _repo(request: Request) -> Repo:
    return request.app.state.repo


@router.get("/reports/{report_id}")
def get_report(report_id: str, request: Request, format: str = "json"):
    repo = _repo(request)
    try:
        uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid report_id")
    # Phase timing (debug log only; no PII, no OCR text): total request
    # time lands in the X-Request-Duration-Ms header via middleware.
    t_build = time.perf_counter()
    db_before = dbmetrics_mod.count()
    data = report_svc.build_report_data(repo, report_id)  # report_id = inspection_id
    build_ms = round((time.perf_counter() - t_build) * 1000, 1)
    if data is None:
        if repo.get_inspection(report_id) is None:
            raise HTTPException(status_code=404, detail="report not found")
        raise HTTPException(status_code=404,
                            detail="no analysis persisted for this inspection")
    score = report_svc.finding_score(data["findings"], data["policy"])
    log.debug("report %s build: %sms (%s db calls, %s findings)",
              report_id, build_ms, dbmetrics_mod.count() - db_before,
              len(data["findings"]))
    if format == "pdf":
        t_pdf = time.perf_counter()
        pdf = report_svc.render_pdf(data, score)
        log.debug("report %s pdf render: %sms (%s bytes)", report_id,
                  round((time.perf_counter() - t_pdf) * 1000, 1), len(pdf))
        return Response(
            content=pdf, media_type="application/pdf",
            headers={"Content-Disposition":
                     f"attachment; filename=legalakshi-report-{report_id}.pdf"})
    if format == "html":
        return HTMLResponse(report_svc.render_html(data, score))
    if format != "json":
        raise HTTPException(status_code=422,
                            detail="format must be json|html|pdf")
    pid = data["product_id"]
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
        "applicability": {}} for r in data["findings"]]
    passed = sum(1 for f in findings if f["status"] == "PASS")
    cfg = get_settings()
    engine_version = next((r.get("engine_version", cfg.ENGINE_VERSION)
                           for r in data["findings"]), cfg.ENGINE_VERSION)
    return {
        "inspection_id": report_id, "product_id": pid,
        "status": score["status"],
        "score": {"value": score["value"], "out_of": 100.0,
                  "policy": score["policy"],
                  "policy_version": score["policy_version"],
                  "finalizable": score["finalizable"]},
        "findings": findings,
        "recommendations": (["Inspector verification required before "
                             "finalization."] if score["needs_review"] else []),
        "engine": {"engine_version": engine_version,
                   "policy": score["policy"],
                   "policy_version": score["policy_version"]},
        "report_id": report_id,
        "generated_at": data["generated_at"],
        "report_version": "1.0",
        "summary": {"total": len(findings), "pass": passed,
                    "violations": len(data["all_violations"])},
    }
