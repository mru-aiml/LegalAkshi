"""TEST-ONLY in-memory repository.

Seeded from backend/authoritative/legalakshi_master_v4.json, which is the
generated projection of the authoritative PostgreSQL schema — NOT the
source of truth. Production MUST use PostgresRepo. Anything that behaves
differently here (e.g. no exclusion-constraint enforcement at write time)
is covered by seed-validation tests instead.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

MASTER_PATH = (Path(__file__).resolve().parent.parent.parent
               / "authoritative" / "legalakshi_master_v4.json")


def load_master() -> dict[str, Any]:
    with open(MASTER_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class MemoryRepo:
    kind = "memory-test-only"

    # authoritative inspected_products columns (subset we persist)
    PRODUCT_COLS = (
        "product_name", "brand", "manufacturer", "packer", "importer",
        "country_of_origin", "category", "subcategory", "is_prepackaged",
        "intended_consumer_type", "quantity", "quantity_unit", "quantity_type",
        "manufacturing_date", "packing_date", "import_date", "best_before",
        "use_by", "imported", "ecommerce", "product_identifier", "barcode",
        "qr_code", "source_listing_url", "combination_package", "group_package",
        "multi_piece_package",
    )

    def __init__(self, master: dict[str, Any] | None = None) -> None:
        self.master = master if master is not None else load_master()
        lk = self.master["legal_knowledge"]
        self._rules = {r["rule_id"]: r for r in lk["rules"]}
        self._versions = lk["rule_versions"]
        self._appl = lk["applicability_rules"]
        self.inspections: dict[str, dict] = {}
        self.products: dict[str, dict] = {}
        self.evidence: dict[str, dict] = {}
        self.declarations: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.violations: dict[str, dict] = {}
        self.audit_log: list[dict] = []

    # ------------------------------------------------------------- legal ---
    def active_rule_versions(self, as_of: str) -> list[dict[str, Any]]:
        # Temporal selection: the version whose validity range covers as_of.
        # SUPERSEDED versions are selectable for historical dates (they were
        # in force then); DRAFT / NOT_YET_IN_FORCE / REPEALED never are.
        best: dict[str, dict] = {}
        for v in self._versions:
            if v["status"] not in ("IN_FORCE", "SUPERSEDED"):
                continue
            if not (v["effective_from"] <= as_of):
                continue
            if v["effective_to"] is not None and not (v["effective_to"] > as_of):
                continue
            rule = self._rules[v["check_id"]]
            row = {"rule_version_id": v["rule_version_id"], "check_id": v["check_id"],
                   "sub_rule": rule["sub_rule"], "clause": rule["clause"],
                   "requirement": v["requirement"],
                   "legal_text_or_paraphrase": v["legal_text_or_paraphrase"],
                   "rule_number": rule["rule_number"], "rule_title": rule["title"],
                   "source_reference": rule["source_reference"],
                   "source_title": v["provenance"]["source_title"],
                   "source_url": v["provenance"].get("source_url"),
                   "authenticity_status": v["provenance"]["authenticity_status"],
                   "effective_from": v["effective_from"], "effective_to": v["effective_to"],
                   "status": v["status"], "supersedes": v["supersedes"],
                   "superseded_by": v["superseded_by"]}
            prev = best.get(v["check_id"])
            if prev is None or v["effective_from"] > prev["effective_from"]:
                best[v["check_id"]] = row
        return list(best.values())

    def applicability_for(self, rule_version_id: str) -> list[dict[str, Any]]:
        return [{"applicability_id": a["applicability_id"],
                 "condition_expression": copy.deepcopy(a["conditions"]),
                 "applicable_result": a["result"], "reason": a["reason"]}
                for a in self._appl if a["rule_version_id"] == rule_version_id]

    def _registry_entry(self, check_id: str) -> dict[str, Any]:
        r = self._rules[check_id]
        return {"check_id": r["rule_id"], "title": r["title"], "field_name": r["field"],
                "check_type": r["check_type"], "mandatory_default": r["mandatory_default"],
                "scoring_category": r["scoring_category"], "description": r["requirement"],
                "rule_number": r["rule_number"], "sub_rule": r["sub_rule"],
                "clause": r["clause"], "source_reference": r["source_reference"]}

    def checks_for(self, rule_version_id: str) -> list[dict[str, Any]]:
        for v in self._versions:
            if v["rule_version_id"] == rule_version_id:
                return [self._registry_entry(v["check_id"])]
        return []

    def list_checks(self) -> list[dict[str, Any]]:
        return [self._registry_entry(cid) for cid in sorted(self._rules)]

    def rule_detail(self, check_id: str) -> dict[str, Any] | None:
        if check_id not in self._rules:
            return None
        versions = sorted(
            (v for v in self._versions if v["check_id"] == check_id),
            key=lambda v: v["effective_from"])
        return {"check": self._registry_entry(check_id), "versions": versions,
                "applicability": [a for a in self._appl if a["check_id"] == check_id]}

    def scoring_policy(self, code: str) -> dict[str, Any] | None:
        for p in self.master["scoring_policy"]:
            if p["policy_id"] == code:
                return {"policy_id": p["policy_id"], "name": p["name"],
                        "version": p["version"], "calc_method": p["calculation"]["method"],
                        "review_handling": p["calculation"]["review_handling"],
                        "not_applicable_handling": p["calculation"]["not_applicable_handling"],
                        "weights": copy.deepcopy(p["rules"])}
        return None

    # -------------------------------------------------------- operational ---
    def create_inspection(self, data: dict[str, Any]) -> dict[str, Any]:
        row = {"inspection_id": str(uuid.uuid4()), "status": "OPEN",
               "created_at": datetime.now(timezone.utc).isoformat(), **data}
        row.setdefault("inspection_date", date.today().isoformat())
        self.inspections[row["inspection_id"]] = row
        return copy.deepcopy(row)

    def get_inspection(self, inspection_id: str) -> dict[str, Any] | None:
        row = self.inspections.get(inspection_id)
        return copy.deepcopy(row) if row else None

    def list_inspections(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(r) for r in self.inspections.values()]

    def add_product(self, inspection_id: str, data: dict[str, Any]) -> dict[str, Any]:
        pid = str(uuid.uuid4())
        row: dict[str, Any] = {"inspected_product_id": pid, "inspection_id": inspection_id}
        extras: dict[str, Any] = {}
        for key, value in data.items():
            if key in self.PRODUCT_COLS:
                row[key] = value
            elif key not in ("inspection_id", "inspected_product_id"):
                extras[key] = value
        row.setdefault("is_prepackaged", True)
        row.setdefault("imported", False)
        row.setdefault("ecommerce", False)
        self.products[pid] = row
        if extras:  # manual declarations -> manual evidence + declaration rows
            ev_id = str(uuid.uuid4())
            self.evidence[ev_id] = {
                "evidence_id": ev_id, "inspection_id": inspection_id, "product_id": pid,
                "evidence_type": "OTHER", "source": "manual-entry",
                "description": "Manually supplied product declaration (not OCR)."}
            for field, value in extras.items():
                did = str(uuid.uuid4())
                self.declarations[did] = {
                    "extraction_id": did, "evidence_id": ev_id, "field_name": field,
                    "extracted_value": None if value is None else str(value),
                    "normalized_value": None, "confidence": None,
                    "ocr_engine": "manual-entry"}
        out = copy.deepcopy(row)
        out["product_id"] = pid
        return out

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        row = self.products.get(product_id)
        if not row:
            return None
        out = copy.deepcopy(row)
        out["product_id"] = row["inspected_product_id"]
        return out

    def products_for(self, inspection_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(p) for p in self.products.values()
                if p.get("inspection_id") == inspection_id]

    def declarations_for(self, product_id: str) -> list[dict[str, Any]]:
        ev_ids = {e["evidence_id"] for e in self.evidence.values()
                  if e.get("product_id") == product_id}
        rows = [d for d in self.declarations.values() if d["evidence_id"] in ev_ids]
        latest: dict[str, dict] = {}
        for d in rows:  # last write wins per field (extraction order)
            latest[d["field_name"]] = d
        return [copy.deepcopy(d) for d in latest.values()]

    def save_result(self, row: dict[str, Any]) -> dict[str, Any]:
        row = {"compliance_result_id": str(uuid.uuid4()),
               "checked_at": datetime.now(timezone.utc).isoformat(), **row}
        self.results[row["compliance_result_id"]] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def results_for(self, inspection_id: str,
                    product_id: str | None = None) -> list[dict[str, Any]]:
        out = []
        for r in self.results.values():
            if r.get("inspection_id") != inspection_id:
                continue
            if product_id is not None and r.get("product_id") != product_id:
                continue
            row = copy.deepcopy(r)
            ver = next((v for v in self._versions
                        if v["rule_version_id"] == r.get("rule_version_id")), None)
            if ver:
                rule = self._rules[ver["check_id"]]
                row["check_id"] = ver["check_id"]
                row["sub_rule"] = rule["sub_rule"]
                row["clause"] = rule["clause"]
                row["rule_number"] = rule["rule_number"]
                row["legal_text_or_paraphrase"] = ver["legal_text_or_paraphrase"]
                row["source_title"] = ver["provenance"]["source_title"]
                row["authenticity_status"] = ver["provenance"]["authenticity_status"]
                row["source_url"] = ver["provenance"].get("source_url")
                row["effective_from"] = ver["effective_from"]
                row["effective_to"] = ver["effective_to"]
            out.append(row)
        return out

    def create_violation(self, row: dict[str, Any]) -> dict[str, Any]:
        row = {"violation_id": str(uuid.uuid4()), "inspector_status": "PENDING",
               "created_at": datetime.now(timezone.utc).isoformat(), **row}
        self.violations[row["violation_id"]] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def violations_for(self, inspection_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(v) for v in self.violations.values()
                if v.get("inspection_id") == inspection_id]

    def verify_violation(self, violation_id: str, status: str,
                         inspector_id: str = "",
                         remarks: str = "") -> dict[str, Any] | None:
        allowed = {"CONFIRMED", "REJECTED", "REQUIRES_REVIEW", "PENDING"}
        if status not in allowed:
            raise ValueError(f"invalid inspector status: {status}")
        row = self.violations.get(violation_id)
        if not row:
            return None
        row["inspector_status"] = status
        row["inspector_id"] = inspector_id
        row["verification_date"] = date.today().isoformat()
        row["inspector_remarks"] = remarks
        return copy.deepcopy(row)

    def audit(self, actor: str, action: str, entity: str,
              entity_id: str, detail: dict[str, Any] | None = None) -> None:
        self.audit_log.append({"user_id": actor, "action": action, "entity_type": entity,
                               "entity_id": entity_id, "new_value": detail or {}})
