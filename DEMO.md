# LegalAkshi — Live Demo Script (manual declarations, no OCR)

**Honest setup note:** this demo uses *manually typed declarations*. No OCR
runs in this flow — the form is labelled "Manual declaration" and values are
stored with `MANUAL` confidence origin (never shown as OCR evidence).

Architecture: **AI extracts. Rules interpret. Evidence supports. Scoring
summarizes. Inspector decides.** PostgreSQL is the runtime legal source of
truth; `legalakshi_master_v4.json` is a reference projection, never the
runtime authority when PostgreSQL is available.

## Prerequisites

```powershell
# 1. PostgreSQL (docker compose on a machine with Docker, or local PG 14+)
cd backend
docker compose up -d postgres
python scripts/apply_schema.py   # DATABASE_URL must be set; see .env.example

# 2. Backend (backend/ directory)
pip install -r requirements.txt
uvicorn app.main:app --port 8000        # Swagger: http://localhost:8000/docs

# 3. Frontend (repository root)
$env:PORT="5174"; $env:BASE_PATH="/"
pnpm --filter @workspace/nutricheck run dev   # http://localhost:5174
```

The frontend reads the backend address from `VITE_API_URL`
(`artifacts/nutricheck/.env.local`, `artifacts/nutricheck/.env.example`).
It must match the port the backend actually runs on (8123 in this local
setup, 8000 in the default docs). The Lab check page banner shows
**LIVE backend** with the configured address, or **unavailable** — analysis
is never fabricated when the backend is down.

## Workspaces & roles

There is no "switch view" button. The sidebar and routes follow the Clerk
account role (`publicMetadata.role`: consumer / officer / admin); visiting
another workspace's route shows a 403 page, and protected API calls return
401/403 enforced by the backend. For local development only, setting
`VITE_DEMO_ROLE_SWITCH=true` reveals a clearly-labeled DEV role override
(default off, never part of the normal UI).

Officer API access needs a verified officer identity: either a Clerk JWT
template embedding the role claim, or the officer's Clerk user ID in the
backend's `OFFICER_USER_IDS` (Clerk dashboard → Users → User ID → backend
`.env` → restart backend). See `backend/.env.example`.

## Demo steps

**STEP 1 — Open LegalAkshi.** Go to `http://localhost:5174`, sign in, open
the consumer workspace. The dashboard shows existing demo (mock) content —
clearly separate from backend results.

**STEP 2 — Create inspection.** Open **Lab check** in the sidebar. The banner
must read **"Data source: LIVE backend"**. (If it reads "unavailable", the
backend is not running — no findings will be fabricated.)

**STEP 3 — Select/add packaged-food product.** Fill the declaration form,
e.g. product "Demo Whole Wheat Atta", manufacturer, 5 kg, mfg date,
MRP 250. Toggle *Imported* / *E-commerce* to change applicability.

Officer path: open **Scan & Inspect**, capture front/back photos, click **Run
OCR extraction** (real RapidOCR on the backend — values show `OCR · %`
badges). Review and correct every field; edited fields flip to `MANUAL`.
OCR failures leave blank fields for manual entry instead of crashing.

**STEP 4 — Analyze label/declaration.** Click **Run backend analysis**.
One call creates the inspection, adds the product (extras stored as manual
declarations), and runs the rule engine.

**STEP 5 — Show applicable legal requirements.** Each finding shows its
requirement text plus `source_reference` (e.g. `Rule 6(1)(e)`) from PostgreSQL.

**STEP 6 — Show PASS/FAIL findings.** Try three runs: (a) complete form →
mostly PASS; (b) blank MRP → `CHK-MRP` FAIL; (c) tick *Imported*, leave
country blank → `CHK-COUNTRY-ORIGIN` FAIL. `NOT_APPLICABLE` rows (e.g.
e-commerce checks for offline goods) never FAIL. Low-evidence rows show
`NEEDS_REVIEW`, never a silent PASS.

**STEP 7 — Show score.** Weighted `DEFAULT-2026` score out of 100 with policy
version. Note: score can be 100 while the analysis is still **not
finalizable** — see Step 8/9.

**STEP 8 — Show evidence/provenance.** Every finding lists detected value,
confidence (`none (manual)` here — never fake OCR numbers), origin
(`MANUAL`), rule version, effective dates, and source authenticity.

**STEP 9 — Show potential violation.** The blank-MRP run raises one
`PENDING` violation. State clearly: **FAIL ≠ confirmed legal offence.**

**STEP 10 — Show inspector verification.** Enter an inspector ID, click
**Confirm** (or Reject). The lab page sends the local-dev officer identity
automatically; the backend enforces the officer role (401 without it, 403 for
consumers — production uses Clerk JWT). Status flips to
`CONFIRMED`/`REJECTED` and persists (`verification_date`,
`inspector_remarks` in PostgreSQL).

**STEP 11 — Show final status.** `CHK-OTHER-MATTERS` (Rule 6(1)(g) catch-all)
always routes to review, so the report reads `NEEDS_REVIEW / not
finalizable` until a human reviews — intentional, per
`DO_NOT_FINALIZE_WITHOUT_REVIEW`. The inspector decision in Step 10 is the
final word, not the engine.

## If PostgreSQL is unavailable

The backend refuses to start (`DATABASE_URL` error) rather than serve fake
legal data. Unit/API tests still run (`python -m pytest tests -q`) via the
in-memory *test* repository; PostgreSQL integration tests report
`skipped — PostgreSQL unavailable`, never passed.
