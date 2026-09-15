"""Inspection / product / analysis endpoints (authoritative openapi.json)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.engine import engine as engine_mod
from app.models.schemas import AnalyzeRequest, InspectionCreate, Product
from app.repositories.base import Repo

router = APIRouter(tags=["inspections"])


def _repo(request: Request) -> Repo:
    return request.app.state.repo


def _uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"invalid {name}: {value}")


@router.post("/inspections", status_code=201)
def create_inspection(body: InspectionCreate, request: Request):
    repo = _repo(request)
    return repo.create_inspection(body.model_dump(exclude_none=True, mode="json"))


@router.get("/inspections")
def list_inspections(request: Request):
    return _repo(request).list_inspections()  # authoritative: bare array


@router.get("/inspections/{inspection_id}")
def get_inspection(inspection_id: str, request: Request):
    repo = _repo(request)
    _uuid(inspection_id, "inspection_id")
    row = repo.get_inspection(inspection_id)
    if not row:
        raise HTTPException(status_code=404, detail="inspection not found")
    return {**row, "products": repo.products_for(inspection_id)}


@router.post("/inspections/{inspection_id}/products", status_code=201)
def add_product(inspection_id: str, body: Product, request: Request):
    repo = _repo(request)
    _uuid(inspection_id, "inspection_id")
    if not repo.get_inspection(inspection_id):
        raise HTTPException(status_code=404, detail="inspection not found")
    return repo.add_product(inspection_id, body.model_dump(mode="json"))


@router.post("/inspections/{inspection_id}/analyze")
def analyze(inspection_id: str, body: AnalyzeRequest, request: Request):
    repo = _repo(request)
    cfg = get_settings()
    _uuid(inspection_id, "inspection_id")
    inspection = repo.get_inspection(inspection_id)
    if not inspection:
        raise HTTPException(status_code=404, detail="inspection not found")
    as_of = str(body.as_of_date) if body.as_of_date else inspection.get("inspection_date")
    if body.product is not None:
        product = repo.add_product(
            inspection_id, body.product.model_dump(exclude_none=True, mode="json"))
    elif body.product_id:
        _uuid(body.product_id, "product_id")
        product = repo.get_product(body.product_id)
        if not product or product.get("inspection_id") != inspection_id:
            raise HTTPException(status_code=404, detail="product not found")
    else:
        products = repo.products_for(inspection_id)
        if not products:
            raise HTTPException(status_code=422, detail="no products to analyze")
        full = repo.get_product(products[0].get("product_id")
                                or products[0].get("inspected_product_id"))
        if not full:
            raise HTTPException(status_code=404, detail="product not found")
        product = full
    pid = product.get("product_id") or product.get("inspected_product_id")
    try:
        result = engine_mod.analyze(
            repo, inspection, product, repo.declarations_for(pid),
            as_of=as_of, engine_version=cfg.ENGINE_VERSION,
            policy_code=body.scoring_policy_code or cfg.SCORING_POLICY_CODE,
            low_conf_threshold=cfg.LOW_CONFIDENCE_THRESHOLD)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"analysis failed: {exc}")
    findings = [{k: f[k] for k in ("rule_id", "status", "requirement", "evidence",
                                   "explanation", "source_reference",
                                   "legal_version", "applicability")}
                for f in result["findings"]]
    return {"inspection_id": result["inspection_id"], "product_id": result["product_id"],
            "status": result["status"], "score": result["score"],
            "findings": findings, "recommendations": result["recommendations"],
            "evidence": repo.declarations_for(pid),
            "engine": result["engine"], "violation_ids": result["violation_ids"]}


@router.get("/inspections/{inspection_id}/products/{product_id}/compliance")
def compliance(inspection_id: str, product_id: str, request: Request):
    repo = _repo(request)
    _uuid(inspection_id, "inspection_id")
    _uuid(product_id, "product_id")
    if not repo.get_inspection(inspection_id):
        raise HTTPException(status_code=404, detail="inspection not found")
    out = []
    for r in repo.results_for(inspection_id, product_id):
        out.append({
            "rule_id": r.get("check_id", ""), "status": r.get("result", ""),
            "requirement": r.get("requirement", ""),
            "evidence": {"detected_value": r.get("detected_value"),
                         "confidence": r.get("confidence")},
            "explanation": r.get("explanation", ""),
            "source_reference": f"Rule {r.get('rule_number', '')}"
                                f"({r.get('sub_rule', '')}){r.get('clause') or ''}",
            "legal_version": {"rule_version_id": r.get("rule_version_id"),
                              "effective_from": r.get("effective_from"),
                              "effective_to": r.get("effective_to")},
            "applicability": {}})
    return out  # authoritative: bare array


@router.get("/inspections/{inspection_id}/results")
def results(inspection_id: str, product_id: str | None = None, request: Request = None):  # type: ignore[assignment]
    repo = _repo(request)
    _uuid(inspection_id, "inspection_id")
    if not repo.get_inspection(inspection_id):
        raise HTTPException(status_code=404, detail="inspection not found")
    return {"items": repo.results_for(inspection_id, product_id)}
