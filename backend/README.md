# LegalAkshi FastAPI backend (Phase 2 — authoritative legal data)

Deterministic packaged-food label compliance engine.
**AI extracts. Rules interpret. Evidence supports. Scoring summarizes. Inspector decides.**

## Authoritative sources (imported verbatim, never hand-edited)

| File | Origin | Location in repo |
|---|---|---|
| `legalakshi_schema_v3_final.sql` | `C:\Users\Nilesh\Desktop\Team Zeinth\` (authoritative) | `backend/legalakshi_schema_v3_final.sql` + `backend/authoritative/` |
| `legalakshi_master_v4.json` | same (JSON projection for consumers; PG stays source of truth) | `backend/authoritative/` |
| `openapi.json` (LegalAkshi Compliance API 4.0.0) | same | `backend/authoritative/` + `lib/api-spec/openapi.json` |

`backend/legalakshi_schema_v3_final.sql` is byte-identical to the
authoritative file — full UUID architecture, exclusion constraint
`rule_versions_no_overlap`, all enforcement/case/audit/lookup tables.
Nothing was simplified. The phase-1 reconstructed mini-schema is gone.

## Production vs test repositories — read this

- **Production API = PostgreSQL only.** `app/main.py` raises `RuntimeError`
  at startup when `DATABASE_URL` is missing/unreachable. It never falls back
  to in-memory data. (`GET /api/v1/health` reports `"backend": "postgres"`.)
- **Unit tests = in-memory test repository** (`app/repositories/memory.py`,
  seeded from `legalakshi_master_v4.json`) injected via the app factory
  (`app/factory.py::create_app`). It is a *testing implementation*, not
  equivalent to PostgreSQL (e.g. no live exclusion constraint — covered by
  `test_seed_consistency.py` instead).

## Database failure behavior (§15)

Normal mode fails fast and loud: missing/unreachable PostgreSQL produces a
clear `RuntimeError` at startup ("DATABASE_URL must be set… / Cannot connect
to PostgreSQL…"), never a silent fallback to test legal data. Only tests
explicitly construct `MemoryRepo`.

## Setup (Windows, PostgreSQL 14+)

```powershell
psql -U postgres -c "CREATE DATABASE legalakshi;"
psql "postgresql://postgres:PASSWORD@localhost:5432/legalakshi" -f backend/legalakshi_schema_v3_final.sql
Copy-Item backend/.env.example backend/.env   # set DATABASE_URL
```

Or with Docker (on a machine with a Docker daemon) + the idempotent applier:

```powershell
cd backend
docker compose up -d postgres
$env:DATABASE_URL="postgresql://postgres:legalakshi-dev-only@localhost:5432/legalakshi"
python scripts/apply_schema.py
python scripts/verify_db.py
```

Then:

```powershell
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Verify: `http://localhost:8000/healthz` → `{"status":"ok"}` ·
Swagger `http://localhost:8000/docs` ·
`python scripts/verify_db.py` (needs `DATABASE_URL`).

Frontend (unchanged UI): `$env:PORT="5174"; $env:BASE_PATH="/"; pnpm --filter @workspace/nutricheck run dev`
→ `http://localhost:5174` · `VITE_API_URL=http://localhost:8000`.

CORS: the browser origin (scheme + host + port, e.g. `http://localhost:5173`
when Vite falls back from 5174) must be listed in `CORS_ORIGINS`
(`backend/.env`); anything else gets `400 Disallowed CORS origin` on
preflight. No wildcard origins with credentials. Covered by
`tests/test_cors.py`. Restart uvicorn after changing `.env`.

## Tests

```powershell
cd backend
python -m pytest tests -q        # 171 passed (incl. 3 live-PostgreSQL integration)
$env:DATABASE_URL="postgresql://..."; python -m pytest tests/test_postgres_integration.py -q
```

Without PostgreSQL the integration file **skips** with
"PostgreSQL integration test not executed because PostgreSQL was unavailable."
— never a fake pass.

## API (authoritative `openapi.json`)

- `POST /api/v1/inspections` (`InspectionCreate`: `inspector_id`, `inspector_name`, `business_name`, `inspection_date` required)
- `GET /api/v1/inspections` → **array**
- `POST /api/v1/inspections/{id}/products` (`Product`: `product_name`, `category`, `is_prepackaged` required; extras like `mrp`/`consumer_care` accepted as manual declarations)
- `POST /api/v1/inspections/{id}/analyze` (`{product?, product_id?, as_of_date?, scoring_policy_code?}`) → `AnalysisResponse{inspection_id, product_id, status, score{value,out_of,policy,policy_version,finalizable}, findings[Finding{rule_id,status,requirement,evidence,explanation,source_reference,legal_version,applicability}], recommendations, violation_ids}`
- `GET /api/v1/inspections/{id}/products/{pid}/compliance` → `Finding[]`
- `GET /api/v1/rules` → `Rule[]` (15) · `GET /api/v1/rules/{check_id}` (registry + full version lineage + applicability)
- `GET /api/v1/reports/{inspection_id}[?format=json|html|pdf]` → `FinalReport`
  (rebuilt from persisted rows; PDF via `app/services/reports.py`, latin-1
  sanitized, greppable for audit)
- `POST /api/v1/violations/{vid}/verify` (`{decision, inspector_id, verification_notes?}`; decision ∈ PENDING/CONFIRMED/REJECTED/REQUIRES_REVIEW) — **officer role required** (see Auth)
- Officer console: `GET /officer/queue|stats|profile`, `GET /officer/cases/{vid}`, `POST /officer/cases/{vid}/actions` — officer role required
- Complaints: `POST /complaints`, `GET /complaints/{id}`, `POST /complaints/{id}/transitions`
- Admin (admin role): `GET /admin/rules/sync/preview`, `POST /admin/rules/sync/apply {confirm:true}` (master-manifest import, dry-run first, history-preserving), `POST /admin/sources|rules/checks|rules/versions`
- `GET /healthz` · `GET /api/v1/health`

## Auth (roles: consumer < officer < admin)

- Without `CLERK_JWKS_URL`, local dev uses `X-LegalAkshi-Role` /
  `X-LegalAkshi-User` headers (or `DEV_AUTH_ROLE` env). Anonymous callers are
  consumers for public endpoints only; protected routes return **401** when
  anonymous, **403** when the role is insufficient.
- With `CLERK_JWKS_URL` set, Clerk RS256 JWTs are verified (role from token
  claims) and dev headers are ignored. Never set `DEV_AUTH_ROLE` in a shared
  deployment.
## Auth (roles: consumer < officer < admin)

- Without `CLERK_JWKS_URL`, local dev uses `X-LegalAkshi-Role` /
  `X-LegalAkshi-User` headers (or `DEV_AUTH_ROLE` env). Anonymous callers are
  consumers for public endpoints only; protected routes return **401** when
  anonymous, **403** when the role is insufficient.
- With `CLERK_JWKS_URL` set, Clerk RS256 JWTs are verified (role from token
  claims) and dev headers are ignored. Never set `DEV_AUTH_ROLE` in a shared
  deployment.
- Clerk role resolution (server-side, in order): explicit JWT role claim
  (requires a Clerk JWT template embedding `role`) → `OFFICER_USER_IDS` /
  `ADMIN_USER_IDS` env maps of verified Clerk `sub` values (works with
  DEFAULT Clerk tokens) → `consumer`. See `.env.example` for setup.
  Frontend lab page sends the dev officer header automatically for verify
  actions; a 401 there means the backend wants a real officer identity.
- Clerk JWT verification: set `CLERK_JWKS_URL` (backend/.env) to the Clerk
  Frontend API `/.well-known/jwks.json` endpoint. Roles come from token
  claims (`public_metadata.role`); dev headers are then ignored. Unit tests
  stay hermetic via `conftest.py` (dotenv disabled); `test_rbac.py` covers a
  real RS256 round-trip through the officer endpoints.

## OCR (real extraction, never a verdict)

Provider: **RapidOCR** (`rapidocr_onnxruntime`, ONNX CPU). Chosen over
PaddleOCR (~1 GB paddle dep, uncertain cp313/Windows support) and Tesseract
(no system binary available, fragile per-machine setup): pure-Python install,
verified working on this stack. `app/services/ocr/` holds the provider
abstraction (`base.py`), RapidOCR wrapper, deterministic regex field
extraction (`fields.py`: MRP, quantity/unit, dates, FSSAI-14 with context,
phones, batch, manufacturer, origin, unit price), and the service layer
(PIL preprocess: RGB, upscale-if-small, autocontrast).

- `POST /api/v1/ocr/extract` (multipart `front_image`/`back_image`,
  authenticated users) returns raw text + structured fields with
  `{value, provenance: "OCR", confidence}`; failures yield
  `status: NEEDS_REVIEW` with null fields instead of crashing.
- Review flow: `field_meta` on product creation records per-field provenance
  (unedited OCR fields keep engine+confidence, incl. mirror rows for
  manufacturer/quantity/dates — no schema change);   edited fields are
  `manual-entry` with NULL confidence. The rule engine receives exactly the
  final reviewed declaration.

## Notifications (migration 003)

`notifications` table (audience `officer`/`consumer`/`user:<id>`) written by
real events only: engine FAIL → officer, review-required → officer,
complaint filed → officer, complaint transition → reporter, rule sync →
officer. `GET /notifications` is scoped to the caller; read/read-all are
scoped the same way. Applied with `python scripts/apply_migrations.py`.

Convenience extras (compatible additions): `GET /inspections/{id}`,
`GET /inspections/{id}/results`, `GET /inspections/{id}/violations`,
`GET /scoring-policy`. Express backend untouched.

## Engine notes

- Temporal selection: `effective_from <= as_of AND (effective_to IS NULL OR
  effective_to > as_of)` over statuses `IN_FORCE`/`SUPERSEDED` (history);
  `NOT_YET_IN_FORCE`/`DRAFT`/`REPEALED` never selected. One version per check.
- Applicability runs first; `CONDITIONAL` QR-proviso rows close only the
  proviso path — the base requirement still stands (`mandatory_default`).
  Ambiguous/missing config → `NEEDS_REVIEW`.
- Confidence origins: `OCR` (measured) below threshold → `NEEDS_REVIEW`;
  `MANUAL` (confidence NULL, never fabricated) and `ASSUMED` (tests) are
  evaluated as stated with origin exposed on every finding.
- Sources with `authenticity_status` outside `VERIFIED_PRIMARY/SECONDARY`
  force `NEEDS_REVIEW`. No penalties invented (amounts stay NULL until orders).
- `CHK-OTHER-MATTERS` (Rule 6(1)(g) catch-all, weight 0) always routes to
  inspector review, so analyses are non-finalizable until reviewed — per
  `DO_NOT_FINALIZE_WITHOUT_REVIEW`.

## Demo data

`backend/demo/cases.json` — **DEMO/TEST ONLY**, cases A–E (compliant / missing
MRP / imported / e-commerce+imported / low-OCR-confidence), exercised by
`tests/test_demo_cases.py`. Not law.

## Layout

```
legalakshi_schema_v3_final.sql  authoritative schema (verbatim)
authoritative/                  schema + master v4 + openapi.json (verbatim)
demo/cases.json                 DEMO data A–E
app/factory.py  app/main.py     DI factory / production entrypoint (DB required)
app/core/config.py              pydantic-settings
app/repositories/{base,postgres,memory}.py
app/engine/{facts,conditions,applicability,checks,scoring,engine}.py
app/models/schemas.py           authoritative contracts
app/routes/{inspections,rules,violations,reports,complaints,officer,admin}.py
app/services/{reports,rule_sync}.py  HTML/PDF/JSON reports, manifest sync
app/core/auth.py                  RBAC (Clerk JWT or local-dev headers)
tests/  96 passed: engine, api, rbac, seed-consistency, demo, reports,
        openapi-contract, schema-static, postgres-integration (live DB)

## Migrations (never edit the authoritative schema)

`backend/legalakshi_schema_v3_final.sql` is verbatim upstream. Application
tables added later (e.g. `complaints` + `complaint_events`, which predate any
inspection) live in `backend/migrations/*.sql`, applied in order via
`python scripts/apply_migrations.py` (tracked in `schema_migrations`).
`scripts/validate_schema.py` checks authoritative + migration tables together.
```
