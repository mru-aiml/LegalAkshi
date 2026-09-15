"""Pydantic contracts mirroring backend/authoritative/openapi.json."""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel


class InspectionCreate(BaseModel):
    inspector_id: str
    inspector_name: str
    business_name: str
    inspection_date: date
    department: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    premises_id: Optional[str] = None
    business_id: Optional[str] = None
    inspection_type: Optional[str] = None
    inspection_time: Optional[str] = None
    location: Optional[str] = None
    remarks: Optional[str] = None


class Product(BaseModel):
    product_name: str
    category: str
    is_prepackaged: bool = True
    brand: Optional[str] = None
    manufacturer: Optional[str] = None
    packer: Optional[str] = None
    importer: Optional[str] = None
    country_of_origin: Optional[str] = None
    subcategory: Optional[str] = None
    quantity: Optional[float] = None
    quantity_unit: Optional[str] = None
    quantity_type: Optional[str] = None
    manufacturing_date: Optional[str] = None
    packing_date: Optional[str] = None
    best_before: Optional[str] = None
    use_by: Optional[str] = None
    imported: bool = False
    ecommerce: bool = False
    barcode: Optional[str] = None
    qr_code: Optional[str] = None
    source_listing_url: Optional[str] = None
    electronic_product: Optional[bool] = None
    food: Optional[bool] = None
    cosmetic: Optional[bool] = None
    may_become_unfit: Optional[bool] = None
    combination_package: bool = False
    group_package: bool = False
    multi_piece_package: bool = False
    package_qr_notice_present: Optional[bool] = None
    country_of_origin_filter: Optional[bool] = None

    model_config = {"extra": "allow"}  # manual declarations (mrp, consumer_care…)


class AnalyzeRequest(BaseModel):
    product: Optional[Product] = None
    product_id: Optional[str] = None  # convenience: analyze stored product
    evidence_ids: Optional[list[str]] = None
    as_of_date: Optional[date] = None
    scoring_policy_code: Optional[str] = None
    scoring_policy_version: Optional[str] = None


class Verification(BaseModel):
    decision: str  # PENDING | CONFIRMED | REJECTED | REQUIRES_REVIEW
    inspector_id: str
    verification_notes: Optional[str] = None
    evidence_confirmed: Optional[bool] = None
    legal_basis_reviewed: Optional[bool] = None
    action_recommended: Optional[str] = None


class Finding(BaseModel):
    rule_id: str
    status: str
    requirement: str
    evidence: dict[str, Any]
    explanation: str
    source_reference: Optional[str] = None
    legal_version: Optional[dict[str, Any]] = None
    applicability: Optional[dict[str, Any]] = None
