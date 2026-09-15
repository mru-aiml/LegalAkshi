"""PRODUCTION repository — PostgreSQL is the source of truth.

Fails fast: empty/unreachable DATABASE_URL raises instead of silently
serving test data. All SQL is parameterized.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

log = logging.getLogger("legalakshi.postgres")

# inspected_products columns we persist (authoritative schema + v2 patch cols)
PRODUCT_COLS = (
    "product_name", "brand", "manufacturer", "packer", "importer",
    "country_of_origin", "category", "subcategory", "is_prepackaged",
    "intended_consumer_type", "quantity", "quantity_unit", "quantity_type",
    "manufacturing_date", "packing_date", "import_date", "best_before",
    "use_by", "imported", "ecommerce", "product_identifier", "barcode",
    "qr_code", "source_listing_url", "combination_package", "group_package",
    "multi_piece_package", "net_quantity_value", "net_quantity_unit",
)

DATE_COLS = {"manufacturing_date", "packing_date", "import_date",
             "best_before", "use_by"}


def parse_date(value: Any) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip()
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%Y", "%m-%Y", "%Y/%m", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


class PostgresRepo:
    kind = "postgres"

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError(
                "DATABASE_URL must be set for the production API. "
                "Unit tests use the in-memory test repository instead.")
        self._dsn = dsn

    # ------------------------------------------------------------ driver ---
    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, row_factory=dict_row)

    def ping(self) -> bool:
        try:
            return bool(self.fetch_one("SELECT 1 AS ok"))
        except Exception as exc:
            log.error("postgres ping failed: %s", exc)
            return False

    def fetch_all(self, query: str, params: list | None = None) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params or [])
            return list(cur.fetchall())

    def fetch_one(self, query: str, params: list | None = None) -> dict | None:
        rows = self.fetch_all(query, params)
        return rows[0] if rows else None

    def execute(self, query: str, params: list | None = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params or [])
            conn.commit()

    # --------------------------------------------------------------- legal ---
    def active_rule_versions(self, as_of: str) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT rv.rule_version_id::text AS rule_version_id,
                   rv.check_id, rv.sub_rule, rv.clause,
                   rv.requirement, rv.legal_text_or_paraphrase,
                   lr.rule_number, lr.short_title AS rule_title,
                   ls.title AS source_title, ls.url AS source_url,
                   ls.authenticity_status,
                   rv.effective_from::text AS effective_from,
                   rv.effective_to::text AS effective_to,
                   rv.status,
                   rv.supersedes::text AS supersedes,
                   rv.superseded_by::text AS superseded_by
            FROM rule_versions rv
            JOIN legal_rules lr ON lr.rule_id = rv.rule_id
            JOIN legal_sources ls ON ls.source_id = rv.source_id
            WHERE rv.effective_from <= %s::date
              AND (rv.effective_to IS NULL OR rv.effective_to > %s::date)
              AND rv.status IN ('IN_FORCE', 'SUPERSEDED')
            ORDER BY rv.check_id, rv.effective_from DESC
            """,
            [as_of, as_of],
        )
        seen: dict[str, dict] = {}
        for row in rows:  # exclusion constraint => 1 per check; latest wins
            row["source_reference"] = (
                f"Rule {row['rule_number']}({row['sub_rule']}){row['clause'] or ''}")
            seen.setdefault(row["check_id"], row)
        return list(seen.values())

    def applicability_for(self, rule_version_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT applicability_id::text AS applicability_id,
                      condition_expression, applicable_result, reason
               FROM rule_applicability WHERE rule_version_id = %s::uuid""",
            [rule_version_id],
        )

    def checks_for(self, rule_version_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT reg.check_id, reg.title, reg.field_name, reg.check_type,
                      reg.mandatory_default, reg.scoring_category, reg.description,
                      lr.rule_number, reg.sub_rule, reg.clause
               FROM rule_versions rv
               JOIN engine_check_registry reg ON reg.check_id = rv.check_id
               JOIN legal_rules lr ON lr.rule_id = reg.rule_id
               WHERE rv.rule_version_id = %s::uuid""",
            [rule_version_id],
        )

    def list_checks(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT reg.check_id, reg.title, reg.field_name, reg.check_type,
                      reg.mandatory_default, reg.scoring_category, reg.description,
                      lr.rule_number, reg.sub_rule, reg.clause
               FROM engine_check_registry reg
               JOIN legal_rules lr ON lr.rule_id = reg.rule_id
               ORDER BY reg.check_id"""
        )

    def rule_detail(self, check_id: str) -> dict[str, Any] | None:
        checks = self.fetch_all(
            """SELECT reg.check_id, reg.title, reg.field_name, reg.check_type,
                      reg.mandatory_default, reg.scoring_category, reg.description,
                      lr.rule_number, reg.sub_rule, reg.clause
               FROM engine_check_registry reg
               JOIN legal_rules lr ON lr.rule_id = reg.rule_id
               WHERE reg.check_id = %s""",
            [check_id],
        )
        if not checks:
            return None
        versions = self.fetch_all(
            """SELECT rule_version_id::text, requirement, legal_text_or_paraphrase,
                      effective_from::text AS effective_from,
                      effective_to::text AS effective_to, status
               FROM rule_versions WHERE check_id = %s ORDER BY effective_from""",
            [check_id],
        )
        appl = self.fetch_all(
            """SELECT ra.applicability_id::text, ra.condition_expression,
                      ra.applicable_result, ra.reason
               FROM rule_applicability ra
               JOIN rule_versions rv USING (rule_version_id)
               WHERE rv.check_id = %s""",
            [check_id],
        )
        return {"check": checks[0], "versions": versions, "applicability": appl}

    def scoring_policy(self, code: str) -> dict[str, Any] | None:
        pol = self.fetch_one(
            """SELECT policy_id, name, version, calc_method, review_handling,
                      not_applicable_handling
               FROM scoring_policies WHERE policy_id = %s AND active""",
            [code],
        )
        if not pol:
            return None
        pol["weights"] = self.fetch_all(
            "SELECT check_id, weight, severity FROM scoring_policy_weights"
            " WHERE policy_id = %s",
            [code],
        )
        return pol

    # ---------------------------------------------------------- operational ---
    def create_inspection(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO inspections
               (inspector_id, inspector_name, department, state, district,
                premises_id, business_id, business_name, inspection_type,
                inspection_date, inspection_time, location, remarks)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::date,%s,%s,%s)
               RETURNING inspection_id::text AS inspection_id, status,
                         inspection_date::text AS inspection_date""",
            [data.get("inspector_id", ""), data.get("inspector_name", ""),
             data.get("department"), data.get("state"), data.get("district"),
             data.get("premises_id"), data.get("business_id"),
             data.get("business_name", ""), data.get("inspection_type"),
             data.get("inspection_date") or date.today().isoformat(),
             data.get("inspection_time"), data.get("location"), data.get("remarks")],
        )
        assert row is not None
        return {**data, **row}

    def get_inspection(self, inspection_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """SELECT inspection_id::text AS inspection_id, inspector_id,
                      inspector_name, department, state, district, business_id,
                      business_name, inspection_type,
                      inspection_date::text AS inspection_date, location, status
               FROM inspections WHERE inspection_id = %s::uuid""",
            [inspection_id],
        )

    def list_inspections(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT inspection_id::text AS inspection_id, inspector_name,
                      business_name, inspection_date::text AS inspection_date,
                      inspection_type, status
               FROM inspections ORDER BY created_at DESC"""
        )

    def add_product(self, inspection_id: str, data: dict[str, Any]) -> dict[str, Any]:
        cols = [c for c in PRODUCT_COLS if c in data]
        values: list[Any] = []
        placeholders: list[str] = []
        for c in cols:
            values.append(parse_date(data[c]) if c in DATE_COLS else data[c])
            placeholders.append("%s::date" if c in DATE_COLS else "%s")
        insert_cols = ["inspection_id", *cols]
        row = self.fetch_one(
            f"INSERT INTO inspected_products ({', '.join(insert_cols)}) VALUES "
            f"(%s::uuid{', ' + ', '.join(placeholders) if placeholders else ''}) "
            "RETURNING inspected_product_id::text AS product_id",
            [inspection_id, *values],
        )
        assert row is not None
        pid = row["product_id"]
        extras = {k: v for k, v in data.items()
                  if k not in PRODUCT_COLS and k not in ("inspection_id", "product_id")}
        if extras:
            ev = self.fetch_one(
                """INSERT INTO inspection_evidence
                   (inspection_id, product_id, evidence_type, description, source)
                   VALUES (%s::uuid, %s::uuid, 'OTHER',
                           'Manually supplied product declaration (not OCR).',
                           'manual-entry')
                   RETURNING evidence_id::text AS evidence_id""",
                [inspection_id, pid],
            )
            assert ev is not None
            for field, value in extras.items():
                self.execute(
                    """INSERT INTO extracted_declarations
                       (evidence_id, field_name, extracted_value, ocr_engine)
                       VALUES (%s::uuid, %s, %s, 'manual-entry')""",
                    [ev["evidence_id"], field,
                     None if value is None else str(value)],
                )
        product = self.get_product(pid) or {}
        product["product_id"] = pid
        return product

    def get_product(self, product_id: str) -> dict[str, Any] | None:
        row = self.fetch_one(
            f"""SELECT inspected_product_id::text AS product_id,
                       inspection_id::text AS inspection_id, {', '.join(PRODUCT_COLS)}
                FROM inspected_products WHERE inspected_product_id = %s::uuid""",
            [product_id],
        )
        if not row:
            return None
        for c in DATE_COLS:
            if row.get(c) is not None:
                row[c] = str(row[c])
        return row

    def products_for(self, inspection_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT inspected_product_id::text AS product_id,
                      product_name, brand, manufacturer, category
               FROM inspected_products WHERE inspection_id = %s::uuid""",
            [inspection_id],
        )

    def declarations_for(self, product_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT d.extraction_id::text AS extraction_id,
                      d.evidence_id::text AS evidence_id, d.field_name,
                      d.extracted_value, d.normalized_value, d.confidence,
                      d.ocr_engine
               FROM extracted_declarations d
               JOIN inspection_evidence e USING (evidence_id)
               WHERE e.product_id = %s::uuid
               ORDER BY d.extraction_timestamp DESC""",
            [product_id],
        )

    def save_result(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = self.fetch_one(
            """INSERT INTO compliance_results
               (inspection_id, product_id, rule_version_id, requirement,
                expected_value, detected_value, result, confidence,
                evidence_id, engine_version, explanation)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING compliance_result_id::text AS compliance_result_id""",
            [row["inspection_id"], row["product_id"], row["rule_version_id"],
             row.get("requirement", ""), row.get("expected_value"),
             row.get("detected_value"), row["result"], row.get("confidence"),
             row.get("evidence_id"), row.get("engine_version", "1.0.0"),
             row.get("explanation", "")],
        )
        assert saved is not None
        return {**row, **saved}

    def results_for(self, inspection_id: str,
                    product_id: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT cr.compliance_result_id::text AS compliance_result_id,
                   cr.inspection_id::text AS inspection_id,
                   cr.product_id::text AS product_id,
                   cr.rule_version_id::text AS rule_version_id,
                   rv.check_id, cr.requirement, cr.expected_value,
                   cr.detected_value, cr.result, cr.confidence,
                   cr.explanation, cr.engine_version,
                   rv.sub_rule, rv.clause, rv.legal_text_or_paraphrase,
                   rv.effective_from::text AS effective_from,
                   rv.effective_to::text AS effective_to, rv.status,
                   lr.rule_number, ls.title AS source_title,
                   ls.authenticity_status, ls.url AS source_url
            FROM compliance_results cr
            JOIN rule_versions rv USING (rule_version_id)
            JOIN legal_rules lr USING (rule_id)
            JOIN legal_sources ls ON ls.source_id = rv.source_id
            WHERE cr.inspection_id = %s::uuid"""
        params: list[Any] = [inspection_id]
        if product_id:
            query += " AND cr.product_id = %s::uuid"
            params.append(product_id)
        return self.fetch_all(query + " ORDER BY lr.rule_number, rv.sub_rule", params)

    def create_violation(self, row: dict[str, Any]) -> dict[str, Any]:
        saved = self.fetch_one(
            """INSERT INTO violations
               (inspection_id, product_id, compliance_result_id, rule_version_id,
                violation_type, description, detected_value, expected_value,
                evidence_id, ai_confidence, inspector_status)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s,
                       'PENDING')
               RETURNING violation_id::text AS violation_id,
                         inspector_status""",
            [row["inspection_id"], row["product_id"], row["compliance_result_id"],
             row["rule_version_id"], row.get("violation_type", "MISSING_DECLARATION"),
             row.get("description", ""), row.get("detected_value"),
             row.get("expected_value"), row.get("evidence_id"),
             row.get("ai_confidence")],
        )
        assert saved is not None
        return {**row, **saved}

    def violations_for(self, inspection_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT violation_id::text AS violation_id,
                      inspection_id::text AS inspection_id,
                      product_id::text AS product_id, violation_type,
                      description, inspector_status, inspector_id
               FROM violations WHERE inspection_id = %s::uuid ORDER BY created_at""",
            [inspection_id],
        )

    def verify_violation(self, violation_id: str, status: str,
                         inspector_id: str = "",
                         remarks: str = "") -> dict[str, Any] | None:
        allowed = {"CONFIRMED", "REJECTED", "REQUIRES_REVIEW", "PENDING"}
        if status not in allowed:
            raise ValueError(f"invalid inspector status: {status}")
        self.execute(
            """UPDATE violations SET inspector_status = %s, inspector_id = %s,
                      verification_date = CURRENT_DATE, inspector_remarks = %s
               WHERE violation_id = %s::uuid""",
            [status, inspector_id, remarks, violation_id],
        )
        return self.fetch_one(
            """SELECT violation_id::text AS violation_id, inspector_status,
                      inspector_id FROM violations WHERE violation_id = %s::uuid""",
            [violation_id],
        )

    def audit(self, actor: str, action: str, entity: str,
              entity_id: str, detail: dict[str, Any] | None = None) -> None:
        self.execute(
            """INSERT INTO audit_logs (user_id, action, entity_type, entity_id, new_value)
               VALUES (%s, %s, %s, %s::uuid, %s::jsonb)""",
            [actor, action, entity, entity_id,
             json.dumps(detail or {}, default=str)],
        )
