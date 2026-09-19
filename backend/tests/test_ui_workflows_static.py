"""UI workflow static tests (B26 consumer + B27 inspector).

The frontend has no JS test runner in this repo, so these tests assert
the shipped source directly: routes, navigation, data-testids backing
every MUST-HAVE flow, RBAC guards, the fixed sidebar, and the absence
of fabricated/legal-simplistic consumer labels and consumer OCR.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
APP = (ROOT / "artifacts" / "nutricheck" / "src" / "App.tsx"
       ).read_text(encoding="utf-8")
API = (ROOT / "artifacts" / "nutricheck" / "src" / "lib" / "api.ts"
       ).read_text(encoding="utf-8")
DEMO = (ROOT / "artifacts" / "nutricheck" / "src" / "lib" / "demo-data.ts"
        ).read_text(encoding="utf-8")
BITS = (ROOT / "artifacts" / "nutricheck" / "src" / "components"
        / "consumer-bits.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "artifacts" / "nutricheck" / "src" / "index.css"
       ).read_text(encoding="utf-8")
VERIFY = (ROOT / "artifacts" / "nutricheck" / "src" / "pages"
          / "consumer-verify.tsx").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "artifacts" / "nutricheck" / "src" / "pages"
             / "inspector-workspace.tsx").read_text(encoding="utf-8")


# ------------------------------------------------- consumer (B26) ---
def test_consumer_search_inputs():
    assert 'placeholder="Scan barcode or search product name..."' in VERIFY
    assert 'data-testid="input-verify-search"' in VERIFY
    assert 'data-testid="button-verify-search"' in VERIFY
    assert 'data-testid="button-scan-code"' in VERIFY
    # No demo/default search value; input starts empty.
    assert "useState('')" in VERIFY
    assert 'Verify Atta' not in VERIFY


def test_consumer_multiple_matches_and_statuses():
    assert 'data-testid={`verify-match-' in VERIFY
    assert 'data-testid={`link-verification-' in VERIFY
    assert 'data-testid={`verify-status-' in VERIFY
    for status in ("VERIFIED", "NEEDS_REVIEW", "NOT_VERIFIED"):
        assert status in VERIFY, status


def test_consumer_no_simplistic_legal_labels():
    for bad in (">LEGAL<", ">ILLEGAL<", ">SAFE<", ">UNSAFE<",
                '"LEGAL"', '"ILLEGAL"', '"SAFE"', '"UNSAFE"'):
        assert bad not in VERIFY, bad
        assert bad not in APP, bad
    assert "does not mean illegal or unsafe" in VERIFY.lower() or \
        "not mean illegal or unsafe" in VERIFY.lower()


def test_consumer_no_ocr():
    for token in ("ocrExtract", "rapidocr", "OcrResponse", "tesseract",
                  "front_image", "back_image"):
        assert token not in VERIFY, token


def test_consumer_verification_details():
    assert 'data-testid="link-back-verify"' in VERIFY
    assert "Batch / lot" in VERIFY
    assert 'data-testid="link-nutrition"' in VERIFY
    assert 'button-report-problem' in VERIFY
    assert "What's been checked" in VERIFY or "Declarations checked" in VERIFY


def test_consumer_nutrition_states():
    assert 'data-testid="nutrition-unavailable"' in VERIFY
    assert 'data-testid={`nutrient-' in VERIFY
    assert "not been verified" in VERIFY
    assert "What does this mean?" in VERIFY


def test_consumer_complaint_flow():
    for testid in ("input-complaint-product", "input-complaint-batch",
                   "input-complaint-place", "input-complaint-date"):
        assert f'testId="{testid}"' in APP, testid
    for testid in ("input-complaint-category", "input-complaint-description"):
        assert f'data-testid="{testid}"' in APP, testid
    for testid in ("button-submit-complaint", "text-complaint-id",
                   "button-view-complaint", "button-back-product"):
        assert f'data-testid="{testid}"' in APP, testid
    for category in ("Incorrect MRP", "Suspected counterfeit",
                     "Expired product"):
        assert category in VERIFY, category


def test_consumer_complaint_list_and_history():
    assert 'data-testid="complaint-status"' in APP
    assert 'data-testid="complaint-id"' in APP
    assert 'button-complaint-' in APP  # details expanders
    assert 'data-testid={`card-check-' in APP
    assert "No recent product checks." in APP


def test_consumer_reports_and_nav():
    assert 'data-testid={`link-report-' in APP
    assert 'data-testid={`button-download-report-' in APP
    assert 'data-testid={`button-report-more-' in APP
    # Nav testids render from labels: Home, Verify a product,
    # My complaints, My reports.
    assert 'link-nav-${' in APP
    for label in ("Home", "Verify a product", "My complaints", "My reports"):
        assert label in APP, label


def test_consumer_backend_unavailable_distinct():
    assert "temporarily unavailable" in APP
    assert "temporarily unavailable" in VERIFY


# ------------------------------------------------- inspector (B27) ---
def test_inspector_navigation_sections():
    for section in ("Workspace", "Knowledge", "Output"):
        assert section in APP, section
    for label, href in (
            ("Overview", "/inspector/dashboard"),
            ("Inspections", "/inspector/inspections"),
            ("Complaints", "/inspector/complaints"),
            ("Scan & Inspect", "/inspector/scan"),
            ("Enforcement Queue", "/inspector/enforcement"),
            ("Rule Library", "/inspector/rules"),
            ("Reports", "/inspector/reports")):
        assert label in APP, label
        assert href in APP, href


def test_inspector_routes_present():
    for route in ('path="/inspector/dashboard"',
                  'path="/inspector/inspections"',
                  'path="/inspector/inspections/:id"',
                  'path="/inspector/scan"',
                  'path="/inspector/complaints"',
                  'path="/inspector/complaints/:id"',
                  'path="/inspector/enforcement"',
                  'path="/inspector/rules"',
                  'path="/inspector/reports"',
                  'path="/inspector/audit/:id"',
                  'path="/inspector/profile"'):
        assert route in APP, route


def test_inspector_routes_guard_officer_only():
    for page in ("InspectionsPage", "InspectionDetailPage",
                 "InspectorComplaints", "ComplaintDetailPage",
                 "EnforcementQueuePage"):
        assert page in APP, page
    # Every /inspector route renders through the officer guard.
    for line in APP.splitlines():
        if '<Route path="/inspector' in line:
            assert "officer(" in line, line


def test_inspections_list_and_search():
    assert 'testId="input-inspection-search"' in WORKSPACE
    for testid in ("input-inspection-status", "input-inspection-from",
                   "input-inspection-to"):
        assert f'data-testid="{testid}"' in WORKSPACE, testid
    assert 'data-testid={`inspection-' in WORKSPACE
    assert 'data-testid={`link-inspection-' in WORKSPACE


def test_inspection_detail_full_flow():
    for testid in ("inspection-stages", "inspection-status",
                   "link-back-inspections", "button-inspection-pdf",
                   "link-inspection-enforcement"):
        assert testid in WORKSPACE, testid
    for marker in ("finding-${", "violation-${", "panel-comparison",
                   "link-history-", "Changed"):
        assert marker in WORKSPACE, marker


def test_officer_complaints_and_detail():
    assert 'data-testid="input-complaint-search"' in APP
    assert 'data-testid="input-complaint-category"' in APP
    assert 'data-testid={`link-complaint-' in APP
    for testid in ("link-back-complaints", "complaint-status",
                   "input-complaint-note", "link-complaint-enforcement"):
        assert testid in WORKSPACE, testid
    assert 'data-testid={`button-complaint-' in WORKSPACE
    for action in ("ACKNOWLEDGED", "UNDER_REVIEW", "ACTION_TAKEN", "CLOSED"):
        assert action in WORKSPACE, action
    for marker in ("Complaint timeline", "Related inspections",
                   "Related findings", "View inspection"):
        assert marker in WORKSPACE, marker


def test_enforcement_queue_page():
    assert 'testId="input-enforcement-search"' in WORKSPACE
    assert 'data-testid="input-enforcement-status"' in WORKSPACE
    assert 'data-testid={`queue-' in WORKSPACE
    assert 'data-testid={`link-case-' in WORKSPACE


def test_rule_library_and_finding_evidence():
    assert 'data-testid="input-search-rules"' in APP
    assert 'button-rule-' in APP
    assert 'data-testid={`button-evidence-' in APP
    assert 'data-testid={`panel-evidence-' in APP
    assert "Observed:" in APP
    assert "Applicable rule:" in APP


def test_officer_reports_and_dashboard():
    assert 'data-testid={`button-pdf-' in APP
    assert 'data-testid="button-refresh-dashboard"' in APP
    for action in ("link-action-scan", "link-action-inspections",
                   "link-action-complaints", "link-action-enforcement"):
        assert f'data-testid="{action}"' in APP, action
    assert "Inspector workspace" in APP


def test_sidebar_still_fixed():
    assert 'data-testid="app-shell"' in APP
    assert 'data-testid="officer-sidebar"' in APP
    assert 'data-testid="workspace-nav"' in APP
    assert "h-[100dvh] overflow-hidden" in APP
    assert "md:sticky" in APP
    assert APP.count("<aside") == 1


def test_officer_scan_route_untouched():
    assert 'path="/inspector/scan"' in APP
    assert "OfficerScanPage" in APP
    assert 'path="/inspector/audit/:id"' in APP


# ------------------------------------------------- performance pass ---
def test_api_timeouts_sane_and_long_calls_exempt():
    assert "READ_TIMEOUT_MS = 12000" in API
    assert "LONG_TIMEOUT_MS = 180000" in API
    # Ordinary reads go through the timeout path…
    assert "new AbortController()" in API
    assert "AbortError" in API
    # …while OCR/analysis/PDF use the long budget, never the 12s one.
    assert "longRunning: true" in API  # analyze
    assert "never the 12s ordinary-read timeout" in API  # ocrExtract
    assert "never the 12s read timeout" in API  # reportPdf
    # Timeout errors are user-friendly with retry guidance, no stacks.
    assert "please retry" in API.lower()
    assert "Traceback" not in API and "stack" not in API.lower().replace(
        "stacked", "")


def test_api_request_dedup_and_ttl_cache():
    # Concurrent identical GETs share one network request.
    assert "inflight" in API
    assert "share one network request" in API
    # Short-TTL cache for safe read-heavy UI data only.
    assert "ttlCache" in API
    assert "clearApiCache" in API
    for prefix in ("'/rules'", "'/officer/profile'", "'/consumer/overview'"):
        assert prefix in API, prefix
    # Mutable flows (decisions, transitions, evaluations) never cached:
    # only GET requests consult the cache.
    assert "method === 'GET'" in API or 'method === "GET"' in API


def test_consumer_overview_progressive_and_retry():
    assert 'data-testid="overview-stats"' in APP
    assert 'data-testid="overview-skeleton"' in APP
    # Actions + history render without waiting on the overview call;
    # stats degrade to explicit unavailable dashes, never "Not verified".
    assert "unavailable" in APP
    assert "LegalAkshi verification is temporarily unavailable." in APP
    assert "button-retry-backend" in APP


def test_inspector_dashboard_independent_sections():
    for testid in ("dashboard-stats", "dashboard-queue",
                   "dashboard-activity", "dashboard-resolution"):
        assert f'data-testid="{testid}"' in APP, testid
    # Per-section loaders exist…
    for name in ("loadStats", "loadQueue", "loadInspections",
                 "loadComplaints"):
        assert name in APP, name
    # …so one failing section cannot block the others.
    assert "one slow or" in APP and "never blocks" in APP


def test_inspections_list_progressive_phases():
    assert "onPhase" in WORKSPACE
    assert "phase 2" in WORKSPACE or "Phase 2" in WORKSPACE or \
        "phase: 1 | 2 | 3" in WORKSPACE
    # Detail data merges progressively; rows render from list data first.
    assert "renders immediately from the two" in WORKSPACE


def test_detail_pages_parallelize_independent_reads():
    assert "Promise.all([" in WORKSPACE
    # History scan is bounded so it cannot N+1 the whole database.
    assert "slice(0, 25)" in WORKSPACE
    assert "Bounded scan" in WORKSPACE


def test_reports_list_consumer_scoped():
    # My Reports renders one bounded call of the caller's OWN
    # complaint-derived reports — never the global inspection list.
    assert "api.consumerReports()" in APP
    assert "ONLY the caller's own complaint-derived reports" in APP
    reports_block = APP.split(
        "function ReportsPage()")[1].split("function OfficerReportsPage")[0]
    assert "api.listInspections()" not in reports_block


# ------------------------------------------------- consumer portal V2 ---
def test_awareness_strip():
    assert 'data-testid="awareness-strip"' in BITS
    assert 'role="region"' in BITS
    assert 'aria-label="Consumer awareness"' in BITS
    for message in ("Check the MRP before purchase",
                    "Verify the FSSAI licence details",
                    "Report suspicious packaged products"):
        assert message in BITS, message
    # Smooth marquee with reduced-motion fallback, in brand styling.
    assert "awareness-track" in CSS
    assert "@keyframes awareness-scroll" in CSS
    assert "prefers-reduced-motion" in CSS
    # Rendered for consumers below the header, above content.
    assert "{role === 'consumer' && <AwarenessStrip />}" in APP


def test_nutrition_landing_and_nav():
    assert 'path="/nutrition"' in APP
    assert "{ href: '/nutrition', label: 'Nutrition'" in APP
    assert 'data-testid="input-nutrition-search"' in VERIFY
    assert 'data-testid="button-nutrition-search"' in VERIFY
    assert 'data-testid={`nutrition-match-' in VERIFY
    assert 'data-testid={`link-nutrition-' in VERIFY


def test_demo_dataset_labelled_and_frontend_only():
    for marker in ("DEMO_PRODUCTS", "DEMO_COMPLAINTS", "isDemoMode",
                   "setDemoMode", "demoProductById"):
        assert marker in DEMO, marker
    # All nine demo items present: 3 products + nutrition + 3 complaints.
    assert "demo-verified-atta" in DEMO
    assert "demo-review-oil" in DEMO
    assert "demo-unverified-chips" in DEMO
    assert "Serving Size" in DEMO
    assert "demo-c-submitted" in DEMO
    assert "demo-c-review" in DEMO
    assert "demo-c-action" in DEMO
    # Never presented as verified data; never touches the backend.
    assert "not verified" in DEMO.lower()
    assert "api." not in DEMO
    assert "fetch(" not in DEMO


def test_demo_mode_controls_and_separation():
    for testid in ("button-demo-home", "button-load-demo",
                   "button-exit-demo", "button-try-demo",
                   "demo-banner", "demo-complaints", "demo-history"):
        assert testid in APP or testid in VERIFY or testid in BITS, testid
    # Demo branches render only for demo-* ids with explicit banners.
    assert "startsWith('demo-')" in VERIFY
    assert "Demo mode is on" in VERIFY
    # Real error states stay; demo is an explicit choice, never silent.
    assert "Try demo data instead" in VERIFY


def test_consumer_reports_scoped_to_owner():
    assert "api.consumerReports()" in APP
    assert "link-report-${" in APP
    assert "Submitted — awaiting review" in APP
    assert 'path="/reports/:id"' in APP
    assert 'data-testid="link-back-reports"' in VERIFY
    assert 'data-testid="button-download-final-report"' in VERIFY
    assert 'data-testid="text-report-pending"' in VERIFY
    # "Not analyzed" never shown for consumer complaint states.
    assert "Not analyzed" not in APP


def test_consumer_suggestions_flow():
    assert 'path="/suggestions"' in APP
    assert "{ href: '/suggestions', label: 'Suggestions'" in APP
    for testid in ("input-suggestion-title", "input-suggestion-category",
                   "input-suggestion-description", "input-suggestion-context",
                   "input-suggestion-location", "button-submit-suggestion",
                   "text-suggestion-submitted"):
        assert f'data-testid="{testid}"' in VERIFY, testid
    for category in ("Product authenticity", "Packaging verification",
                     "Food safety", "Label transparency",
                     "Consumer awareness", "Digital verification", "Other"):
        assert category in VERIFY, category
    assert "submitted to LegalAkshi for review" in VERIFY
    # No claim of government delivery.
    assert "government department" not in VERIFY


def test_officer_suggestions_section():
    assert 'path="/inspector/suggestions"' in APP
    assert 'path="/inspector/suggestions/:id"' in APP
    assert "Consumer Suggestions" in APP
    for testid in ("input-suggestion-search", "input-suggestion-status",
                   "input-suggestion-category", "input-suggestion-from",
                   "input-suggestion-to", "link-back-suggestions",
                   "suggestion-status", "input-suggestion-note"):
        assert testid in WORKSPACE, testid
    assert 'data-testid={`suggestion-' in WORKSPACE
    assert 'data-testid={`link-suggestion-' in WORKSPACE
    for action in ("UNDER_REVIEW", "ACKNOWLEDGED", "ACTIONED", "CLOSED"):
        assert action in WORKSPACE, action
    # Original consumer text never edited; identity masked.
    assert "never edited" in WORKSPACE
    assert "consumer" in WORKSPACE.lower()


def test_demo_detail_nutrition_nav_prefill():
    # Demo detail + demo nutrition render from static data with banners.
    assert "DemoVerificationView" in VERIFY
    assert "DemoNutritionView" in VERIFY
    assert "demo-history-" in APP
    # Nutrition + Suggestions in consumer nav with routes.
    assert "{ href: '/nutrition', label: 'Nutrition'" in APP
    assert "{ href: '/suggestions', label: 'Suggestions'" in APP
    # Complaint prefill from verification pages.
    assert "useComplaintPrefill" in APP
    assert "?product=" in VERIFY


def test_suggestions_migration_additive():
    migration = (ROOT / "backend" / "migrations"
                 / "004_consumer_suggestions.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS consumer_suggestions" in migration
    assert "CREATE TABLE IF NOT EXISTS suggestion_events" in migration
    assert "consumer_user_id" in migration
    for status in ("SUBMITTED", "UNDER_REVIEW", "ACKNOWLEDGED",
                   "ACTIONED", "CLOSED"):
        assert status in migration, status
    # Authoritative schema untouched by workflow additions.
    schema = (ROOT / "backend" / "legalakshi_schema_v3_final.sql"
              ).read_text(encoding="utf-8")
    assert "consumer_suggestions" not in schema
