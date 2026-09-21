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
        self._pool: Any = None  # lazy psycopg_pool.ConnectionPool / False

    # ------------------------------------------------------------ driver ---
    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self._dsn, row_factory=dict_row)

    def _pool_or_none(self) -> Any | None:
        """Shared connection pool (reused across queries in this process).

        One pool per PostgresRepo: Neon handshakes (TCP+TLS, plus cold
        starts) happen once per pooled connection instead of once per
        query. Falls back to per-query connections when psycopg_pool is
        unavailable — behavior is identical, only slower.
        """
        if self._pool is None:
            try:
                from psycopg_pool import ConnectionPool
                from psycopg.rows import dict_row

                self._pool = ConnectionPool(
                    self._dsn, min_size=1, max_size=5, timeout=10,
                    kwargs={"row_factory": dict_row,
                            "connect_timeout": 10})
            except Exception as exc:
                log.warning("connection pool unavailable (%s); using "
                            "per-query connections", exc)
                self._pool = False
        return self._pool or None

    def close(self) -> None:
        """Release pooled connections (process shutdown path)."""
        pool, self._pool = self._pool, None
        if pool:
            try:
                pool.close()
            except Exception as exc:
                log.warning("connection pool close failed: %s", exc)

    def ping(self) -> bool:
        try:
            return bool(self.fetch_one("SELECT 1 AS ok"))
        except Exception as exc:
            log.error("postgres ping failed: %s", exc)
            return False

    def fetch_all(self, query: str, params: list | None = None) -> list[dict]:
        from app.core import dbmetrics as _dbmetrics

        _dbmetrics.increment()
        pool = self._pool_or_none()
        if pool is not None:
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute(query, params or [])
                return list(cur.fetchall())
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params or [])
            return list(cur.fetchall())

    def fetch_one(self, query: str, params: list | None = None) -> dict | None:
        rows = self.fetch_all(query, params)
        return rows[0] if rows else None

    def execute(self, query: str, params: list | None = None) -> None:
        from app.core import dbmetrics as _dbmetrics

        _dbmetrics.increment()
        pool = self._pool_or_none()
        if pool is not None:
            with pool.connection() as conn, conn.cursor() as cur:
                cur.execute(query, params or [])
                conn.commit()
            return
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
                  if k not in PRODUCT_COLS
                  and k not in ("inspection_id", "product_id", "field_meta")}
        # field_meta carries reviewed provenance per REGISTRY field name, e.g.
        # {"mrp": {"ocr_engine": "rapidocr", "confidence": 0.94},
        #  "manufacturer": {"ocr_engine": "rapidocr", "confidence": 0.91}}.
        # Absent/edited fields stay manual-entry with NULL confidence.
        # Column-mapped fields (manufacturer, quantity, dates, ...) get
        # mirror declaration rows with the SAME reviewed value (server-side
        # strings) so OCR provenance survives without schema changes.
        from app.engine.facts import COLUMN_MAP
        from app.repositories._util import meta_confidence

        meta = data.get("field_meta") or {}

        def _is_ocr(field: str) -> bool:
            return (meta.get(field) or {}).get("ocr_engine",
                                               "manual-entry") != "manual-entry"

        mirrors = [(COLUMN_MAP[col], data[col])
                   for col in COLUMN_MAP
                   if col in data and data[col] is not None
                   and _is_ocr(COLUMN_MAP[col])]
        # Uncertainty mirrors (see memory.py): OCR attempted a registry
        # field but the reviewer left it blank. A NULL-valued declaration
        # row with measured low/zero confidence routes the check to
        # NEEDS_REVIEW via the engine's low-confidence gate instead of a
        # legal FAIL for what may be mere OCR uncertainty.
        mirror_fields = {m[0] for m in mirrors}
        uncertain = [field for col, field in COLUMN_MAP.items()
                     if _is_ocr(field)
                     and field not in mirror_fields
                     and field not in extras
                     and (col not in data or data[col] is None)]
        mixed = bool(mirrors) or bool(uncertain) or any(_is_ocr(f) for f in extras)
        if extras or mirrors or uncertain:
            ev = self.fetch_one(
                """INSERT INTO inspection_evidence
                   (inspection_id, product_id, evidence_type, description, source)
                   VALUES (%s::uuid, %s::uuid, 'OTHER', %s, %s)
                   RETURNING evidence_id::text AS evidence_id""",
                [inspection_id, pid,
                 ("Reviewed declaration: unedited OCR-extracted fields plus "
                  "manual entries/confirmations."
                  if mixed else
                  "Manually supplied product declaration (not OCR)."),
                 "reviewed-declaration" if mixed else "manual-entry"],
            )
            assert ev is not None
            # One multi-row INSERT instead of one round trip per field:
            # product creation on a cold database was paying a full
            # handshake per declaration row. Row content is identical.
            decl_rows: list[tuple] = []
            for field, value in list(extras.items()) + mirrors:
                m = meta.get(field) or {}
                decl_rows.append((
                    ev["evidence_id"], field,
                    None if value is None else str(value),
                    meta_confidence(m.get("confidence")),
                    str(m.get("ocr_engine") or "manual-entry")))
            for field in uncertain:
                m = meta.get(field) or {}
                decl_rows.append((
                    ev["evidence_id"], field, None,
                    meta_confidence(m.get("confidence")),
                    str(m.get("ocr_engine") or "manual-entry")))
            if decl_rows:
                placeholders = ",".join(
                    ["(%s::uuid, %s, %s, %s, %s)"] * len(decl_rows))
                params: list[Any] = []
                for row in decl_rows:
                    params.extend(row)
                self.execute(
                    """INSERT INTO extracted_declarations
                       (evidence_id, field_name, extracted_value, confidence,
                        ocr_engine)
                       VALUES """ + placeholders,
                    params,
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

    # --------------------------------- batched reads, same semantics ---
    # Aggregate pages (consumer overview) fetch these three lists once
    # with ANY() instead of once per inspection/product. Row shapes match
    # the single-id methods so route semantics are unchanged.
    def products_for_many(
            self, inspection_ids: list[str]) -> dict[str, list[dict]]:
        if not inspection_ids:
            return {}
        rows = self.fetch_all(
            """SELECT inspected_product_id::text AS product_id,
                      inspection_id::text AS inspection_id,
                      product_name, brand, manufacturer, quantity,
                      quantity_unit, barcode
               FROM inspected_products
               WHERE inspection_id = ANY(%s::uuid[])""",
            [inspection_ids],
        )
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["inspection_id"], []).append(row)
        return grouped

    def results_for_many(
            self, inspection_ids: list[str]) -> list[dict[str, Any]]:
        if not inspection_ids:
            return []
        return self.fetch_all(
            """SELECT cr.compliance_result_id::text AS compliance_result_id,
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
               WHERE cr.inspection_id = ANY(%s::uuid[])
               ORDER BY lr.rule_number, rv.sub_rule""",
            [inspection_ids],
        )

    def violations_for_many(
            self, inspection_ids: list[str]) -> list[dict[str, Any]]:
        if not inspection_ids:
            return []
        return self.fetch_all(
            """SELECT violation_id::text AS violation_id,
                      inspection_id::text AS inspection_id,
                      product_id::text AS product_id, violation_type,
                      description, inspector_status, inspector_id
               FROM violations WHERE inspection_id = ANY(%s::uuid[])
               ORDER BY created_at""",
            [inspection_ids],
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

    def audit_list(self, entity: str, entity_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT audit_id, user_id, action, entity_type,
                      entity_id::text AS entity_id, old_value, new_value,
                      reason, "timestamp"
               FROM audit_logs WHERE entity_type = %s AND entity_id = %s::uuid
               ORDER BY "timestamp\"""",
            [entity, entity_id],
        )

    def get_violation(self, violation_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """SELECT violation_id::text AS violation_id,
                      inspection_id::text AS inspection_id,
                      product_id::text AS product_id, violation_type,
                      description, detected_value, expected_value,
                      inspector_status, inspector_id,
                      verification_date::text AS verification_date,
                      inspector_remarks
               FROM violations WHERE violation_id = %s::uuid""",
            [violation_id],
        )

    # ------------------------------------------------------- complaints ---
    def create_complaint(self, data: dict[str, Any]) -> dict[str, Any]:
        from app.models.lifecycle import STATUSES

        status = data.get("status", "SUBMITTED")
        if status not in STATUSES:
            raise ValueError(f"invalid complaint status: {status}")
        row = self.fetch_one(
            """INSERT INTO complaints
               (reporter_id, product_name, retailer, city, severity,
                description, status, inspection_id, violation_id, evidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               RETURNING complaint_id::text AS complaint_id, status,
                         created_at, updated_at""",
            [data.get("reporter_id", ""), data.get("product_name", ""),
             data.get("retailer", ""), data.get("city", ""),
             data.get("severity", "Medium"), data.get("description", ""),
             status, data.get("inspection_id"), data.get("violation_id"),
             json.dumps(data.get("evidence", []), default=str)],
        )
        assert row is not None
        self.execute(
            """INSERT INTO complaint_events
               (complaint_id, event_type, from_status, to_status, actor_id, note)
               VALUES (%s::uuid, 'CREATED', NULL, %s, %s, 'Complaint submitted.')""",
            [row["complaint_id"], status, data.get("reporter_id", "")],
        )
        return {**data, **row}

    def get_complaint(self, complaint_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """SELECT complaint_id::text AS complaint_id, reporter_id,
                      product_name, retailer, city, severity, description,
                      status, inspection_id::text AS inspection_id,
                      violation_id::text AS violation_id, evidence,
                      created_at, updated_at
               FROM complaints WHERE complaint_id = %s::uuid""",
            [complaint_id],
        )

    def list_complaints(self, reporter_id: str | None = None) -> list[dict[str, Any]]:
        query = ("SELECT complaint_id::text AS complaint_id, reporter_id,"
                 " product_name, retailer, city, severity, status,"
                 " created_at, updated_at FROM complaints")
        params: list[Any] = []
        if reporter_id is not None:
            query += " WHERE reporter_id = %s"
            params.append(reporter_id)
        return self.fetch_all(query + " ORDER BY created_at DESC", params)

    def transition_complaint(self, complaint_id: str, to_status: str,
                             actor_id: str = "",
                             note: str = "") -> dict[str, Any] | None:
        from app.models.lifecycle import STATUSES, allowed

        if to_status not in STATUSES:
            raise ValueError(f"invalid complaint status: {to_status}")
        current = self.get_complaint(complaint_id)
        if not current:
            return None
        if not allowed(current["status"], to_status):
            raise ValueError(
                f"illegal transition {current['status']} -> {to_status}")
        self.execute(
            """UPDATE complaints SET status = %s, updated_at = now()
               WHERE complaint_id = %s::uuid""",
            [to_status, complaint_id],
        )
        self.execute(
            """INSERT INTO complaint_events
               (complaint_id, event_type, from_status, to_status, actor_id, note)
               VALUES (%s::uuid, 'STATUS_CHANGE', %s, %s, %s, %s)""",
            [complaint_id, current["status"], to_status, actor_id, note],
        )
        return self.get_complaint(complaint_id)

    def complaint_timeline(self, complaint_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT event_id::text AS event_id, event_type, from_status,
                      to_status, actor_id, note, created_at
                FROM complaint_events WHERE complaint_id = %s::uuid
                ORDER BY created_at""",
            [complaint_id],
        )

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
        row = self.fetch_one(
            """INSERT INTO consumer_suggestions
               (consumer_user_id, title, category, description, context,
                location, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING suggestion_id::text AS suggestion_id, status,
                          created_at, updated_at""",
            [data.get("consumer_user_id", ""), data.get("title", ""),
             category, data.get("description", ""), data.get("context", ""),
             data.get("location", ""), status],
        )
        assert row is not None
        self.execute(
            """INSERT INTO suggestion_events
               (suggestion_id, event_type, from_status, to_status,
                actor_id, note)
                VALUES (%s::uuid, 'CREATED', NULL, %s, %s,
                        'Suggestion submitted.')""",
            [row["suggestion_id"], status, data.get("consumer_user_id", "")],
        )
        return {**data, **row}

    def get_suggestion(self, suggestion_id: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """SELECT suggestion_id::text AS suggestion_id,
                      consumer_user_id, title, category, description,
                      context, location, status, reviewed_by, officer_note,
                      created_at, updated_at
                FROM consumer_suggestions WHERE suggestion_id = %s::uuid""",
            [suggestion_id],
        )

    def list_suggestions(self, consumer_user_id: str | None = None
                         ) -> list[dict[str, Any]]:
        query = ("SELECT suggestion_id::text AS suggestion_id,"
                 " consumer_user_id, title, category, description, context,"
                 " location, status, reviewed_by, officer_note,"
                 " created_at, updated_at FROM consumer_suggestions")
        params: list[Any] = []
        if consumer_user_id is not None:
            query += " WHERE consumer_user_id = %s"
            params.append(consumer_user_id)
        return self.fetch_all(query + " ORDER BY created_at DESC", params)

    def update_suggestion(self, suggestion_id: str, to_status: str,
                          actor_id: str = "",
                          note: str = "") -> dict[str, Any] | None:
        from app.models.lifecycle import (SUGGESTION_STATUSES,
                                          suggestion_allowed)

        if to_status not in SUGGESTION_STATUSES:
            raise ValueError(f"invalid suggestion status: {to_status}")
        current = self.get_suggestion(suggestion_id)
        if not current:
            return None
        if not suggestion_allowed(current["status"], to_status):
            raise ValueError(
                f"illegal transition {current['status']} -> {to_status}")
        if note:
            self.execute(
                """UPDATE consumer_suggestions
                   SET status = %s, reviewed_by = %s, officer_note = %s,
                       updated_at = now()
                   WHERE suggestion_id = %s::uuid""",
                [to_status, actor_id, note, suggestion_id],
            )
        else:
            self.execute(
                """UPDATE consumer_suggestions
                   SET status = %s, reviewed_by = %s, updated_at = now()
                   WHERE suggestion_id = %s::uuid""",
                [to_status, actor_id, suggestion_id],
            )
        self.execute(
            """INSERT INTO suggestion_events
               (suggestion_id, event_type, from_status, to_status,
                actor_id, note)
                VALUES (%s::uuid, 'STATUS_CHANGE', %s, %s, %s, %s)""",
            [suggestion_id, current["status"], to_status, actor_id, note],
        )
        return self.get_suggestion(suggestion_id)

    def suggestion_timeline(self, suggestion_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT event_id::text AS event_id, event_type, from_status,
                      to_status, actor_id, note, created_at
                FROM suggestion_events WHERE suggestion_id = %s::uuid
                ORDER BY created_at""",
            [suggestion_id],
        )

    # ------------------------------------------------- officer console ---
    def violations_queue(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        query = """
            SELECT v.violation_id::text AS violation_id,
                   v.inspection_id::text AS inspection_id,
                   v.product_id::text AS product_id,
                   p.product_name, p.category, i.business_name,
                   i.state, i.district, v.violation_type, v.description,
                   rv.check_id, COALESCE(w.severity, 'MEDIUM') AS severity,
                   v.inspector_status, v.inspector_id, v.created_at,
                   v.verification_date::text AS verification_date
            FROM violations v
            JOIN inspected_products p ON p.inspected_product_id = v.product_id
            JOIN inspections i ON i.inspection_id = v.inspection_id
            JOIN rule_versions rv ON rv.rule_version_id = v.rule_version_id
            LEFT JOIN scoring_policy_weights w
              ON w.check_id = rv.check_id AND w.policy_id = 'DEFAULT-2026'"""
        clauses, params = [], []
        mapping = {"status": "v.inspector_status",
                   "violation_type": "v.violation_type",
                   "check_id": "rv.check_id", "category": "p.category",
                   "state": "i.state"}
        for key, col in mapping.items():
            if filters.get(key):
                clauses.append(f"{col} = %s")
                params.append(filters[key])
        if filters.get("severity"):
            clauses.append("COALESCE(w.severity, 'MEDIUM') = %s")
            params.append(str(filters["severity"]).upper())
        if filters.get("date_from"):
            clauses.append("v.created_at::date >= %s::date")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            clauses.append("v.created_at::date <= %s::date")
            params.append(filters["date_to"])
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        return self.fetch_all(query + " ORDER BY v.created_at DESC", params)

    def officer_stats(self) -> dict[str, Any]:
        row = self.fetch_one(
            """SELECT
                 COUNT(*) FILTER (WHERE inspector_status = 'PENDING') AS awaiting,
                 COUNT(*) FILTER (WHERE inspector_status = 'CONFIRMED') AS confirmed,
                 COUNT(*) FILTER (WHERE inspector_status IN ('CONFIRMED','REJECTED')) AS resolved,
                 COUNT(*) AS total,
                 AVG(verification_date - created_at::date)
                   FILTER (WHERE verification_date IS NOT NULL) AS avg_days
               FROM violations""",
        ) or {}
        high = self.fetch_one(
            """SELECT COUNT(*) AS n FROM violations v
               JOIN rule_versions rv USING (rule_version_id)
               JOIN scoring_policy_weights w
                 ON w.check_id = rv.check_id AND w.policy_id = 'DEFAULT-2026'
               WHERE v.inspector_status = 'PENDING' AND w.severity = 'HIGH'""",
        ) or {"n": 0}
        rules = self.fetch_one(
            """SELECT COUNT(*) AS n FROM rule_versions
               WHERE status = 'IN_FORCE' AND effective_from <= CURRENT_DATE
                 AND (effective_to IS NULL OR effective_to > CURRENT_DATE)""",
        ) or {"n": 0}
        complaints = self.fetch_one(
            """SELECT COUNT(*) FILTER (WHERE status <> 'CLOSED') AS open,
                      COUNT(*) AS total FROM complaints""",
        ) if self.fetch_one("SELECT to_regclass('public.complaints')") else None
        total = int(row.get("total", 0) or 0)
        resolved = int(row.get("resolved", 0) or 0)
        avg = row.get("avg_days")
        return {
            "awaiting_review": int(row.get("awaiting", 0) or 0),
            "high_priority": int(high.get("n", 0) or 0),
            "action_taken": int(row.get("confirmed", 0) or 0),
            "resolved": resolved, "total_violations": total,
            "resolution_rate": round(resolved / total, 3) if total else 0.0,
            "avg_response_days": round(float(avg), 1) if avg is not None else None,
            "active_rules": int(rules.get("n", 0) or 0),
            "open_complaints": int((complaints or {}).get("open", 0) or 0),
            "total_complaints": int((complaints or {}).get("total", 0) or 0),
        }

    def case_detail(self, violation_id: str) -> dict[str, Any] | None:
        v = self.get_violation(violation_id)
        if not v:
            return None
        findings = self.results_for(v["inspection_id"], v["product_id"])
        rule_ids = sorted({r.get("check_id", "") for r in findings if r.get("check_id")})
        return {
            "violation": v,
            "product": self.get_product(v["product_id"]),
            "inspection": self.get_inspection(v["inspection_id"]),
            "findings": findings,
            "evidence": self.evidence_for(v["product_id"]),
            "declarations": self.declarations_for(v["product_id"]),
            "rules": [self.rule_detail(c) for c in rule_ids],
            "enforcement": self.enforcement_for_violation(violation_id),
            "audit": self.audit_list("violation", violation_id),
        }

    def evidence_for(self, product_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT evidence_id::text AS evidence_id,
                      inspection_id::text AS inspection_id,
                      product_id::text AS product_id, evidence_type,
                      file_path, description, source, metadata
               FROM inspection_evidence WHERE product_id = %s::uuid""",
            [product_id],
        )

    def record_enforcement_action(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO enforcement_actions
               (violation_id, inspection_id, action_type, legal_basis,
                enforcement_provision_id, authority, action_date,
                action_status, notice_number, remarks, created_by)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s,
                       COALESCE(%s::date, CURRENT_DATE), %s, %s, %s, %s)
               RETURNING action_id::text AS action_id""",
            [data["violation_id"], data["inspection_id"], data["action_type"],
             data["legal_basis"], data.get("enforcement_provision_id"),
             data.get("authority"), data.get("action_date"),
             data.get("action_status", "INITIATED"), data.get("notice_number"),
             data.get("remarks"), data.get("created_by")],
        )
        assert row is not None
        return {**data, **row}

    def enforcement_for_violation(self, violation_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT action_id::text AS action_id, action_type, legal_basis,
                      authority, action_date::text AS action_date,
                      action_status, notice_number, remarks, created_by
               FROM enforcement_actions WHERE violation_id = %s::uuid
               ORDER BY created_at""",
            [violation_id],
        )

    # ------------------------------------------------------ rule sync ---
    def all_rule_versions(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT rv.rule_version_id::text AS rule_version_id,
                      rv.check_id, rv.requirement,
                      rv.legal_text_or_paraphrase,
                      rv.effective_from::text AS effective_from,
                      rv.effective_to::text AS effective_to, rv.status,
                      rv.sub_rule, rv.clause, lr.rule_number
               FROM rule_versions rv
               JOIN legal_rules lr USING (rule_id)""")

    def all_applicability(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            """SELECT ra.applicability_id::text AS applicability_id,
                      ra.rule_version_id::text AS rule_version_id,
                      rv.check_id, ra.condition_expression,
                      ra.applicable_result, ra.reason
               FROM rule_applicability ra
               JOIN rule_versions rv USING (rule_version_id)""")

    def all_weights(self, policy_id: str) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT check_id, weight, severity FROM scoring_policy_weights"
            " WHERE policy_id = %s",
            [policy_id],
        )

    def get_rule_by_number(self, rule_number: str) -> dict[str, Any] | None:
        return self.fetch_one(
            """SELECT rule_id::text AS rule_id, rule_set_name, rule_number,
                      short_title FROM legal_rules WHERE rule_number = %s""",
            [rule_number],
        )

    def create_legal_rule(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO legal_rules (rule_set_name, rule_number, short_title)
               VALUES (%s, %s, %s)
               RETURNING rule_id::text AS rule_id""",
            [data.get("rule_set_name", ""), data.get("rule_number", ""),
             data.get("short_title", "")],
        )
        assert row is not None
        return {**data, **row}

    def create_legal_source(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO legal_sources
               (source_type, title, issuing_authority, notification_number,
                publication_date, effective_date, url, document_identifier,
                version, jurisdiction, authenticity_status, notes)
               VALUES (%s,%s,%s,%s,%s::date,%s::date,%s,%s,%s,
                       COALESCE(%s,'India (Union)'),%s,%s)
               RETURNING source_id::text AS source_id""",
            [data.get("source_type"), data.get("title"),
             data.get("issuing_authority"), data.get("notification_number"),
             data.get("publication_date"), data.get("effective_date"),
             data.get("url"), data.get("document_identifier"),
             data.get("version"), data.get("jurisdiction"),
             data.get("authenticity_status"), data.get("notes")],
        )
        assert row is not None
        return {**data, **row}

    def insert_registry_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        self.execute(
            """INSERT INTO engine_check_registry
               (check_id, rule_id, sub_rule, clause, title, field_name,
                check_type, mandatory_default, scoring_category, description)
               VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [entry["check_id"], entry["rule_id"], entry.get("sub_rule"),
             entry.get("clause"), entry.get("title", ""),
             entry.get("field_name", ""), entry.get("check_type", "MANUAL_REVIEW"),
             bool(entry.get("mandatory_default", False)),
             entry.get("scoring_category", "OTHER"),
             entry.get("description", "")],
        )
        return dict(entry)

    def insert_rule_version(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO rule_versions
               (rule_version_id, rule_id, sub_rule, clause, requirement,
                legal_text_or_paraphrase, requirement_type, is_mandatory,
                effective_from, effective_to, source_id, supersedes,
                status, check_id)
               VALUES (COALESCE(%s::uuid, gen_random_uuid()), %s::uuid, %s, %s,
                       %s, %s, %s, %s, %s::date, %s::date, %s::uuid, %s::uuid,
                       %s, %s)
               RETURNING rule_version_id::text AS rule_version_id""",
            [data.get("rule_version_id"), data["rule_id"], data.get("sub_rule"),
             data.get("clause"), data.get("requirement", ""),
             data.get("legal_text_or_paraphrase", ""),
             data.get("requirement_type", "DECLARATION"),
             bool(data.get("is_mandatory", True)), data["effective_from"],
             data.get("effective_to"), data["source_id"], data.get("supersedes"),
             data.get("status", "IN_FORCE"), data["check_id"]],
        )
        assert row is not None
        return {**data, **row}

    def supersede_version(self, rule_version_id: str,
                          effective_to: str) -> dict[str, Any] | None:
        self.execute(
            """UPDATE rule_versions SET effective_to = %s::date,
                      status = 'SUPERSEDED', superseded_by = NULL
               WHERE rule_version_id = %s::uuid""",
            [effective_to, rule_version_id],
        )
        return self.fetch_one(
            """SELECT rule_version_id::text AS rule_version_id, status,
                      effective_to::text AS effective_to FROM rule_versions
               WHERE rule_version_id = %s::uuid""",
            [rule_version_id],
        )

    def insert_applicability(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO rule_applicability
               (rule_version_id, condition_expression, applicable_result,
                reason, source_id)
               VALUES (%s::uuid, %s::jsonb, %s, %s, %s::uuid)
               RETURNING applicability_id::text AS applicability_id""",
            [data["rule_version_id"],
             json.dumps(data.get("condition_expression", {}), default=str),
             data.get("applicable_result", "CONDITIONAL"),
             data.get("reason", ""), data["source_id"]],
        )
        assert row is not None
        return {**data, **row}

    def upsert_weight(self, policy_id: str, check_id: str,
                      weight: float, severity: str) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO scoring_policy_weights
               (policy_id, check_id, weight, severity)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (policy_id, check_id) DO UPDATE
                 SET weight = EXCLUDED.weight, severity = EXCLUDED.severity
               RETURNING policy_id, check_id, weight, severity""",
            [policy_id, check_id, weight, severity],
        )
        assert row is not None
        return dict(row)

    # ------------------------------------------------------ notifications ---
    def create_notification(self, data: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one(
            """INSERT INTO notifications (audience, type, title, body, link)
               VALUES (%s, %s, %s, %s, %s)
               RETURNING notification_id::text AS notification_id, audience,
                         type, title, body, link, read, created_at""",
            [data.get("audience", ""), data.get("type", ""),
             data.get("title", ""), data.get("body", ""), data.get("link", "")],
        )
        assert row is not None
        return dict(row)

    def list_notifications(self, audiences: list[str]) -> list[dict[str, Any]]:
        if not audiences:
            return []
        return self.fetch_all(
            """SELECT notification_id::text AS notification_id, audience,
                      type, title, body, link, read, created_at
               FROM notifications WHERE audience = ANY(%s)
               ORDER BY created_at DESC LIMIT 50""",
            [audiences],
        )

    def mark_notification_read(self, notification_id: str,
                               audiences: list[str]) -> dict[str, Any] | None:
        row = self.fetch_one(
            """UPDATE notifications SET read = TRUE
               WHERE notification_id = %s::uuid AND audience = ANY(%s)
               RETURNING notification_id::text AS notification_id, read""",
            [notification_id, audiences or [""]],
        )
        return dict(row) if row else None

    def mark_notifications_read(self, audiences: list[str]) -> int:
        if not audiences:
            return 0
        rows = self.fetch_all(
            """UPDATE notifications SET read = TRUE
               WHERE audience = ANY(%s) AND read = FALSE
               RETURNING notification_id""",
            [audiences],
        )
        return len(rows)

    # --------------------------------------- declaration corrections ---
    # Migration 005 (declaration_corrections). Missing table -> actionable
    # error naming the migration (same pattern as suggestions fallback).
    def create_correction(self, data: dict[str, Any]) -> dict[str, Any]:
        import json as _json

        if not str(data.get("field_key", "") or "").strip():
            raise ValueError("field_key is required")
        try:
            row = self.fetch_one(
                """INSERT INTO declaration_corrections
                   (inspection_id, product_id, field_key, original_value,
                    corrected_value, original_status, original_confidence,
                    source, evidence_snapshot, officer_user_id,
                    correction_reason)
                   VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s,
                           %s::jsonb, %s, %s)
                   RETURNING id::text AS id, inspection_id::text,
                    product_id::text, field_key, original_value,
                    corrected_value, original_status, original_confidence,
                    source, evidence_snapshot, officer_user_id, created_at,
                    verified, verified_by, verified_at, correction_reason""",
                [data.get("inspection_id"), data.get("product_id"),
                 str(data.get("field_key", "")).strip(),
                 data.get("original_value"), data.get("corrected_value"),
                 data.get("original_status", "NEEDS_REVIEW"),
                 data.get("original_confidence"),
                 data.get("source", "officer-review"),
                 _json.dumps(data.get("evidence_snapshot") or {}),
                 data.get("officer_user_id", ""),
                 data.get("correction_reason", "")],
            )
        except Exception as exc:
            raise RuntimeError(
                "declaration_corrections is not readable. Apply "
                "backend/migrations/005_declaration_corrections.sql via "
                f"backend/scripts/apply_migrations.py ({exc})")
        assert row is not None
        return dict(row)

    def list_corrections(self, inspection_id: str,
                         product_id: str) -> list[dict[str, Any]]:
        try:
            return self.fetch_all(
                """SELECT id::text AS id, inspection_id::text,
                          product_id::text, field_key, original_value,
                          corrected_value, original_status,
                          original_confidence, source, evidence_snapshot,
                          officer_user_id, created_at, verified, verified_by,
                          verified_at, correction_reason
                   FROM declaration_corrections
                   WHERE inspection_id = %s::uuid AND product_id = %s::uuid
                   ORDER BY created_at""",
                [inspection_id, product_id],
            )
        except Exception as exc:
            raise RuntimeError(
                "declaration_corrections is not readable. Apply "
                "backend/migrations/005_declaration_corrections.sql via "
                f"backend/scripts/apply_migrations.py ({exc})")

    def all_corrections(self) -> list[dict[str, Any]]:
        try:
            return self.fetch_all(
                """SELECT id::text AS id, inspection_id::text,
                          product_id::text, field_key, original_value,
                          corrected_value, original_status,
                          original_confidence, source, evidence_snapshot,
                          officer_user_id, created_at, verified, verified_by,
                          verified_at, correction_reason
                   FROM declaration_corrections
                   ORDER BY created_at""")
        except Exception as exc:
            raise RuntimeError(
                "declaration_corrections is not readable. Apply "
                "backend/migrations/005_declaration_corrections.sql via "
                f"backend/scripts/apply_migrations.py ({exc})")

    def verify_correction(self, correction_id: str, verified: bool,
                          verifier_id: str = "") -> dict[str, Any] | None:
        row = self.fetch_one(
            """UPDATE declaration_corrections
               SET verified = %s, verified_by = %s,
                   verified_at = CASE WHEN %s THEN now() ELSE NULL END
               WHERE id = %s::uuid
               RETURNING id::text AS id, verified, verified_by,
                         verified_at""",
            [bool(verified), verifier_id or "", bool(verified),
             correction_id],
        )
        return dict(row) if row else None

