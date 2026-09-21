⚖️ LegalAkshi
AI-Powered Food Package Compliance & Verification Platform

LegalAkshi is an intelligent regulatory-compliance platform designed to help food safety officers inspect packaged food products, extract mandatory label information, validate declarations against applicable regulations, and generate evidence-backed compliance reports — while giving consumers a simple way to verify products and raise complaints.

🏆 Smart India Hackathon
	Details
Project	LegalAkshi
Domain	Smart Governance / Food Safety / AI
Platform	Web Application
Primary Users	Food Safety Officers
Secondary Users	Consumers
Core Technologies	OCR, Computer Vision, AI Vision, Rule Engine, PostgreSQL
Architecture	React + FastAPI + PostgreSQL
Compliance Model	Evidence-driven rule evaluation
📌 Table of Contents
Problem
Our Solution
Key Features
How LegalAkshi Works
System Architecture
Inspector Module
Consumer Module
AI-Powered OCR Pipeline
Package Intelligence
Rule Engine
Self-Learning & Corrections
Complaint & Enforcement Workflow
Technology Stack
Project Structure
Database
API Architecture
Installation
Environment Configuration
Running the Project
Testing
Security & RBAC
Performance
Current Limitations
Future Roadmap
Team
🚨 Problem

Food-package compliance inspection can involve manually reading and verifying a large number of details from product packaging.

An inspector may need to verify:

Product name
Net quantity
MRP
Manufacturing / packing information
Best-before / expiry information
Manufacturer details
FSSAI licence information
Consumer-care information
Batch / lot information
Ingredients
Allergen declarations
Vegetarian / non-vegetarian symbol
Nutrition information
Other mandatory declarations

The challenge becomes significantly harder when:

packaging contains dense text,
photographs are captured using mobile devices,
lighting is poor,
text is small,
labels are curved,
information is distributed across multiple panels,
OCR introduces character substitutions,
fields are split across multiple lines,
different declarations appear close to each other.

A conventional OCR system is therefore insufficient.

💡 Our Solution

LegalAkshi combines OCR, computer vision, AI-assisted extraction, evidence reconciliation and a deterministic compliance rule engine.

Instead of simply asking:

"What text is present in this image?"

LegalAkshi asks:

"What legally relevant information can be reliably extracted from this package, what evidence supports it, and which applicable compliance requirements are satisfied?"

The system is designed around evidence-first extraction.

Every important value can retain:

source image,
OCR evidence,
bounding-box information,
confidence,
extraction method,
competing candidates,
validation status,
officer correction history.
✨ Key Features
👮 Inspector Platform
Scan & Inspect
Multi-image package inspection
OCR extraction
OpenCV image preprocessing
Region-based extraction
AI vision assistance
Field-level confidence
Evidence inspection
Automatic validation
Manual correction
Correction history
Rule-engine readiness check
Compliance analysis
Violation detection
Report generation
Inspection Management
Inspection dashboard
Inspection history
Search and filtering
Product comparison
Batch / lot search
Inspection detail pages
Evidence-backed findings
Complaints
Complaint inbox
Category filtering
Complaint lifecycle
Related inspections
Related findings
Complaint timeline
Enforcement linkage
Enforcement
Enforcement queue
Pending cases
Confirmed violations
Case history
Cross-linking with inspections and complaints
Knowledge
Regulatory rule library
Applicable requirements
Rule provenance
Compliance checks
Reports
Inspection reports
Evidence-backed findings
Compliance status
Violation details
PDF generation
👤 Consumer Platform

LegalAkshi does not require consumers to perform OCR themselves.

Instead, consumers can:

🔎 Verify a Product

Search using:

Barcode
Product code
Product name

The consumer receives a clear verification state:

VERIFIED

NEEDS REVIEW

NOT VERIFIED

"Not Verified" does not automatically mean that a product is illegal or unsafe. It means that a verified LegalAkshi inspection was not established or that an issue requires review.

📄 Product Information

Consumers can view:

Product identity
Pack size
Declaration information
Verification status
Inspection information where available
🥗 Nutrition

Consumers can access available nutritional declarations and simplified explanations.

🚨 Raise a Complaint

Consumers can submit complaints related to:

Product authenticity
Labelling
Packaging
MRP
Expiry / date information
Product quality
Other concerns
📋 Track Complaints

Consumers can see:

Complaint ID
Product
Category
Status
Timeline
Updates
Related report when available
💡 Consumer Suggestions

Consumers can suggest improvements to the regulatory ecosystem.

Example:

Introduce a standardized verification sticker or authenticity marker on packaged food products.

Suggestions can then be reviewed by authorized officers.

🔄 How LegalAkshi Works
                ┌───────────────────────┐
                │    Package Images     │
                └───────────┬───────────┘
                            ↓
                ┌───────────────────────┐
                │ Image Quality Analysis│
                │     OpenCV Pipeline    │
                └───────────┬───────────┘
                            ↓
                ┌───────────────────────┐
                │       OCR Layer       │
                │ RapidOCR + Tesseract  │
                └───────────┬───────────┘
                            ↓
                ┌───────────────────────┐
                │ Region & Field        │
                │ Detection             │
                └───────────┬───────────┘
                            ↓
                ┌───────────────────────┐
                │ AI Vision Assistance  │
                │     (Optional)        │
                └───────────┬───────────┘
                            ↓
                ┌───────────────────────┐
                │ Field Validation &    │
                │ Candidate Reconcile   │
                └───────────┬───────────┘
                            ↓
                ┌───────────────────────┐
                │ Inspector Verification│
                └───────────┬───────────┘
                            ↓
                ┌───────────────────────┐
                │    Rule Engine        │
                └───────────┬───────────┘
                            ↓
              ┌─────────────┴─────────────┐
              ↓                           ↓
      ┌───────────────┐           ┌───────────────┐
      │ Compliance    │           │  Violations   │
      │ Results       │           │  & Findings   │
      └───────┬───────┘           └───────┬───────┘
              └─────────────┬─────────────┘
                            ↓
                  ┌──────────────────┐
                  │ Evidence-backed  │
                  │ Report Generation│
                  └──────────────────┘
🧠 AI-Powered OCR Pipeline

LegalAkshi uses a multi-stage extraction architecture rather than depending on one OCR engine.

Stage 1 — Image Normalization

OpenCV/Pillow processing can perform:

EXIF correction
orientation normalization
image quality analysis
skew estimation
conservative deskewing
perspective correction when reliable
adaptive thresholding
duplicate-image detection
Stage 2 — OCR

Primary OCR:

RapidOCR

Fallback / secondary OCR:

Tesseract

OCR calls are budgeted to avoid unnecessary processing.

Stage 3 — Region Detection

Instead of treating the entire image as one text block, LegalAkshi attempts to identify regions such as:

PRODUCT
QUANTITY
MRP
DATES
FSSAI
MANUFACTURER
CONSUMER CARE
INGREDIENTS
NUTRITION
BATCH
BARCODE
QR
VEG SYMBOL

This reduces contamination between unrelated sections.

Stage 4 — AI Vision

When enabled, an AI vision provider can assist with difficult or ambiguous regions.

The vision system:

receives selected image regions,
extracts candidate values,
validates the response against field rules,
compares it with OCR,
retains conflicting candidates,
avoids silently inventing values.

AI is therefore an assistive extraction layer, not the authority for determining legality.

🔍 Package Intelligence

LegalAkshi maintains structured candidates instead of blindly accepting the first OCR result.

For example:

MRP

Candidate A
₹45
Source: RapidOCR
Confidence: 0.71

Candidate B
₹45
Source: Vision AI
Confidence: 0.88

Agreement:
YES

Final:
₹45
Status:
DETECTED

If candidates disagree:

OCR:
₹108

Vision:
₹180

↓

Status:
NEEDS_REVIEW

Both candidates retained.

This prevents a potentially incorrect AI prediction from silently entering the compliance engine.

⚖️ Rule Engine

The rule engine is deliberately separated from OCR and AI.

Extracted Facts
      ↓
Applicability
      ↓
Applicable Rules
      ↓
Checks
      ↓
Compliance Results
      ↓
Violations
      ↓
Officer Decision
      ↓
Report

This separation means:

AI extracts information.
The rule engine determines compliance.

The authoritative regulatory data remains separate from AI-generated information.

🧩 Rule-Engine Readiness

Before analysis, LegalAkshi determines which fields are required for the applicable checks.

Example:

Required Information

✓ Product
✓ Quantity
✓ MRP
✓ Manufacturer
✓ Manufacturing Date
✓ Consumer Care

Optional / Conditional

○ Best Before
○ Vegetarian Symbol
○ Origin
○ E-commerce declarations

The inspector is therefore not forced to manually determine which fields matter.

The system tells the inspector:

READY

or

READY WITH REVIEW

or

BLOCKED

with the specific missing information.

🔁 Human-in-the-Loop Learning

LegalAkshi is designed to improve through verified corrections.

The workflow is:

OCR / AI
   ↓
Candidate
   ↓
Inspector Review
   ↓
Correction
   ↓
Verified Correction
   ↓
Trusted Dataset
   ↓
Error Pattern Analysis
   ↓
Future Improvement

Only verified corrections enter the trusted learning dataset.

The system does not automatically retrain itself from every OCR prediction.

This prevents incorrect officer corrections or noisy OCR output from contaminating the learning data.

📊 Error Analysis

The learning subsystem can identify recurring patterns such as:

FSSAI:
"O" → "0"

MRP:
"R5" → "Rs"

Weight:
"WEI6HT" → "WEIGHT"

Manufacturer:
"Mfd.By" → "Mfd. By"

These patterns can guide future:

preprocessing improvements,
field validators,
OCR post-processing,
region detection,
AI prompts,
golden-set expansion.
🚨 Complaint → Inspection → Enforcement

LegalAkshi connects the consumer and officer workflows.

Consumer
   │
   │ Complaint
   ↓
Complaint System
   │
   ↓
Officer Review
   │
   ↓
Inspection
   │
   ↓
Scan & Inspect
   │
   ↓
Rule Engine
   │
   ├── PASS
   │
   └── FINDING / VIOLATION
             ↓
        Enforcement Queue
             ↓
           Report

This creates a traceable lifecycle rather than treating complaints as isolated submissions.

🏗️ System Architecture
                    FRONTEND
        ┌───────────────────────────────┐
        │           React               │
        │                               │
        │ Consumer │ Inspector │ Admin  │
        └──────────────┬────────────────┘
                       │
                       ↓
                 FastAPI Backend
                       │
        ┌──────────────┼──────────────┐
        ↓              ↓              ↓
      OCR          AI Vision       Rule Engine
        │              │              │
        └──────────────┼──────────────┘
                       ↓
               Package Intelligence
                       │
                       ↓
                PostgreSQL / Neon
                       │
        ┌──────────────┼──────────────┐
        ↓              ↓              ↓
    Inspections    Complaints      Reports
🛠️ Technology Stack
Layer	Technology
Frontend	React 19
Build	Vite
Styling	Tailwind CSS
Routing	Wouter
Authentication	Clerk
Backend	FastAPI
Language	Python
Database	PostgreSQL
Cloud Database	Neon
OCR	RapidOCR
OCR Fallback	Tesseract
Computer Vision	OpenCV
Image Processing	Pillow
AI Vision	Gemini-compatible provider
Rule Engine	Custom Python Engine
Validation	JSON Schema / Pydantic
Reports	fpdf2
Testing	Pytest
API	REST
📁 Project Structure
LegalAkshi/
│
├── artifacts/
│   └── nutricheck/
│       └── src/
│           ├── components/
│           ├── pages/
│           ├── hooks/
│           ├── lib/
│           ├── App.tsx
│           └── main.tsx
│
├── backend/
│   │
│   ├── app/
│   │   ├── core/
│   │   ├── engine/
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── routes/
│   │   └── services/
│   │       ├── ocr/
│   │       ├── vision/
│   │       ├── package_intelligence/
│   │       ├── learning/
│   │       └── ...
│   │
│   ├── authoritative/
│   ├── migrations/
│   ├── scripts/
│   ├── tests/
│   ├── requirements.txt
│   └── .env
│
├── package.json
├── pnpm-workspace.yaml
└── README.md
🗄️ Database Architecture

The backend uses PostgreSQL for persistent storage.

Major entities include:

Users
 │
 ├── Inspections
 │      ├── Products
 │      ├── Declarations
 │      ├── Compliance Results
 │      ├── Violations
 │      └── Reports
 │
 ├── Complaints
 │      └── Complaint Events
 │
 └── Suggestions
        └── Suggestion Events

Correction learning adds an append-only evidence trail for verified corrections.

🔐 Security & RBAC

LegalAkshi separates user capabilities.

Consumer
Product verification
Nutrition
Complaints
Complaint tracking
Suggestions
Officer
Inspections
OCR
AI-assisted extraction
Rule analysis
Findings
Complaints
Enforcement
Reports
Knowledge base
Suggestions review

Protected officer APIs use role-based access control.

Consumer requests cannot access officer-only operations.

⚡ Performance

The OCR system uses explicit call budgets.

Current architecture includes:

duplicate-image reuse,
targeted OCR regions,
OCR call budgeting,
provider timing diagnostics,
image quality gating,
progressive UI loading,
PostgreSQL connection pooling,
batched overview queries,
bounded history queries,
GET request caching where appropriate.

The system prioritizes useful extraction over blindly increasing OCR calls.

🧪 Testing

The backend contains an extensive automated test suite covering:

OCR
OCR accuracy behaviors
ingredient extraction
package extraction
OpenCV preprocessing
AI vision integration
package intelligence
field validation
rule engine
compliance checks
complaints
inspections
reports
RBAC
PostgreSQL integration
consumer verification
suggestions
learning/corrections
API contracts

Run:

cd backend

.venv\Scripts\python.exe -m pytest tests/ -q

Frontend type checking:

node node_modules/typescript/bin/tsc \
  -p artifacts/nutricheck/tsconfig.json \
  --noEmit
⚙️ Installation
1. Clone
git clone <YOUR_REPOSITORY_URL>
cd LegalAkshi
2. Backend
cd backend

python -m venv .venv

Windows:

.venv\Scripts\activate

Install dependencies:

pip install -r requirements.txt
3. Frontend

From project root:

pnpm install
🔑 Environment Configuration

Create:

backend/.env

Example:

DATABASE_URL=your_postgresql_connection_string

LEGALAKSHI_VISION_ENABLED=false
LEGALAKSHI_VISION_PROVIDER=
LEGALAKSHI_VISION_MODEL=
LEGALAKSHI_VISION_API_KEY=

TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
AI Vision

For local OCR-only operation:

LEGALAKSHI_VISION_ENABLED=false

For Gemini vision:

LEGALAKSHI_VISION_ENABLED=true
LEGALAKSHI_VISION_PROVIDER=gemini
LEGALAKSHI_VISION_MODEL=<model-name>
LEGALAKSHI_VISION_API_KEY=<server-side-key>

Never commit .env or API keys to GitHub.

▶️ Running LegalAkshi
Backend
cd backend

.venv\Scripts\activate

uvicorn app.main:app --reload --port 8000

Backend:

http://localhost:8000
Frontend

From the project root:

pnpm dev

The Vite development server will provide the frontend URL.

📱 Core User Journeys
Officer
Login
 ↓
Dashboard
 ↓
Create Inspection
 ↓
Upload Package Images
 ↓
OCR + OpenCV
 ↓
AI Vision (optional)
 ↓
Review Extracted Details
 ↓
Correct / Verify
 ↓
Analysis Readiness
 ↓
Rule Engine
 ↓
Compliance Findings
 ↓
Generate Report
Consumer
Login
 ↓
Verify Product
 ↓
View Verification
 ↓
Nutrition
 ↓
Raise Complaint
 ↓
Track Complaint
📈 Current Development Status
Component	Status
Consumer Verification	✅
Consumer Complaints	✅
Consumer Suggestions	✅
Nutrition Interface	✅
Inspector Dashboard	✅
Inspection Management	✅
Complaint Management	✅
Enforcement Queue	✅
Rule Library	✅
Report Generation	✅
OCR Pipeline	✅
OpenCV Preprocessing	✅
Tesseract Fallback	✅
AI Vision Architecture	✅
AI Vision Integration	🟡 Optional / Configurable
Correction Recording	✅
Learning Analytics	✅
Automatic Model Training	🔄 Future
Large Real-World Golden Dataset	🔄 In Progress
🔮 Future Roadmap
Phase 1 — Foundation
OCR
Rule engine
Inspection workflow
Consumer verification
Phase 2 — Intelligent Extraction
OpenCV preprocessing
Region-aware OCR
AI vision
Candidate reconciliation
Phase 3 — Human-in-the-Loop Learning
Verified corrections
Golden dataset
Error-pattern analysis
Field-level benchmarking
Phase 4 — Advanced Intelligence
Larger labeled package dataset
Field-specific OCR models
Improved vision models
Barcode / QR verification
Authenticity verification
Advanced package comparison
Phase 5 — Ecosystem Integration
Government-system integration where authorized
Product verification ecosystem
Authenticity markers
Regulatory analytics
Population-level compliance insights
🎯 Design Principles

LegalAkshi follows five core principles:

1. Evidence First

Every important decision should be traceable to evidence.

2. Human in the Loop

AI assists officers; it does not silently replace their verification.

3. Deterministic Compliance

The rule engine, not an LLM, determines whether a documented requirement is satisfied.

4. No Fabrication

When evidence is insufficient:

NEEDS_REVIEW

is preferable to an invented value.

5. Continuous Improvement

Verified officer corrections can become high-quality learning data for future system improvements.

🌱 Vision

LegalAkshi aims to build a bridge between:

Regulation → Inspection → Evidence → Enforcement → Consumer Awareness

By combining computer vision, OCR, AI-assisted extraction and deterministic regulatory rules, the platform aims to reduce repetitive inspection work while making compliance information more traceable and understandable.

👥 Team
Team LegalAkshi
Member	Role
Member 1	AI / Backend
Member 2	Frontend
Member 3	AI / OCR
Member 4	Backend / Database
Member 5	UI / UX
Member 6	Research / Integration

Replace the placeholders with your actual team members, roles, college and SIH details.

📜 Disclaimer

LegalAkshi is a technology prototype for assisting food-package inspection and consumer awareness.

A system-generated status should not be interpreted as a substitute for an authorized regulatory determination unless formally integrated and approved for such use.
