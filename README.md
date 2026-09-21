<<<<<<< HEAD

=======
# ⚖️ LegalAkshi

### AI-Assisted Food Package Compliance & Verification Platform

<img src="./artifacts/nutricheck/public/logo.svg" alt="LegalAkshi logo" width="72" />

![Python](https://img.shields.io/badge/Python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)
![React](https://img.shields.io/badge/React-19-61dafb)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-336791)
![Tests](https://img.shields.io/badge/Tests-pytest-yellow)

LegalAkshi is an AI-assisted food-package compliance inspection and consumer verification platform. Food safety inspectors capture package information, extract structured declarations from package images, validate the extracted information, evaluate applicable regulatory rules, record findings, and generate evidence-backed reports.

Consumers can independently verify products, access available nutrition information, raise complaints, track complaints, and submit suggestions. A deterministic rule engine — never the AI — evaluates compliance: **AI extracts, rules interpret, evidence supports, the inspector decides.**

## 🏆 Smart India Hackathon

| Item | Details |
|---|---|
| Problem statement ID | *TBD — add official SIH problem statement number* |
| Problem statement title | *TBD — add official SIH problem statement title* |
| Category | *TBD (e.g. Food Safety / Regulatory Tech)* |
| Team | See [👥 Team](#-team) |

## 🚨 Problem Statement

Packaged-food compliance inspection is still largely manual, and that creates real friction:

- Inspectors must check a large amount of label information (MRP, net quantity, dates, FSSAI number, manufacturer, consumer-care, ingredients, nutrition, symbols).
- Packaging is often difficult to read — small fonts, reflective surfaces, curved packs, multilingual text.
- Photographs taken in the field are inconsistent in lighting, angle, and quality.
- OCR output contains errors and needs repeated verification work.
- It is difficult to maintain traceable evidence from photo → extracted value → finding.
- Consumers have no easy way to verify a product or report a label issue and track it.

## 💡 Solution

LegalAkshi structures the whole flow as an evidence pipeline:

```
OCR → Computer Vision → AI Vision assistance → Field validation
  → Evidence reconciliation → Inspector verification → Rule Engine
  → Compliance findings → Reports
```

OCR and vision only **propose** information. Deterministic validators check every value, conflicting candidates are reconciled (never silently picked), the officer verifies or corrects fields, and only then does the rule engine evaluate compliance. Every finding carries its provenance and confidence origin.

## 🎯 Target Users

### 👮 Food Safety Inspectors (Officers)

Capture package photos, review extracted declarations, run rule-engine analysis, manage violations and the enforcement queue, handle consumer complaints, and generate reports. Admins additionally manage rule-sync.

### 👤 Consumers

Verify products from persisted inspection data, view nutrition details, file and track complaints, track verification reports, submit suggestions, and read awareness information.

## ✨ Key Features

### 👮 Inspector Platform

- Inspector dashboard (`/inspector/dashboard`) with queue and stats (`GET /officer/stats`)
- Inspections list and detail views with results (`/inspector/inspections`, `/inspector/inspections/:id`)
- Scan & Inspect (`/inspector/scan`): capture front/back photos → OCR review/edit → analysis → findings/violations/report
- Lab check page for manual declarations (no OCR in this flow; values stored with `MANUAL` origin)
- OCR extraction (`POST /ocr/extract`) — RapidOCR primary, Tesseract targeted fallback
- OpenCV-assisted preprocessing and quality analysis (optional, guarded; degrades gracefully)
- AI Vision assistance (disabled by default; Gemini-compatible or mock provider)
- Package Intelligence: region proposals, field candidates, OCR↔vision reconciliation, analysis readiness
- Field-level extraction with evidence and confidence; `DETECTED` / `NEEDS_REVIEW` / `NOT_DETECTED`
- Manual verification/correction of fields before analysis (`declaration_corrections`, append-only)
- Analysis readiness gate (`GET /analysis/requirements`, package-intelligence `readiness`)
- Rule Engine analysis (`POST /inspections/{id}/analyze`) with findings, score, recommendations
- Violations with officer verification (`POST /violations/{id}/verify`: `CONFIRMED` / `REJECTED` / `REQUIRES_REVIEW`)
- Complaint review and lifecycle transitions (officer-only)
- Enforcement queue, case detail, and enforcement actions with audit trail
- Rule library (`GET /rules`, `GET /rules/{check_id}`, `GET /scoring-policy`)
- Reports in JSON, HTML, and PDF (`GET /reports/{id}?format=json|html|pdf`)
- Notifications bell (officer/consumer scoped, 60s polling, mark read / read-all)
- Consumer-suggestion review (`GET/PATCH /officer/suggestions`)

### 👤 Consumer Platform

- Product verification (`/verify-product`) — lookup by barcode, product code, or product name against persisted inspections (**no consumer OCR**)
- Verification status: `VERIFIED` / `NEEDS_REVIEW` / `NOT_VERIFIED` with consumer-safe details
- Product verification details page (`/verify-product/:productId`)
- Nutrition view (`/nutrition`, `/nutrition/:productId`) — shows persisted data only, `available=false` when absent (never invented)
- Complaint creation (`/complaint/new`) and complaint tracking (`/complaints`)
- Consumer reports linked to complaints (`/reports`, `/reports/:id`)
- Consumer suggestions (`/suggestions`) with tracking
- Consumer overview dashboard (real counts from `GET /consumer/overview`)
- Awareness ticker (`AwarenessStrip`) with rotating food-safety messages

## 🔄 End-to-End Workflow

Inspector flow:

```
Package Images
  ↓
Image Quality / OpenCV preprocessing
  ↓
OCR (RapidOCR, Tesseract fallback)
  ↓
Region & Field Extraction
  ↓
AI Vision Assistance (optional, bounded)
  ↓
Validation & Candidate Reconciliation
  ↓
Inspector Verification
  ↓
Analysis Readiness
  ↓
Rule Engine
  ↓
Compliance Findings
  ↓
Report / Enforcement
```

Consumer flow:

```
Product
  ↓
Verification (barcode / code / name)
  ↓
Nutrition / Details
  ↓
Complaint
  ↓
Complaint Tracking
```

## 🧠 AI + OCR Architecture

### Image preprocessing

- Pillow: RGB conversion, upscale-if-small, autocontrast (`backend/app/services/ocr/service.py`)
- OpenCV (`backend/app/services/ocr/opencv_preprocessor.py`, optional and guarded): quality/sharpness/brightness/contrast scoring, orientation voting, deskew (≤12°), layout panels, variant ordering. `cv2` is exercised only where available; the service degrades gracefully without it.
- EXIF/orientation handling, duplicate detection and image-role classification (`image_roles.py`: `FRONT` / `BACK` / `SIDE` / `TOP` / `BOTTOM` / `UNKNOWN` / `DUPLICATE`) with per-field image priority.

### OCR

- **RapidOCR** (`rapidocr_onnxruntime`, ONNX CPU) is the primary engine — pure-Python install, no system binaries.
- **Tesseract** (`pytesseract` + Tesseract 5 binary via `LEGALAKSHI_TESSERACT_CMD`) is used solely for one targeted ingredient-crop fallback when strict gates fire. Absent binary → RapidOCR-only, never a crash.
- Targeted single-pass crop re-reads (`field_ocr.py`) focus OCR budget on unresolved fields instead of rescanning everything.

### Field extraction

Deterministic, pattern-anchored extractors (`fields.py`, `food.py`) with confidence and coherence gates — values are never placeholders. Supported fields include product name, quantity/unit, MRP, dates, FSSAI (14-digit with licence context), manufacturer, consumer-care (phone/email), batch, ingredients, nutrition rows, and veg/non-veg symbol evidence (`veg_symbol.py`: colour-blob + geometry; ambiguous → `NEEDS_REVIEW`).

### AI Vision

- Providers (`backend/app/services/vision/`): `gemini` (REST `generateContent`, stdlib `urllib`, server-side key) and `mock` (deterministic scripted candidates for tests). Resolved via `provider.py`; grouped, bounded calls (max calls per inspection, per-call timeout, per-inspection cache).
- Vision is an **assistive candidate-generation/reconciliation layer** for unresolved or high-value fields (groups A–F on cropped regions with OCR+layout context). It never decides compliance, never silently overrides OCR — conflicts and uncertain values become `NEEDS_REVIEW`, and failure falls back to the OCR-only flow.

## 🔍 Package Intelligence

`backend/app/services/package_intelligence/` proposes regions, builds per-field candidates from each source, validates, reconciles, and retains evidence:

- **Region proposals** (`regions.py`): targeted regions (quantity, MRP, dates, FSSAI, ingredients, nutrition, …) plus excluded regions (storage, preparation, marketing).
- **Field candidates** with source tracking (`OCR`, `vision`, `manual`) and confidence.
- **Validation first** (`validators.py`): per-field gates (e.g. MRP needs a currency anchor and rejects phone/FSSAI-length numbers).
- **Reconciliation** (`reconciliation.py`): full agreement → `DETECTED` (confidence ≥ 0.6); any conflict → `NEEDS_REVIEW` with **all** candidates retained; no evidence → `NOT_DETECTED`. No silent picks, no invented values.

Example — agreement:

```
OCR candidate    → ₹45
Vision candidate → ₹45
Agreement        → DETECTED (₹45)
```

Example — conflict:

```
OCR candidate    → ₹108
Vision candidate → ₹180
Conflict         → NEEDS_REVIEW (both retained for the officer)
```

## ⚖️ Rule Engine

AI/OCR = information extraction. The Rule Engine = compliance evaluation.

- **Applicability** (`backend/app/engine/applicability.py`): rule-version conditions evaluate to `REQUIRED` / `NOT_REQUIRED` / `NOT_APPLICABLE` / `CONDITIONAL` / `NEEDS_REVIEW` before any check runs.
- **Facts** (`facts.py`): inspected-product columns + `extracted_declarations` mapped to registry fields with `(value, confidence, origin)`; sub-threshold OCR confidence forces review.
- **Checks** (`checks.py`): generic handlers dispatched by `engine_check_registry` (`FIELD_PRESENT`, `REGEX_MATCH`, `DATE_VALID`, `INGREDIENT_SCREEN`, …). Missing config → `NEEDS_REVIEW`, never an invented pass/fail.
- **Scoring** (`scoring.py`): configuration-driven score from `scoring_policies` + weights; `NEEDS_REVIEW` findings make the analysis non-finalizable.
- **Orchestration** (`engine.py`): versions → applicability → checks → persisted results; `FAIL` creates violations (starting `PENDING`) plus officer notifications. The AI never determines legality.

## 🧑‍⚖️ Human-in-the-Loop Verification

```
Auto-detected → Needs Review → Officer Correction → Verified Value → Rule Engine
```

Officers verify or correct extracted fields before analysis; corrections persist append-only in `declaration_corrections` (migration `005`), and only `verified=true` rows are overlaid for reporting. Unedited OCR fields keep their engine confidence and origin; edited fields become `manual-entry`.

## 🔁 Learning & Continuous Improvement

Correction learning (`backend/app/services/learning/`) collects and analyses verified feedback — it does **not** retrain any model:

- Correction records keep original value, corrected value, confidence, status, and evidence snapshot.
- Officer verification admits rows into the trusted set; only `verified=true` rows enter the exported evaluation dataset (`evaluation.py`).
- `error_patterns.py` classifies failures (`DATE_CONFUSION`, `MRP_DIGIT_CONFUSION`, `QUANTITY_UNIT_CONFUSION`, `FSSAI_CONTEXT_MISS`, …) with counts.
- A prioritised review queue (`HIGH` / `MEDIUM` / `LOW`) surfaces the most useful examples, e.g. OCR/vision conflicts on required fields.
- Officer-only endpoints: `GET /officer/learning/error-patterns|review-queue|dataset`, `POST /officer/learning/corrections/{id}/verify`.

> "No automatic training, ever." — `backend/app/services/learning/evaluation.py`

## 🚨 Complaints & Enforcement

Implemented lifecycle (`backend/app/models/lifecycle.py`, migrations `001`–`002`):

```
Consumer Complaint
  ↓ SUBMITTED → ACKNOWLEDGED → UNDER_REVIEW → INSPECTION_SCHEDULED
  → INSPECTION_COMPLETED → ACTION_TAKEN → RESOLVED → CLOSED
Officer Review → Inspection → Finding → Violation / Case → Enforcement → Report
```

- Forward-only transitions, terminal `CLOSED`; officers drive transitions (`POST /complaints/{id}/transitions`, officer-only).
- Violations start `PENDING`; officer verify → `CONFIRMED` / `REJECTED` / `REQUIRES_REVIEW`.
- Enforcement actions (`POST /officer/cases/{id}/actions`): `confirm`, `reject`, `request_evidence`, `under_review`, `action_taken`, `close` — persisted with legal basis and audit rows.
- Consumer suggestions follow a separate 5-status workflow (`SUBMITTED → UNDER_REVIEW → ACKNOWLEDGED → ACTIONED → CLOSED`, migration `004`).

## 📄 Reports

`backend/app/services/reports.py` rebuilds reports from persisted rows in three formats (`GET /reports/{id}?format=json|html|pdf`):

- Cover/score, product identity, mandatory declarations, food/ingredients/nutrition/veg-symbol sections
- Legal findings grouped `PASS` / `FAIL` / `NEEDS_REVIEW` / `NOT_APPLICABLE`, with source references and legal versions
- Separate online-listing section (`NOT CHECKED` without listing evidence), evidence/provenance, potential violations, inspector-action disclaimer
- PDF via **fpdf2** (pure-Python, uncompressed/greppable for audit); HTML printable preview; JSON `FinalReport`

## 🏗️ System Architecture

```
React Frontend (Vite + Clerk)          http://localhost:5174
  │  REST /api/v1 (VITE_API_URL)
FastAPI backend                        http://localhost:8000 (/docs)
  │  psycopg, parameterized SQL
  ├── OCR                  (RapidOCR primary, Tesseract fallback)
  ├── Vision               (Gemini-compatible / mock, optional)
  ├── Package Intelligence (regions, reconciliation, readiness)
  ├── Rule Engine          (applicability → checks → scoring)
  ├── Learning             (corrections, error patterns, review queue)
  ├── Complaints           (lifecycle + enforcement queue)
  ├── Notifications        (officer / consumer / user audiences)
  └── Reports              (JSON / HTML / PDF via fpdf2)
  │  PostgreSQL is the runtime legal source of truth
PostgreSQL 14+                         localhost:5432 (database: legalakshi)
```

PostgreSQL holds rule versions, applicability, check definitions, scoring weights, evidence, findings, and violations — no legal requirement is hard-coded in Python. JSON files (`legalakshi_master_v4.json`, `openapi.json`) are reference projections; the in-memory repository is test-only, and the production API refuses to start without `DATABASE_URL`.

## 🛠️ Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite 7, TypeScript ~5.9, Tailwind CSS 4, Wouter 3, Clerk (`@clerk/react`), TanStack Query, shadcn/Radix UI, Zod |
| Backend | Python, FastAPI ≥ 0.115, Uvicorn, Pydantic ≥ 2.7, pydantic-settings |
| AI / CV | `rapidocr_onnxruntime` (primary OCR), `pytesseract` + Tesseract 5 binary (targeted fallback only), Pillow, NumPy, OpenCV (guarded use; arrives transitively with RapidOCR), Gemini-compatible vision via REST (optional, disabled by default) |
| Database | PostgreSQL 14+ via `psycopg` (any `DATABASE_URL`, incl. hosted Postgres) |
| Reports | `fpdf2` (PDF), server-rendered HTML, JSON |
| Testing | `pytest`, `httpx` |

## 📁 Project Structure

```
LegalAkshi-main/
├── artifacts/nutricheck/        # Main frontend (React + Vite + Clerk)
│   ├── src/pages/               # consumer-verify, inspector-workspace, officer-scan, lab-check
│   ├── src/components/          # scan slots, consumer bits (AwarenessStrip), ui/ (shadcn)
│   ├── src/lib/                 # api.ts (typed FastAPI client), auth-role.ts, demo-data.ts
│   ├── package.json             # dev / build / serve / typecheck scripts
│   └── .env.example             # VITE_CLERK_PUBLISHABLE_KEY, VITE_API_URL, PORT, BASE_PATH
├── backend/
│   ├── app/main.py + factory.py # Production entrypoint (DB required) / DI app factory
│   ├── app/routes/              # 13 routers, all mounted under /api/v1
│   ├── app/engine/              # facts, conditions, applicability, checks, scoring, engine
│   ├── app/services/ocr/        # RapidOCR, Tesseract fallback, fields, preprocessor, roles
│   ├── app/services/vision/     # gemini / mock providers, bounded extraction service
│   ├── app/services/package_intelligence/  # regions, validators, reconciliation, vision_stage
│   ├── app/services/learning/   # corrections, error_patterns, evaluation
│   ├── app/services/reports.py  # JSON / HTML / PDF report rendering
│   ├── app/repositories/        # postgres (production) / memory (tests only)
│   ├── app/models/              # schemas, complaint/suggestion lifecycles
│   ├── authoritative/           # schema + master JSON + openapi.json (verbatim reference)
│   ├── migrations/              # 001 complaints → 005 declaration_corrections
│   ├── tests/                   # pytest suite (unit + live-DB integration)
│   └── requirements.txt
├── attached_assets/             # UI screenshots referenced during development
├── DEMO.md                      # Live demo walkthrough (manual declarations, no OCR claimed)
└── README.md                    # This file
```

## 🗄️ Database Architecture

Major entities (`backend/legalakshi_schema_v3_final.sql` + `backend/migrations/`):

```
legal_sources → legal_acts → act_provisions → legal_rules → rule_versions
                                                        ↓
                                              rule_applicability
inspections → inspected_products → inspection_evidence / extracted_declarations
     ↓                ↓
compliance_results → violations → enforcement_actions / notices / legal_cases
complaints → complaint_events ──↘ (transitions can schedule inspections)
consumer_suggestions → suggestion_events
declaration_corrections (append-only officer feedback)
notifications (officer / consumer / user:<id> audiences)
engine_check_registry · scoring_policies · scoring_policy_weights · audit_logs
```

## 🔐 Security & RBAC

- Clerk authentication: RS256 JWTs verified against `CLERK_JWKS_URL` (PyJWT + JWKS); frontend bridges the Clerk session token to the API (`ApiAuthBinder` → `VITE_API_URL`).
- Roles `consumer < officer < admin`, resolved server-side: token role claim → `OFFICER_USER_IDS` / `ADMIN_USER_IDS` (verified Clerk `sub` values) → `consumer`.
- Officer-only: violation verification, officer console, corrections, learning, vision-health. Admin-only: rule sync endpoints.
- Local dev fallback: `X-LegalAkshi-Role` / `X-LegalAkshi-User` headers or `DEV_AUTH_ROLE`; dev headers are ignored once Clerk is configured. Protected routes return `401` anonymous / `403` insufficient role.
- No security certifications or production-compliance claims are made.

## 🔌 API Architecture

All routers are mounted under `/api/v1` (`backend/app/factory.py`):

| Group | Key endpoints |
|---|---|
| `inspections` | `POST/GET /inspections`, `GET /inspections/{id}`, `POST /inspections/{id}/products`, `POST /inspections/{id}/analyze`, `GET …/products/{pid}/compliance`, `GET …/results`, `GET /analysis/requirements` |
| `package-intelligence` | `POST /package-intelligence/reconcile`, `GET …/readiness`, `GET …/vision-status`, `POST …/vision-health` (officer) |
| `ocr` | `POST /ocr/extract` (multipart images, authenticated) |
| `corrections` | `POST/GET /inspections/{id}/products/{pid}/corrections` (officer) |
| `learning` | `GET /officer/learning/error-patterns|review-queue|dataset`, `POST …/corrections/{id}/verify` (officer) |
| `consumer` | `GET /consumer/products/lookup`, `GET …/{id}/verification`, `GET …/{id}/nutrition`, `GET /consumer/overview`, suggestions + reports |
| `complaints` | `POST/GET /complaints`, `GET /complaints/{id}`, `POST /complaints/{id}/transitions` (officer) |
| `officer` | `GET /officer/queue|stats|profile`, `GET/POST /officer/cases/{id}[/actions]`, suggestion review |
| `violations` | `GET /inspections/{id}/violations`, `POST /violations/{id}/verify` (officer) |
| `reports` | `GET /reports/{id}?format=json\|html\|pdf` |
| `rules` | `GET /rules`, `GET /rules/{check_id}`, `GET /scoring-policy` |
| `notifications` | `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all` |
| `admin` | `GET /admin/rules/sync/preview`, `POST /admin/rules/sync/apply`, `POST /admin/sources\|rules/checks\|rules/versions` (admin) |

Full contract: `backend/authoritative/openapi.json` (Swagger at `http://localhost:8000/docs`).

## ⚙️ Installation

Windows PowerShell (primary dev environment):

```powershell
# 1. Clone
git clone <YOUR-REPOSITORY-URL>
cd LegalAkshi-main

# 2. Backend virtual environment
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Frontend dependencies (repository root)
cd ..
pnpm install

# 4. PostgreSQL 14+ — create database and apply schema
psql -U postgres -c "CREATE DATABASE legalakshi;"
psql "postgresql://postgres:PASSWORD@localhost:5432/legalakshi" -f backend/legalakshi_schema_v3_final.sql
# Or with Docker:  cd backend; docker compose up -d postgres

# 5. Environment variables
Copy-Item backend/.env.example backend/.env   # then set DATABASE_URL (and vision keys if needed)
# Frontend: create artifacts/nutricheck/.env with VITE_CLERK_PUBLISHABLE_KEY + VITE_API_URL

# 6. Database migrations
cd backend
python scripts/apply_migrations.py

# 7. Run backend
uvicorn app.main:app --port 8000

# 8. Run frontend (repository root, new terminal)
$env:PORT="5174"; $env:BASE_PATH="/"
pnpm --filter @workspace/nutricheck run dev
# Open http://localhost:5174
```

macOS/Linux: same steps; replace step 8 with `PORT=5174 BASE_PATH=/ pnpm --filter @workspace/nutricheck run dev`.

## 🔑 Environment Variables

Backend (`backend/.env.example`):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (required — API refuses to start without it) |
| `CORS_ORIGINS` | Allowed browser origins, comma-separated, exact scheme+host+port |
| `ENGINE_VERSION` | Engine version string |
| `CLERK_JWKS_URL` / `CLERK_AUDIENCE` | Production Clerk JWT verification (empty = local-dev identity) |
| `OFFICER_USER_IDS` / `ADMIN_USER_IDS` | Server-controlled role assignment by verified Clerk user ID |
| `LEGALAKSHI_TESSERACT_CMD` | Absolute path to Tesseract 5 binary (empty = pure-RapidOCR). Example: `C:\Program Files\Tesseract-OCR\tesseract.exe` |
| `DEV_AUTH_ROLE` | Local-dev fixed role (`consumer`/`officer`/`admin`); never set in shared deployments |
| `LEGALAKSHI_VISION_PROVIDER` | `""` / `mock` / `gemini` (empty = disabled) |
| `LEGALAKSHI_VISION_MODEL` | e.g. `gemini-2.0-flash` |
| `LEGALAKSHI_VISION_API_KEY` | Server-side only; never logged or exposed (use a placeholder, never a real key) |
| `LEGALAKSHI_VISION_ENABLED` | `false` by default — vision is opt-in |

Frontend (`artifacts/nutricheck/.env.example`): `VITE_CLERK_PUBLISHABLE_KEY`, `VITE_API_URL` (default `http://localhost:8000`), `PORT` (default `5174`), `BASE_PATH` (default `/`).

## ▶️ Running the Project

```powershell
# Backend (backend/)
uvicorn app.main:app --port 8000
# Health: http://localhost:8000/healthz   Swagger: http://localhost:8000/docs

# Frontend (repository root)
$env:PORT="5174"; $env:BASE_PATH="/"
pnpm --filter @workspace/nutricheck run dev        # dev server
pnpm --filter @workspace/nutricheck run build      # production build
pnpm --filter @workspace/nutricheck run serve      # preview build
pnpm --filter @workspace/nutricheck run typecheck  # type-check
```

Live demo walkthrough: [`DEMO.md`](./DEMO.md). UI captures: [`attached_assets/`](./attached_assets/).

## 🧪 Testing

```powershell
cd backend
python -m pytest tests -q
$env:DATABASE_URL="postgresql://..."; python -m pytest tests/test_postgres_integration.py -q
```

Categories: engine, API, RBAC (incl. real RS256 round-trip), OCR, OpenCV preprocessor, package intelligence + vision integration, extraction stages, inspector/complaint/consumer/suggestion/notification/report workflows, rule sync, schema/static contracts, performance and scan benchmarks. The PostgreSQL integration file **skips** (never fake-passes) when no database is reachable. No fixed test count is claimed here — run the suite for the current number.

## 📊 Current Status

| Feature | Status |
|---|---|
| Inspector dashboard / inspections / scan & inspect | ✅ Implemented |
| OCR (RapidOCR + targeted Tesseract fallback) | ✅ Implemented |
| Package Intelligence + reconciliation + readiness | ✅ Implemented |
| AI Vision assistance (Gemini-compatible / mock) | 🟡 Partial / Configurable (opt-in, needs credentials) |
| Deterministic rule engine + scoring | ✅ Implemented |
| Officer verification + enforcement queue | ✅ Implemented |
| Complaints lifecycle + suggestions | ✅ Implemented |
| Reports (JSON / HTML / PDF) | ✅ Implemented |
| Notifications (scoped, event-driven) | ✅ Implemented |
| Consumer verification / nutrition / tracking | ✅ Implemented |
| Correction learning (collect + analyse, no retraining) | ✅ Implemented |
| Barcode/QR authenticity verification | 🔄 Planned (lookup exists; authenticity checks future) |
| Government system integration | 🔄 Planned (future, only where authorised) |

## ⚠️ Current Limitations

- OCR accuracy depends on image quality — poor lighting, blur, or glare produce `NEEDS_REVIEW` instead of guesses.
- The real-world labelled golden dataset is still limited (`backend/tests/golden/` is growing, not a benchmark).
- AI vision requires explicit configuration and API credentials, and is disabled by default.
- Uncertain or conflicting fields require officer review by design — the pipeline will not finalise them.
- Barcode/QR capabilities are currently lookup-based; authenticity verification is future work.
- There is no automatic model retraining — corrections only build a verified evaluation dataset.

## 🔮 Future Roadmap

### Short Term

- Larger labelled golden dataset for extraction evaluation
- Improved field-specific OCR targeting and image-role coverage

### Medium Term

- Stronger barcode/QR verification and authenticity checks
- Correction-driven evaluation loops and review tooling

### Long Term

- Government system integration where officially authorised
- Advanced analytics across inspections, complaints, and enforcement

All roadmap items are **future work**, not implemented features.

## 🏆 Innovation Highlights

1. **Evidence-first extraction** — every value carries provenance, confidence, and retained candidates.
2. **OCR + CV + optional AI Vision** — RapidOCR primary, OpenCV assistance, Gemini-compatible vision as a bounded helper, Tesseract only where it helps.
3. **Deterministic rule engine** — PostgreSQL-backed rules; AI never decides legality.
4. **Human-in-the-loop verification** — officers correct before analysis; corrections persist append-only.
5. **Correction learning without retraining claims** — verified data, error patterns, prioritised review queue.
6. **Consumer ↔ Officer workflow** — verification and complaints on one side, review and enforcement on the other.
7. **Complaint → Inspection → Enforcement traceability** — lifecycle states, audit rows, and evidence-linked reports.

## 👥 Team

| Name | Role |
|---|---|
| *TBD* | *TBD* |
| *TBD* | *TBD* |

*Add team members and roles here.*

## 📜 Disclaimer

LegalAkshi is an inspection-assistance and prototype system. System-generated outputs (extractions, findings, scores, reports) assist human officers and must not be represented as official regulatory determinations unless the deployment is officially authorised and integrated with the competent authority.
>>>>>>> cf93d31 (improved ocr feature)
