"""Deterministic rule-engine orchestrator.

PostgreSQL -> rule_versions -> rule_applicability -> engine_check_registry
-> generic check handler -> compliance result. No legal rule is hard-coded;
Python only interprets database configuration.

Findings follow the authoritative Finding shape:
{rule_id(check_id), status, requirement, evidence{}, explanation,
 source_reference, legal_version{}, applicability{}}.

Confidence origins: only measured OCR confidence below threshold forces
NEEDS_REVIEW. MANUAL/ASSUMED values are evaluated as stated with the origin
exposed. Sources whose authenticity_status is not VERIFIED_PRIMARY /
VERIFIED_SECONDARY force NEEDS_REVIEW instead of a legal conclusion.
"""
from __future__ import annotations

import logging
from typing import Any

from app.engine import applicability as app_mod
from app.engine import checks as checks_mod
from app.engine import scoring as scoring_mod
from app.engine.facts import ASSUMED, MANUAL, OCR, build_facts, values_of

log = logging.getLogger("legalakshi.engine")

VERIFIED = {"VERIFIED_PRIMARY", "VERIFIED_SECONDARY"}


def analyze(repo, inspection: dict, product: dict,
            declarations: list[dict] | None = None,
            as_of: str | None = None,
            engine_version: str = "1.0.0",
            policy_code: str = "DEFAULT-2026",
            low_conf_threshold: float = 0.6,
            default_origin: str = MANUAL) -> dict[str, Any]:
    as_of = as_of or inspection.get("inspection_date") or "2026-09-15"
    if declarations is None and hasattr(repo, "declarations_for"):
        pid = product.get("product_id") or product.get("inspected_product_id")
        declarations = repo.declarations_for(pid) if pid else []
    facts = build_facts(product, declarations or [], default_origin)
    values = values_of(facts)
    versions = repo.active_rule_versions(str(as_of))
    log.info("analyze inspection=%s product=%s as_of=%s versions=%d",
             inspection.get("inspection_id"), product.get("product_id"),
             as_of, len(versions))

    findings: list[dict[str, Any]] = []
    for ver in versions:
        appl_rows = repo.applicability_for(ver["rule_version_id"])
        for chk in repo.checks_for(ver["rule_version_id"]):
            decision = app_mod.decide(ver["rule_version_id"], appl_rows, values,
                                      bool(chk.get("mandatory_default", True)))
            findings.append(_evaluate(repo, inspection, product, facts, values,
                                      ver, chk, decision, engine_version,
                                      low_conf_threshold))

    policy = repo.scoring_policy(policy_code)
    summary = scoring_mod.score(findings, policy)

    violation_ids: list[str] = []
    for f in findings:
        if f["status"] == "FAIL":
            v = repo.create_violation({
                "inspection_id": inspection["inspection_id"],
                "product_id": product.get("product_id")
                or product.get("inspected_product_id"),
                "compliance_result_id": f["compliance_result_id"],
                "rule_version_id": f["legal_version"]["rule_version_id"],
                "violation_type": ("MISSING_DECLARATION"
                                   if not f["evidence"].get("detected_value")
                                   else "NON_CONFORMING_DECLARATION"),
                "description": f["explanation"],
                "detected_value": f["evidence"].get("detected_value"),
                "expected_value": f.get("expected_note"),
                "evidence_id": f["evidence"].get("source_evidence_id"),
                "ai_confidence": f["evidence"].get("confidence"),
            })
            violation_ids.append(v["violation_id"])

    recommendations = [f"Inspector verification required before finalization."
                       for _ in [0] if summary["needs_review"]]
    for f in findings:
        if f["status"] == "FAIL":
            recommendations.append(f"{f['rule_id']}: {f['requirement']}")

    repo.audit("engine", "analyze", "inspection",
               str(inspection.get("inspection_id")),
               {"product_id": str(product.get("product_id")
                                  or product.get("inspected_product_id")),
                "score": summary["value"], "findings": len(findings),
                "violations": len(violation_ids)})
    return {
        "inspection_id": inspection["inspection_id"],
        "product_id": product.get("product_id") or product.get("inspected_product_id"),
        "as_of_date": str(as_of),
        "status": summary["status"], "score": summary,
        "findings": findings, "recommendations": recommendations,
        "violation_ids": violation_ids,
        "engine": {"engine_version": engine_version,
                   "policy": summary["policy"],
                   "policy_version": summary["policy_version"],
                   "rule_versions": len(versions)},
    }


def _evaluate(repo, inspection, product, facts, values, ver, chk,
              decision, engine_version, low_conf_threshold) -> dict[str, Any]:
    field, check_id = chk.get("field_name", ""), chk["check_id"]
    fact = facts.get(field, {})
    provenance_note = ""
    if not decision["applicable"]:
        status = decision["status"]
        result = "NOT_APPLICABLE" if status in ("NOT_APPLICABLE", "NOT_REQUIRED") \
            else "NEEDS_REVIEW"
        detected, conf = "", fact.get("confidence")
        explanation = (f"Applicability {status}: "
                       f"{decision['reason'] or 'see applicability config'}.")
    elif ver.get("authenticity_status") not in VERIFIED:
        result = "NEEDS_REVIEW"
        detected = str(values.get(field) or "")
        conf = fact.get("confidence")
        provenance_note = (
            f"Source authenticity is '{ver.get('authenticity_status')}' "
            f"({ver.get('source_title')}) — verification required before "
            f"treating as operative law.")
        explanation = provenance_note
    else:
        out = checks_mod.run_check(chk, values)
        result, detected, explanation = out["result"], out["detected"], out["explanation"]
        conf = fact.get("confidence")
        # low measured OCR confidence -> human review, never silent PASS/FAIL
        if (fact.get("origin") == OCR and conf is not None
                and conf < low_conf_threshold and result in ("PASS", "FAIL")):
            result = "NEEDS_REVIEW"
            explanation += (f" OCR confidence {conf} below threshold "
                            f"{low_conf_threshold}; routed to inspector.")

    try:
        conf_val = None if conf is None else float(conf)
    except (TypeError, ValueError):
        conf_val = None
    saved = repo.save_result({
        "inspection_id": inspection["inspection_id"],
        "product_id": product.get("product_id") or product.get("inspected_product_id"),
        "rule_version_id": ver["rule_version_id"],
        "requirement": ver.get("requirement", ""),
        "expected_value": _expected_note(chk),
        "detected_value": detected or None,
        "result": result, "confidence": conf_val,
        "evidence_id": fact.get("evidence_id"),
        "engine_version": engine_version, "explanation": explanation,
    })
    return {
        "compliance_result_id": saved.get("compliance_result_id"),
        "rule_id": check_id, "status": result,
        "requirement": ver.get("requirement", ""),
        "expected_note": _expected_note(chk),
        "evidence": {"field": field, "detected_value": detected or None,
                     "confidence": conf_val,
                     "confidence_origin": fact.get("origin", MANUAL),
                     "source_evidence_id": fact.get("evidence_id")},
        "explanation": explanation,
        "source_reference": ver.get("source_reference", ""),
        "legal_version": {
            "rule_version_id": ver["rule_version_id"],
            "requirement": ver.get("requirement", ""),
            "legal_text_or_paraphrase": ver.get("legal_text_or_paraphrase", ""),
            "effective_from": ver.get("effective_from"),
            "effective_to": ver.get("effective_to"),
            "status": ver.get("status"),
            "authenticity_status": ver.get("authenticity_status"),
            "source_title": ver.get("source_title"),
            "source_url": ver.get("source_url")},
        "applicability": {"status": decision["status"],
                          "reason": decision["reason"]},
    }


def _expected_note(chk: dict) -> str:
    desc = chk.get("description", "") or chk.get("requirement", "")
    return desc


def origin_of(_fact: dict) -> str:
    return _fact.get("origin", ASSUMED)
