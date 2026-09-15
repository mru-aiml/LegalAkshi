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

## Setup (Windows, PostgreSQL 14+)

```powershell
psql -U postgres -c "CREATE DATABASE legalakshi;"
psql "postgresql://postgres:PASSWORD@localhost:5432/legalakshi" -f backend/legalakshi_schema_v3_final.sql
Copy-Item backend/.env.example backend/.env   # set DATABASE_URL
cd backend
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Verify: `http://localhost:8000/healthz` → `{"status":"ok"}` ·
Swagger `http://localhost:8000/docs` ·
`python scripts/verify_db.py` (needs `DATABASE_URL`).

Frontend (unchanged UI): `$env:PORT="5174"; $env:BASE_PATH="/"; pnpm --filter @workspace/nutricheck run dev`
→ `http://localhost:5174` · `VITE_API_URL=http://localhost:8000`.

## Tests

```powershell
cd backend
python -m pytest tests -q        # 48 passed, 2 skipped (PG unavailable)
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
- `GET /api/v1/reports/{inspection_id}` → `FinalReport` (rebuilt from persisted rows)
- `POST /api/v1/violations/{vid}/verify` (`{decision, inspector_id, verification_notes?}`; decision ∈ PENDING/CONFIRMED/REJECTED/REQUIRES_REVIEW)
- `GET /healthz` · `GET /api/v1/health`

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
app/routes/{inspections,rules,violations,reports}.py
tests/  engine(22) api(11) seed-consistency(8) demo(5) postgres(2, skip w/o PG)
```
