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

from app.repositories._util import meta_confidence as _meta_confidence

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
        self.complaints: dict[str, dict] = {}
        self.complaint_events: dict[str, dict] = {}
        self.suggestions: dict[str, dict] = {}
        self.suggestion_events: dict[str, dict] = {}
        self.notifications: dict[str, dict] = {}
        self.enforcement: dict[str, dict] = {}
        # Stage 2 declaration corrections (migration 005): append-only.
        self.corrections: dict[str, dict] = {}
        # sync-created (never mutates master seed = legal history preserved)
        self._custom_checks: dict[str, dict] = {}
        self._custom_versions: list[dict] = []
        self._custom_appl: list[dict] = []
        self._weight_overrides: dict[str, dict] = {}

    # ------------------------------------------------------------- legal ---
    def _all_rules(self) -> dict[str, dict]:
        return {**self._rules, **self._custom_checks}

    def _all_versions(self) -> list[dict]:
        return [*self._versions, *self._custom_versions]

    def _all_appl(self) -> list[dict]:
        return [*self._appl, *self._custom_appl]

    def active_rule_versions(self, as_of: str) -> list[dict[str, Any]]:
        # Temporal selection: the version whose validity range covers as_of.
        # SUPERSEDED versions are selectable for historical dates (they were
        # in force then); DRAFT / NOT_YET_IN_FORCE / REPEALED never are.
        best: dict[str, dict] = {}
        rules = self._all_rules()
        for v in self._all_versions():
            if v["status"] not in ("IN_FORCE", "SUPERSEDED"):
                continue
            if not (v["effective_from"] <= as_of):
                continue
            if v["effective_to"] is not None and not (v["effective_to"] > as_of):
                continue
            rule = rules[v["check_id"]]
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
                for a in self._all_appl() if a["rule_version_id"] == rule_version_id]

    def _registry_entry(self, check_id: str) -> dict[str, Any]:
        r = self._all_rules()[check_id]
        return {"check_id": r.get("rule_id", check_id), "title": r["title"],
                "field_name": r["field"], "check_type": r["check_type"],
                "mandatory_default": r["mandatory_default"],
                "scoring_category": r["scoring_category"],
                "description": r.get("requirement", r.get("description", "")),
                "rule_number": r["rule_number"], "sub_rule": r["sub_rule"],
                "clause": r["clause"],
                "source_reference": r.get("source_reference") or
                f"Rule {r['rule_number']}({r['sub_rule']}){r['clause'] or ''}"}

    def checks_for(self, rule_version_id: str) -> list[dict[str, Any]]:
        for v in self._all_versions():
            if v["rule_version_id"] == rule_version_id:
                return [self._registry_entry(v["check_id"])]
        return []

    def list_checks(self) -> list[dict[str, Any]]:
        return [self._registry_entry(cid) for cid in sorted(self._all_rules())]

    def rule_detail(self, check_id: str) -> dict[str, Any] | None:
        if check_id not in self._all_rules():
            return None
        versions = sorted(
            (v for v in self._all_versions() if v["check_id"] == check_id),
            key=lambda v: v["effective_from"])
        return {"check": self._registry_entry(check_id), "versions": versions,
                "applicability": [a for a in self._all_appl()
                                  if a["check_id"] == check_id]}

    def scoring_policy(self, code: str) -> dict[str, Any] | None:
        for p in self.master["scoring_policy"]:
            if p["policy_id"] == code:
                weights = copy.deepcopy(p["rules"])
                by_check = {w["check_id"]: w for w in weights}
                by_check.update(copy.deepcopy(self._weight_overrides))
                return {"policy_id": p["policy_id"], "name": p["name"],
                        "version": p["version"], "calc_method": p["calculation"]["method"],
                        "review_handling": p["calculation"]["review_handling"],
                        "not_applicable_handling": p["calculation"]["not_applicable_handling"],
                        "weights": list(by_check.values())}
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
            elif key not in ("inspection_id", "inspected_product_id", "field_meta"):
                extras[key] = value
        # field_meta carries reviewed provenance per extra field, e.g.
        # {"mrp": {"ocr_engine": "rapidocr", "confidence": 0.94}}.
        # Absent/edited fields stay manual-entry with NULL confidence.
        meta = data.get("field_meta") or {}
        row.setdefault("is_prepackaged", True)
        row.setdefault("imported", False)
        row.setdefault("ecommerce", False)
        self.products[pid] = row
        # Mirror rows: registry-field declaration rows for the SAME reviewed
        # values (server-side strings, never client values). Only for column
        # fields the reviewer left untouched (non-manual meta) — this is how
        # OCR provenance/confidence survives for manufacturer, quantity,
        # dates, etc. without schema changes. Facts merge lets declaration
        # rows override columns; values are identical by contract.
        from app.engine.facts import COLUMN_MAP

        mirrors = [(COLUMN_MAP[col], row[col], meta.get(COLUMN_MAP[col]) or {})
                   for col in COLUMN_MAP
                   if col in row and row[col] is not None
                   and (meta.get(COLUMN_MAP[col]) or {}).get("ocr_engine",
                                                             "manual-entry")
                   != "manual-entry"]
        # Uncertainty mirrors: OCR attempted a registry field (field_meta
        # carries a non-manual ocr_engine) but the reviewer left it blank.
        # A NULL-valued declaration row with measured low/zero confidence
        # lets the engine's existing low-confidence gate route the check to
        # NEEDS_REVIEW ("OCR looked, found nothing readable") instead of
        # reporting a legal FAIL for what may be mere OCR uncertainty.
        # Officer-confirmed blanks (manual-entry / no meta) still FAIL.
        uncertain = [(field, meta.get(field) or {})
                     for col, field in COLUMN_MAP.items()
                     if (meta.get(field) or {}).get("ocr_engine",
                                                    "manual-entry")
                     != "manual-entry"
                     and field not in {m[0] for m in mirrors}
                     and field not in extras
                     and (col not in row or row[col] is None)]
        if extras or mirrors or uncertain:
            mixed = bool(mirrors) or bool(uncertain) or any(
                (meta.get(f) or {}).get("ocr_engine", "manual-entry")
                != "manual-entry" for f in extras)
            ev_id = str(uuid.uuid4())
            self.evidence[ev_id] = {
                "evidence_id": ev_id, "inspection_id": inspection_id, "product_id": pid,
                "evidence_type": "OTHER",
                "source": "reviewed-declaration" if mixed else "manual-entry",
                "description": ("Reviewed declaration: unedited OCR-extracted fields "
                                "plus manual entries/confirmations."
                                if mixed else
                                "Manually supplied product declaration (not OCR).")}
            for field, value in extras.items():
                m = meta.get(field) or {}
                did = str(uuid.uuid4())
                self.declarations[did] = {
                    "extraction_id": did, "evidence_id": ev_id,
                    "field_name": field,
                    "extracted_value": None if value is None else str(value),
                    "normalized_value": None,
                    "confidence": _meta_confidence(m.get("confidence")),
                    "ocr_engine": str(m.get("ocr_engine") or "manual-entry")}
            for field, value, m in mirrors:
                did = str(uuid.uuid4())
                self.declarations[did] = {
                    "extraction_id": did, "evidence_id": ev_id,
                    "field_name": field,
                    "extracted_value": str(value),
                    "normalized_value": None,
                    "confidence": _meta_confidence(m.get("confidence")),
                    "ocr_engine": str(m.get("ocr_engine") or "manual-entry")}
            for field, m in uncertain:
                did = str(uuid.uuid4())
                self.declarations[did] = {
                    "extraction_id": did, "evidence_id": ev_id,
                    "field_name": field,
                    # NULL value + measured OCR confidence: OCR uncertainty,
                    # never a confirmed declaration.
                    "extracted_value": None,
                    "normalized_value": None,
                    "confidence": _meta_confidence(m.get("confidence")),
                    "ocr_engine": str(m.get("ocr_engine") or "manual-entry")}
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
            ver = next((v for v in self._all_versions()
                        if v["rule_version_id"] == r.get("rule_version_id")), None)
            if ver:
                rule = self._all_rules()[ver["check_id"]]
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

    def get_violation(self, violation_id: str) -> dict[str, Any] | None:
        row = self.violations.get(violation_id)
        return copy.deepcopy(row) if row else None

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

    def audit_list(self, entity: str, entity_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(a) for a in self.audit_log
                if a.get("entity_type") == entity and a.get("entity_id") == entity_id]

    # --------------------------------------- declaration corrections ---
    def create_correction(self, data: dict[str, Any]) -> dict[str, Any]:
        """Append-only correction row; original evidence never overwritten."""
        if not str(data.get("field_key", "") or "").strip():
            raise ValueError("field_key is required")
        now = datetime.now(timezone.utc).isoformat()
        row = {"id": str(uuid.uuid4()),
               "inspection_id": data.get("inspection_id", ""),
               "product_id": data.get("product_id", ""),
               "field_key": str(data.get("field_key", "")).strip(),
               "original_value": data.get("original_value"),
               "corrected_value": data.get("corrected_value"),
               "original_status": data.get("original_status",
                                           "NEEDS_REVIEW"),
               "original_confidence": data.get("original_confidence"),
               "source": data.get("source", "officer-review"),
               "evidence_snapshot": copy.deepcopy(
                   data.get("evidence_snapshot") or {}),
               "officer_user_id": data.get("officer_user_id", ""),
               "created_at": now,
               "verified": False,
               "verified_by": "",
               "verified_at": None,
               "correction_reason": data.get("correction_reason", "")}
        self.corrections[row["id"]] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def list_corrections(self, inspection_id: str,
                         product_id: str) -> list[dict[str, Any]]:
        rows = [c for c in self.corrections.values()
                if c.get("inspection_id") == inspection_id
                and c.get("product_id") == product_id]
        rows.sort(key=lambda r: r.get("created_at", ""))
        return [copy.deepcopy(r) for r in rows]

    def verify_correction(self, correction_id: str, verified: bool,
                          verifier_id: str = "") -> dict[str, Any] | None:
        row = self.corrections.get(correction_id)
        if not row:
            return None
        row["verified"] = bool(verified)
        row["verified_by"] = verifier_id or ""
        row["verified_at"] = datetime.now(timezone.utc).isoformat() \
            if verified else None
        return copy.deepcopy(row)

    def all_corrections(self) -> list[dict[str, Any]]:
        rows = sorted(self.corrections.values(),
                      key=lambda r: r.get("created_at", ""))
        return [copy.deepcopy(r) for r in rows]

    # ---------------------------------------------------- notifications ---
    def create_notification(self, data: dict[str, Any]) -> dict[str, Any]:
        row = {"notification_id": str(uuid.uuid4()), "read": False,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "audience": data.get("audience", ""),
               "type": data.get("type", ""), "title": data.get("title", ""),
               "body": data.get("body", ""), "link": data.get("link", "")}
        self.notifications[row["notification_id"]] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def list_notifications(self, audiences: list[str]) -> list[dict[str, Any]]:
        wanted = set(audiences or [])
        rows = [n for n in self.notifications.values() if n.get("audience") in wanted]
        rows.sort(key=lambda n: n.get("created_at", ""), reverse=True)
        return [copy.deepcopy(n) for n in rows[:50]]

    def mark_notification_read(self, notification_id: str,
                               audiences: list[str]) -> dict[str, Any] | None:
        row = self.notifications.get(notification_id)
        if not row or row.get("audience") not in set(audiences or []):
            return None
        row["read"] = True
        return copy.deepcopy(row)

    def mark_notifications_read(self, audiences: list[str]) -> int:
        wanted = set(audiences or [])
        count = 0
        for row in self.notifications.values():
            if row.get("audience") in wanted and not row.get("read"):
                row["read"] = True
                count += 1
        return count

    # ------------------------------------------------------- complaints ---
    def create_complaint(self, data: dict[str, Any]) -> dict[str, Any]:
        from app.models.lifecycle import STATUSES

        status = data.get("status", "SUBMITTED")
        if status not in STATUSES:
            raise ValueError(f"invalid complaint status: {status}")
        now = datetime.now(timezone.utc).isoformat()
        row = {"complaint_id": str(uuid.uuid4()), "status": status,
               "created_at": now, "updated_at": now, **data}
        row["status"] = status
        self.complaints[row["complaint_id"]] = copy.deepcopy(row)
        self._complaint_event(row["complaint_id"], "CREATED", None, status,
                              data.get("reporter_id", ""), "Complaint submitted.")
        return copy.deepcopy(row)

    def _complaint_event(self, complaint_id: str, event_type: str,
                         from_status: str | None, to_status: str | None,
                         actor_id: str, note: str) -> None:
        eid = str(uuid.uuid4())
        self.complaint_events[eid] = {
            "event_id": eid, "complaint_id": complaint_id, "event_type": event_type,
            "from_status": from_status, "to_status": to_status,
            "actor_id": actor_id, "note": note,
            "created_at": datetime.now(timezone.utc).isoformat()}

    def get_complaint(self, complaint_id: str) -> dict[str, Any] | None:
        row = self.complaints.get(complaint_id)
        return copy.deepcopy(row) if row else None

    def list_complaints(self, reporter_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.complaints.values()
        if reporter_id is not None:
            rows = [r for r in rows if r.get("reporter_id") == reporter_id]
        return [copy.deepcopy(r) for r in sorted(
            rows, key=lambda r: r.get("created_at", ""), reverse=True)]

    def transition_complaint(self, complaint_id: str, to_status: str,
                             actor_id: str = "",
                             note: str = "") -> dict[str, Any] | None:
        from app.models.lifecycle import STATUSES, allowed

        if to_status not in STATUSES:
            raise ValueError(f"invalid complaint status: {to_status}")
        row = self.complaints.get(complaint_id)
        if not row:
            return None
        if not allowed(row["status"], to_status):
            raise ValueError(
                f"illegal transition {row['status']} -> {to_status}")
        from_status = row["status"]
        row["status"] = to_status
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._complaint_event(complaint_id, "STATUS_CHANGE", from_status,
                              to_status, actor_id, note)
        return copy.deepcopy(row)

    def complaint_timeline(self, complaint_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(e) for e in sorted(
            (e for e in self.complaint_events.values()
             if e["complaint_id"] == complaint_id),
            key=lambda e: e["created_at"])]

    # ------------------------------------------- consumer suggestions ---
    def create_suggestion(self, data: dict[str, Any]) -> dict[str, Any]:
        from app.models.lifecycle import (SUGGESTION_CATEGORIES,
                                          SUGGESTION_STATUSES)

        status = data.get("status", "SUBMITTED")
        if status not in SUGGESTION_STATUSES:
            raise ValueError(f"invalid suggestion status: {status}")
        category = data.get("category", "Other")
        if category not in SUGGESTION_CATEGORIES:
            raise ValueError(f"invalid suggestion category: {category}")
        if not str(data.get("title", "")).strip():
            raise ValueError("suggestion title is required")
        now = datetime.now(timezone.utc).isoformat()
        row = {"suggestion_id": str(uuid.uuid4()), "status": status,
               "reviewed_by": "", "officer_note": "",
               "created_at": now, "updated_at": now, **data}
        row["status"] = status
        self.suggestions[row["suggestion_id"]] = copy.deepcopy(row)
        self._suggestion_event(row["suggestion_id"], "CREATED", None,
                               status, data.get("consumer_user_id", ""),
                               "Suggestion submitted.")
        return copy.deepcopy(row)

    def _suggestion_event(self, suggestion_id: str, event_type: str,
                          from_status: str | None, to_status: str | None,
                          actor_id: str, note: str) -> None:
        eid = str(uuid.uuid4())
        self.suggestion_events[eid] = {
            "event_id": eid, "suggestion_id": suggestion_id,
            "event_type": event_type, "from_status": from_status,
            "to_status": to_status, "actor_id": actor_id, "note": note,
            "created_at": datetime.now(timezone.utc).isoformat()}

    def get_suggestion(self, suggestion_id: str) -> dict[str, Any] | None:
        row = self.suggestions.get(suggestion_id)
        return copy.deepcopy(row) if row else None

    def list_suggestions(self, consumer_user_id: str | None = None
                         ) -> list[dict[str, Any]]:
        rows = self.suggestions.values()
        if consumer_user_id is not None:
            rows = [r for r in rows
                    if r.get("consumer_user_id") == consumer_user_id]
        return [copy.deepcopy(r) for r in sorted(
            rows, key=lambda r: r.get("created_at", ""), reverse=True)]

    def update_suggestion(self, suggestion_id: str, to_status: str,
                          actor_id: str = "",
                          note: str = "") -> dict[str, Any] | None:
        from app.models.lifecycle import (SUGGESTION_STATUSES,
                                          suggestion_allowed)

        if to_status not in SUGGESTION_STATUSES:
            raise ValueError(f"invalid suggestion status: {to_status}")
        row = self.suggestions.get(suggestion_id)
        if not row:
            return None
        if not suggestion_allowed(row["status"], to_status):
            raise ValueError(
                f"illegal transition {row['status']} -> {to_status}")
        from_status = row["status"]
        row["status"] = to_status
        row["reviewed_by"] = actor_id
        if note:
            row["officer_note"] = note
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._suggestion_event(suggestion_id, "STATUS_CHANGE", from_status,
                               to_status, actor_id, note)
        return copy.deepcopy(row)

    def suggestion_timeline(self, suggestion_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(e) for e in sorted(
            (e for e in self.suggestion_events.values()
             if e["suggestion_id"] == suggestion_id),
            key=lambda e: e["created_at"])]

    # --------------------------------- batched reads, same semantics ---
    def products_for_many(
            self, inspection_ids: list[str]) -> dict[str, list[dict]]:
        return {iid: self.products_for(iid) for iid in inspection_ids}

    def results_for_many(
            self, inspection_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for iid in inspection_ids:
            out.extend(self.results_for(iid))
        return out

    def violations_for_many(
            self, inspection_ids: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for iid in inspection_ids:
            out.extend(self.violations_for(iid))
        return out

    # ------------------------------------------------- officer console ---
    def _severity_for(self, check_id: str) -> str:
        pol = self.scoring_policy("DEFAULT-2026") or {}
        for w in pol.get("weights", []):
            if w.get("check_id") == check_id:
                return str(w.get("severity", "MEDIUM")).upper()
        return "MEDIUM"

    def _queue_item(self, v: dict) -> dict[str, Any]:
        prod = self.products.get(v.get("product_id", ""), {})
        insp = self.inspections.get(v.get("inspection_id", ""), {})
        check_id = ""
        for r in self.results.values():
            if r.get("compliance_result_id") == v.get("compliance_result_id"):
                for ver in self._all_versions():
                    if ver["rule_version_id"] == r.get("rule_version_id"):
                        check_id = ver["check_id"]
        return {
            "violation_id": v.get("violation_id"),
            "inspection_id": v.get("inspection_id"),
            "product_id": v.get("product_id"),
            "product_name": prod.get("product_name", ""),
            "category": prod.get("category", ""),
            "business_name": insp.get("business_name", ""),
            "state": insp.get("state", ""), "district": insp.get("district", ""),
            "violation_type": v.get("violation_type", ""),
            "description": v.get("description", ""),
            "check_id": check_id, "severity": self._severity_for(check_id),
            "inspector_status": v.get("inspector_status", "PENDING"),
            "inspector_id": v.get("inspector_id", ""),
            "created_at": v.get("created_at", ""),
            "verification_date": v.get("verification_date"),
        }

    def violations_queue(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        items = [self._queue_item(v) for v in self.violations.values()]
        if filters.get("status"):
            items = [i for i in items if i["inspector_status"] == filters["status"]]
        if filters.get("severity"):
            items = [i for i in items
                     if i["severity"] == str(filters["severity"]).upper()]
        if filters.get("violation_type"):
            items = [i for i in items if i["violation_type"] == filters["violation_type"]]
        if filters.get("check_id"):
            items = [i for i in items if i["check_id"] == filters["check_id"]]
        if filters.get("category"):
            items = [i for i in items if i["category"] == filters["category"]]
        if filters.get("state"):
            items = [i for i in items if i["state"] == filters["state"]]
        if filters.get("date_from"):
            items = [i for i in items if (i["created_at"] or "")[:10] >= filters["date_from"]]
        if filters.get("date_to"):
            items = [i for i in items if (i["created_at"] or "")[:10] <= filters["date_to"]]
        return sorted(items, key=lambda i: i["created_at"], reverse=True)

    def officer_stats(self) -> dict[str, Any]:
        from datetime import date as _date

        items = [self._queue_item(v) for v in self.violations.values()]
        total = len(items)
        pending = [i for i in items if i["inspector_status"] == "PENDING"]
        high = [i for i in pending if i["severity"] == "HIGH"]
        confirmed = [i for i in items if i["inspector_status"] == "CONFIRMED"]
        resolved = [i for i in items
                    if i["inspector_status"] in ("CONFIRMED", "REJECTED")]
        deltas = []
        for v in self.violations.values():
            if v.get("verification_date") and v.get("created_at"):
                try:
                    d0 = _date.fromisoformat(v["created_at"][:10])
                    d1 = _date.fromisoformat(v["verification_date"][:10])
                    deltas.append((d1 - d0).days)
                except ValueError:
                    pass
        active_rules = len(self.active_rule_versions(_date.today().isoformat()))
        complaints = list(self.complaints.values())
        return {
            "awaiting_review": len(pending),
            "high_priority": len(high),
            "action_taken": len(confirmed),
            "resolved": len(resolved),
            "total_violations": total,
            "resolution_rate": round(len(resolved) / total, 3) if total else 0.0,
            "avg_response_days": round(sum(deltas) / len(deltas), 1) if deltas else None,
            "active_rules": active_rules,
            "open_complaints": len([c for c in complaints if c["status"] != "CLOSED"]),
            "total_complaints": len(complaints),
        }

    def case_detail(self, violation_id: str) -> dict[str, Any] | None:
        v = self.violations.get(violation_id)
        if not v:
            return None
        pid, iid = v.get("product_id", ""), v.get("inspection_id", "")
        findings = [copy.deepcopy(r) for r in self.results.values()
                    if r.get("product_id") == pid and r.get("inspection_id") == iid]
        rule_ids = {self._check_of_result(r) for r in findings}
        return {
            "violation": copy.deepcopy(v),
            "product": copy.deepcopy(self.products.get(pid)),
            "inspection": copy.deepcopy(self.inspections.get(iid)),
            "findings": findings,
            "evidence": [copy.deepcopy(e) for e in self.evidence.values()
                         if e.get("product_id") == pid],
            "declarations": self.declarations_for(pid),
            "rules": [self.rule_detail(c) for c in sorted(rule_ids) if c],
            "enforcement": [copy.deepcopy(e) for e in self.enforcement.values()
                            if e.get("violation_id") == violation_id],
            "audit": self.audit_list("violation", violation_id),
        }

    def _check_of_result(self, r: dict) -> str:
        for ver in self._all_versions():
            if ver["rule_version_id"] == r.get("rule_version_id"):
                return ver["check_id"]
        return ""

    def evidence_for(self, product_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(e) for e in self.evidence.values()
                if e.get("product_id") == product_id]

    def record_enforcement_action(self, data: dict[str, Any]) -> dict[str, Any]:
        row = {"action_id": str(uuid.uuid4()), "action_status": "INITIATED",
               "action_date": date.today().isoformat(),
               "created_at": datetime.now(timezone.utc).isoformat(), **data}
        self.enforcement[row["action_id"]] = copy.deepcopy(row)
        return copy.deepcopy(row)

    def enforcement_for_violation(self, violation_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(e) for e in self.enforcement.values()
                if e.get("violation_id") == violation_id]

    # ------------------------------------------------------ rule sync ---
    def all_rule_versions(self) -> list[dict[str, Any]]:
        out = []
        for v in self._all_versions():
            rule = self._all_rules()[v["check_id"]]
            out.append({"rule_version_id": v["rule_version_id"],
                        "check_id": v["check_id"],
                        "requirement": v["requirement"],
                        "legal_text_or_paraphrase": v["legal_text_or_paraphrase"],
                        "effective_from": v["effective_from"],
                        "effective_to": v["effective_to"], "status": v["status"],
                        "sub_rule": rule["sub_rule"], "clause": rule["clause"],
                        "rule_number": rule["rule_number"]})
        return out

    def all_applicability(self) -> list[dict[str, Any]]:
        return [{"applicability_id": a.get("applicability_id"),
                 "rule_version_id": a["rule_version_id"],
                 "check_id": a.get("check_id"),
                 "condition_expression": copy.deepcopy(a.get("conditions",
                                                             a.get("condition_expression"))),
                 "applicable_result": a.get("result", a.get("applicable_result")),
                 "reason": a.get("reason", "")}
                for a in self._all_appl()]

    def all_weights(self, policy_id: str) -> list[dict[str, Any]]:
        pol = self.scoring_policy(policy_id)
        return copy.deepcopy(pol["weights"]) if pol else []

    def get_rule_by_number(self, rule_number: str) -> dict[str, Any] | None:
        for r in self._all_rules().values():
            if r.get("rule_number") == rule_number:
                return {"rule_id": f"memory-rule-{rule_number}",
                        "rule_set_name": "Legal Metrology (Packaged Commodities) Rules, 2011",
                        "rule_number": rule_number,
                        "short_title": "Declarations to be made on every package"}
        return None

    def create_legal_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"rule_id": f"memory-{data.get('rule_number')}",
                "rule_set_name": data.get("rule_set_name", ""),
                "rule_number": data.get("rule_number", ""),
                "short_title": data.get("short_title", "")}

    def create_legal_source(self, data: dict[str, Any]) -> dict[str, Any]:
        return {"source_id": str(uuid.uuid4()), **data}

    def insert_registry_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        check_id = entry["check_id"]
        if check_id in self._all_rules():
            raise ValueError(f"registry entry {check_id} already exists")
        self._custom_checks[check_id] = {
            "rule_id": check_id, "title": entry.get("title", ""),
            "field": entry.get("field_name", ""),
            "check_type": entry.get("check_type", "MANUAL_REVIEW"),
            "mandatory_default": bool(entry.get("mandatory_default", False)),
            "scoring_category": entry.get("scoring_category", "OTHER"),
            "requirement": entry.get("description", ""),
            "description": entry.get("description", ""),
            "rule_number": entry.get("rule_number", "6"),
            "sub_rule": entry.get("sub_rule"), "clause": entry.get("clause"),
            "source_reference": entry.get("source_reference") or
            f"Rule {entry.get('rule_number', '6')}({entry.get('sub_rule', '')})"
            f"{entry.get('clause') or ''}"}
        return copy.deepcopy(self._custom_checks[check_id])

    def insert_rule_version(self, data: dict[str, Any]) -> dict[str, Any]:
        # memory equivalent of the exclusion constraint: reject overlaps
        for v in self._all_versions():
            same = (v["check_id"] == data["check_id"])
            if not same:
                continue
            a_from, a_to = v["effective_from"], v.get("effective_to") or "9999-12-31"
            b_from, b_to = data["effective_from"], data.get("effective_to") or "9999-12-31"
            if a_from < b_to and b_from < a_to:
                raise ValueError(
                    f"overlapping version range for {data['check_id']}")
        row = {"rule_version_id": data.get("rule_version_id") or str(uuid.uuid4()),
               "check_id": data["check_id"],
               "requirement": data.get("requirement", ""),
               "legal_text_or_paraphrase": data.get("legal_text_or_paraphrase", ""),
               "effective_from": data["effective_from"],
               "effective_to": data.get("effective_to"),
               "status": data.get("status", "IN_FORCE"),
               "supersedes": data.get("supersedes"),
               "superseded_by": data.get("superseded_by"),
               "provenance": data.get("provenance", {
                   "source_title": data.get("source_title", ""),
                   "source_url": data.get("source_url"),
                   "authenticity_status": data.get("authenticity_status",
                                                   "VERIFICATION_REQUIRED")})}
        self._custom_versions.append(copy.deepcopy(row))
        return copy.deepcopy(row)

    def supersede_version(self, rule_version_id: str,
                          effective_to: str) -> dict[str, Any] | None:
        for v in self._custom_versions:
            if v["rule_version_id"] == rule_version_id:
                v["effective_to"] = effective_to
                v["status"] = "SUPERSEDED"
                return copy.deepcopy(v)
        for v in self._versions:
            if v["rule_version_id"] == rule_version_id:
                # seed history is immutable: report a copy, do not mutate
                out = copy.deepcopy(v)
                out["effective_to"] = effective_to
                out["status"] = "SUPERSEDED"
                return out
        return None

    def insert_applicability(self, data: dict[str, Any]) -> dict[str, Any]:
        row = {"applicability_id": data.get("applicability_id") or str(uuid.uuid4()),
               "rule_version_id": data["rule_version_id"],
               "check_id": data.get("check_id"),
               "conditions": copy.deepcopy(data.get("condition_expression", {})),
               "result": data.get("applicable_result", "CONDITIONAL"),
               "reason": data.get("reason", "")}
        self._custom_appl.append(copy.deepcopy(row))
        return copy.deepcopy(row)

    def upsert_weight(self, policy_id: str, check_id: str,
                      weight: float, severity: str) -> dict[str, Any]:
        row = {"check_id": check_id, "weight": float(weight),
               "severity": severity}
        self._weight_overrides[check_id] = copy.deepcopy(row)
        return copy.deepcopy(row)
