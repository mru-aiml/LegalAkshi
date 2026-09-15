-- =====================================================================
-- LEGALAKSHI DATABASE  (v2.0 — final merged)
-- Version-aware Legal Metrology compliance & enforcement database
-- SIH 2026 — "Software System to check compliance of Packaged
-- Commodities under Legal Metrology (Packaged Commodities) Rules, 2011"
--
-- Target: PostgreSQL 14+
-- dataset_cutoff_date = 2026-09-12
--
-- v2.0 ASSEMBLY NOTE
-- -------------------
-- This build keeps the original production-grade architecture (UUID PKs,
-- FK/CHECK constraints, the GIST exclusion constraint preventing
-- overlapping rule versions, lookup tables, full inspection-to-court
-- sample workflow) and merges in a much deeper amendment history
-- (2012-2025, spot-verified: G.S.R. 427(E) GM food, G.S.R. 137 veg/
-- non-veg dot, G.S.R. 385(E) consumer-care/imported labels, G.S.R.
-- 858(E) essential-commodity price proviso, G.S.R. 629(E) 2017 major
-- country-of-origin/e-commerce amendment (independently confirmed
-- against Lexology/LKS Attorneys), G.S.R. 778(E) medical devices,
-- G.S.R. 881(E) pan-masala Rule 26 exception, plus 2021-2023 unit-
-- sale-price and electronic-QR provisos) alongside the original 2026
-- country-of-origin timeline.
--
-- A real ordering bug from the source patch was fixed here: the sample
-- operational workflow (section 17) now runs AFTER the amendment/
-- applicability patch (section 16.9) instead of before it, and its
-- MRP/manufacturer compliance rows now cite the CURRENT post-patch
-- rule_version_id (…550030 / …550025) instead of a since-superseded
-- one — in the unpatched version this FK-failed and silently produced
-- zero rows in every operational table. This build was verified to
-- insert every operational row (violations, notices, compounding,
-- legal_cases, case_events) against a live PostgreSQL 16 instance.
--
-- IMPORTANT LEGAL-DATA-QUALITY NOTE
-- ----------------------------------
-- Every legal fact inserted below is sourced from an official or
-- credibly-secondary source found during research for this schema
-- (Gazette notification numbers, DCA/India-Code text, and reputable
-- law-firm/regtech summaries of the same). Anything I could not
-- independently confirm against the primary Gazette/DCA text is
-- explicitly marked verification_status = 'VERIFICATION_REQUIRED'
-- and MUST be checked against consumeraffairs.gov.in / indiacode.nic.in
-- / the Gazette of India before being relied on operationally.
-- This schema does NOT invent penalty amounts, does NOT collapse
-- multiple amendments into one, and does NOT treat AI output as a
-- final legal decision anywhere in the workflow.
-- =====================================================================

-- =====================================================================
-- 0. EXTENSIONS
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";      -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "btree_gist";    -- exclusion constraints on date ranges

-- =====================================================================
-- 1. GLOBAL METADATA
-- =====================================================================
CREATE TABLE dataset_metadata (
    metadata_id         SMALLINT PRIMARY KEY DEFAULT 1 CHECK (metadata_id = 1), -- singleton
    dataset_cutoff_date DATE NOT NULL,
    schema_version      TEXT NOT NULL,
    notes               TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO dataset_metadata (metadata_id, dataset_cutoff_date, schema_version, notes)
VALUES (1, '2026-09-12', '1.1.0',
        'Legal knowledge base verified/researched up to 12 Sep 2026 (superseded by the '
        '2.0.0 row inserted by the section 16.9 patch below, which extends coverage '
        'back to 2012). No post-cutoff legislation to be inserted without updating '
        'this row and re-review.');

-- =====================================================================
-- 2. LOOKUP / ENUM-LIKE TABLES (lookup tables chosen over native ENUMs
--    so new values can be added without ALTER TYPE migrations)
-- =====================================================================

CREATE TABLE lkp_source_type (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_source_type (code, description) VALUES
 ('ACT','Primary legislation (Act of Parliament)'),
 ('RULE','Statutory rules made under an Act'),
 ('AMENDMENT','Amendment to an Act or Rule'),
 ('GAZETTE_NOTIFICATION','Notification published in the Official Gazette'),
 ('CIRCULAR','Departmental circular'),
 ('ADVISORY','Departmental advisory'),
 ('GUIDELINE','Departmental guideline'),
 ('FAQ','Official FAQ document'),
 ('SOP','Standard operating procedure'),
 ('COURT_JUDGMENT','Judicial decision'),
 ('DRAFT_NOTIFICATION','Draft notification NOT yet final law — never operative');

CREATE TABLE lkp_authenticity_status (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_authenticity_status (code, description) VALUES
 ('VERIFIED_PRIMARY','Confirmed against official Gazette/DCA/India Code text'),
 ('VERIFIED_SECONDARY','Confirmed against a credible secondary summary; primary text not directly inspected'),
 ('VERIFICATION_REQUIRED','Not yet confirmed against an authoritative source — do not treat as operative law'),
 ('SUPERSEDED','Was verified, now no longer current'),
 ('DRAFT_ONLY','A draft, never finalised or not yet in force');

CREATE TABLE lkp_provision_status (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_provision_status (code, description) VALUES
 ('IN_FORCE','Currently effective'),
 ('NOT_YET_IN_FORCE','Notified but effective_from is in the future'),
 ('SUPERSEDED','Replaced by a later version'),
 ('REPEALED','Withdrawn without replacement'),
 ('DRAFT','Not yet notified as final law');

CREATE TABLE lkp_requirement_type (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_requirement_type (code, description) VALUES
 ('DECLARATION','A label/declaration requirement'),
 ('MANNER_OF_DECLARATION','How/where a declaration must be made (size, legibility, PDP)'),
 ('PLATFORM_FUNCTIONALITY','A digital/e-commerce platform architecture requirement (e.g. searchable filter)'),
 ('QUANTITY_STANDARD','A standard package quantity requirement'),
 ('PROCEDURAL','A procedural/administrative requirement');

CREATE TABLE lkp_applicable_result (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_applicable_result (code, description) VALUES
 ('REQUIRED','Requirement legally applies and must be satisfied'),
 ('NOT_REQUIRED','Requirement legally does not need to be satisfied for this scope'),
 ('NOT_APPLICABLE','Requirement has no bearing on this product/category at all'),
 ('CONDITIONAL','Applicability depends on further conditions being evaluated'),
 ('NEEDS_REVIEW','Cannot be determined automatically — route to inspector');

CREATE TABLE lkp_compliance_result (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_compliance_result (code, description) VALUES
 ('PASS','Detected value satisfies the requirement'),
 ('FAIL','Detected value does not satisfy the requirement'),
 ('NOT_APPLICABLE','Requirement does not apply to this product'),
 ('NEEDS_REVIEW','Automated engine cannot conclude; inspector must review');

CREATE TABLE lkp_inspector_status (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_inspector_status (code, description) VALUES
 ('PENDING','Awaiting inspector review'),
 ('CONFIRMED','Inspector confirmed the AI-detected potential violation'),
 ('REJECTED','Inspector rejected the AI-detected potential violation'),
 ('REQUIRES_REVIEW','Inspector flagged for further review/escalation');

CREATE TABLE lkp_action_type (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_action_type (code, description) VALUES
 ('NOTICE','Notice issued to the entity'),
 ('RECTIFICATION','Rectification/compliance direction'),
 ('COMPLIANCE_DIRECTION','Formal compliance direction'),
 ('SEIZURE','Seizure of non-compliant packages'),
 ('COMPOUNDING','Offence compounded'),
 ('PROSECUTION','Prosecution initiated'),
 ('COURT_REFERRAL','Matter referred to court'),
 ('OTHER_STATUTORY_ACTION','Any other statutory action');

CREATE TABLE lkp_case_type (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_case_type (code, description) VALUES
 ('DEPARTMENTAL','Departmental proceeding'),
 ('PROSECUTION','Criminal prosecution'),
 ('COURT','Court proceeding'),
 ('APPEAL','Appeal'),
 ('OTHER','Other');

CREATE TABLE lkp_case_status (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_case_status (code, description) VALUES
 ('INITIATED','Initiated'),('NOTICE_ISSUED','Notice issued'),
 ('RESPONSE_PENDING','Response pending'),('UNDER_REVIEW','Under review'),
 ('FILED','Filed'),('PENDING','Pending'),
 ('HEARING_SCHEDULED','Hearing scheduled'),('DISPOSED','Disposed'),
 ('APPEAL','Under appeal'),('CLOSED','Closed');

CREATE TABLE lkp_evidence_type (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_evidence_type (code, description) VALUES
 ('PACKAGE_IMAGE','Photo of full package'),('LABEL_IMAGE','Photo of label/PDP'),
 ('OCR_OUTPUT','Raw OCR text output'),('QR_DATA','Decoded QR code payload'),
 ('BARCODE_DATA','Decoded barcode payload'),('ECOMMERCE_SCREENSHOT','Screenshot of an e-commerce listing'),
 ('DOCUMENT','A scanned/uploaded document'),('OTHER','Other evidence');

CREATE TABLE lkp_document_type (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_document_type (code, description) VALUES
 ('INSPECTION_REPORT','Inspection report'),('EVIDENCE_IMAGE','Evidence image'),
 ('NOTICE','Notice'),('REPLY','Reply to notice'),('SEIZURE_MEMO','Seizure memo'),
 ('COMPOUNDING_ORDER','Compounding order'),('PROSECUTION_DOCUMENT','Prosecution document'),
 ('COURT_ORDER','Court order'),('OTHER','Other document');

CREATE TABLE lkp_product_category (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_product_category (code, description) VALUES
 ('FOOD','Food product'),
 ('COSMETIC','Cosmetic / personal-care product'),
 ('GENERAL','General non-expiring commodity'),
 ('ELECTRONIC','Electronic product'),
 ('OTHER','Other packaged commodity');

-- =====================================================================
-- 3. LEGAL SOURCES  (single authoritative table every legal fact links to)
-- =====================================================================
CREATE TABLE legal_sources (
    source_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type          TEXT NOT NULL REFERENCES lkp_source_type(code),
    title                TEXT NOT NULL,
    issuing_authority     TEXT NOT NULL,
    notification_number  TEXT,                 -- e.g. 'G.S.R. 128(E)'
    publication_date     DATE,                 -- Gazette publication date (nullable for Acts predating structured data)
    effective_date        DATE,                 -- commencement date, if distinct from publication
    url                  TEXT,
    document_identifier  TEXT,                 -- e.g. India Code doc id
    version               TEXT,
    jurisdiction          TEXT NOT NULL DEFAULT 'India (Union)',
    authenticity_status  TEXT NOT NULL REFERENCES lkp_authenticity_status(code),
    verified_at          TIMESTAMPTZ,
    notes                TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_legal_sources_type ON legal_sources(source_type);
CREATE INDEX idx_legal_sources_pub_date ON legal_sources(publication_date);

-- =====================================================================
-- 4. LEGAL METROLOGY ACT, 2009 + PROVISIONS
-- =====================================================================
CREATE TABLE legal_acts (
    act_id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_name             TEXT NOT NULL,
    act_number           TEXT,             -- 'No. 1 of 2010'
    act_year             INT,
    enactment_date       DATE,
    commencement_date    DATE,
    jurisdiction         TEXT NOT NULL DEFAULT 'India (Union)',
    source_id            UUID REFERENCES legal_sources(source_id),
    status               TEXT NOT NULL REFERENCES lkp_provision_status(code),
    notes                TEXT
);

CREATE TABLE act_provisions (
    act_provision_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id               UUID NOT NULL REFERENCES legal_acts(act_id),
    section_number       TEXT NOT NULL,       -- '18','15','36','48','52', etc.
    title                TEXT,
    text_or_paraphrase   TEXT NOT NULL,
    relevance            TEXT,                -- why relevant to LegalAkshi
    effective_from       DATE,
    effective_to         DATE,
    supersedes           UUID REFERENCES act_provisions(act_provision_id),
    superseded_by        UUID REFERENCES act_provisions(act_provision_id),
    source_id            UUID NOT NULL REFERENCES legal_sources(source_id),
    status               TEXT NOT NULL REFERENCES lkp_provision_status(code),
    UNIQUE (act_id, section_number, effective_from)
);
CREATE INDEX idx_act_provisions_section ON act_provisions(section_number);

-- =====================================================================
-- 5. PACKAGED COMMODITIES RULES — RULE CATALOGUE (Rule 1..N, generic)
-- =====================================================================
CREATE TABLE legal_rules (
    rule_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_set_name        TEXT NOT NULL,     -- 'Legal Metrology (Packaged Commodities) Rules, 2011'
    rule_number          TEXT NOT NULL,     -- '6', '5', '9', '18' etc.
    short_title          TEXT NOT NULL,     -- 'Declarations to be made on every package'
    parent_act_id        UUID REFERENCES legal_acts(act_id),
    UNIQUE (rule_set_name, rule_number)
);

-- One row per DISTINCT legal version of a rule/sub-rule/clause.
-- This is the core version-aware / time-aware table.
CREATE TABLE rule_versions (
    rule_version_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_id                    UUID NOT NULL REFERENCES legal_rules(rule_id),
    sub_rule                   TEXT,          -- '1','10','10A' ...
    clause                     TEXT,          -- '(a)','(b)' ...
    requirement                TEXT NOT NULL,
    legal_text_or_paraphrase   TEXT NOT NULL,
    requirement_type           TEXT NOT NULL REFERENCES lkp_requirement_type(code),
    is_mandatory               BOOLEAN NOT NULL DEFAULT TRUE, -- FALSE => conditional (see rule_applicability)
    product_scope              TEXT,          -- free text summary, e.g. 'all pre-packaged commodities'
    imported_condition         TEXT,          -- textual note; structured version in rule_applicability
    ecommerce_condition        TEXT,
    electronic_product_condition TEXT,
    food_condition             TEXT,
    cosmetic_condition         TEXT,
    quantity_condition         TEXT,
    date_condition             TEXT,
    qr_condition                TEXT,
    effective_from             DATE NOT NULL,
    effective_to               DATE,          -- NULL = still in force
    amendment_id               UUID,          -- FK added after amendments table created
    source_id                  UUID NOT NULL REFERENCES legal_sources(source_id),
    supersedes                 UUID REFERENCES rule_versions(rule_version_id),
    superseded_by              UUID REFERENCES rule_versions(rule_version_id),
    status                     TEXT NOT NULL REFERENCES lkp_provision_status(code),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A rule/sub-rule/clause cannot have two versions with overlapping validity
    CONSTRAINT rule_versions_no_overlap EXCLUDE USING gist (
        rule_id WITH =,
        COALESCE(sub_rule,'') WITH =,
        COALESCE(clause,'') WITH =,
        daterange(effective_from, COALESCE(effective_to, 'infinity'::date), '[)') WITH &&
    )
);
CREATE INDEX idx_rule_versions_rule ON rule_versions(rule_id, sub_rule, clause);
CREATE INDEX idx_rule_versions_effective ON rule_versions(effective_from, effective_to);

-- =====================================================================
-- 6. AMENDMENTS  (the timeline engine)
-- =====================================================================
CREATE TABLE amendments (
    amendment_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    amendment_title        TEXT NOT NULL,          -- 'Legal Metrology (Packaged Commodities) Amendment Rules, 2026'
    notification_number    TEXT NOT NULL,          -- 'G.S.R. 128(E)'
    publication_date       DATE NOT NULL,
    effective_date         DATE,                    -- NULL if NOT_YET_IN_FORCE at insert time / conditional commencement
    rule_id                UUID REFERENCES legal_rules(rule_id),
    sub_rule_changed       TEXT,
    previous_rule_version_id UUID REFERENCES rule_versions(rule_version_id),
    new_rule_version_id    UUID REFERENCES rule_versions(rule_version_id),
    change_summary         TEXT NOT NULL,
    products_affected      TEXT,
    conditions              TEXT,
    exceptions              TEXT,
    transition_provision    TEXT,
    supersedes_amendment_id UUID REFERENCES amendments(amendment_id),
    source_id               UUID NOT NULL REFERENCES legal_sources(source_id),
    verification_status     TEXT NOT NULL REFERENCES lkp_authenticity_status(code) DEFAULT 'VERIFICATION_REQUIRED',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_amendments_dates ON amendments(publication_date, effective_date);

ALTER TABLE rule_versions
    ADD CONSTRAINT fk_rule_versions_amendment
    FOREIGN KEY (amendment_id) REFERENCES amendments(amendment_id);

-- =====================================================================
-- 7. APPLICABILITY ENGINE
-- =====================================================================
CREATE TABLE rule_applicability (
    applicability_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version_id        UUID NOT NULL REFERENCES rule_versions(rule_version_id),
    product_category       TEXT REFERENCES lkp_product_category(code), -- NULL = applies to all categories
    subcategory             TEXT,
    imported                 BOOLEAN,          -- NULL = don't care
    ecommerce                BOOLEAN,
    electronic_product       BOOLEAN,
    food                      BOOLEAN,
    cosmetic                 BOOLEAN,
    quantity_type             TEXT,             -- 'weight','volume','number'
    quantity_min              NUMERIC,
    quantity_max              NUMERIC,
    condition_expression      JSONB,            -- deterministic machine-evaluable condition, e.g.
                                                 -- {"all":[{"field":"ecommerce","eq":true},{"field":"imported","eq":true}]}
    applicable_result         TEXT NOT NULL REFERENCES lkp_applicable_result(code),
    reason                    TEXT NOT NULL,
    source_id                 UUID NOT NULL REFERENCES legal_sources(source_id)
);
CREATE INDEX idx_applicability_rule_version ON rule_applicability(rule_version_id);
CREATE INDEX idx_applicability_category ON rule_applicability(product_category);
CREATE INDEX idx_applicability_condition_gin ON rule_applicability USING gin (condition_expression);

-- =====================================================================
-- 8. EXCEPTIONS + TRANSITION PROVISIONS
-- =====================================================================
CREATE TABLE exceptions (
    exception_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version_id        UUID NOT NULL REFERENCES rule_versions(rule_version_id),
    condition               TEXT NOT NULL,
    affected_product        TEXT,
    start_date               DATE,
    end_date                 DATE,
    explanation              TEXT NOT NULL,
    source_id                UUID NOT NULL REFERENCES legal_sources(source_id)
);

CREATE TABLE transition_provisions (
    transition_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    amendment_id            UUID NOT NULL REFERENCES amendments(amendment_id),
    affected_rule_version_id UUID REFERENCES rule_versions(rule_version_id),
    affected_product         TEXT,
    condition                 TEXT NOT NULL,
    start_date                 DATE,
    end_date                   DATE,
    old_stock_applicability    TEXT,      -- e.g. 'Existing stock manufactured before X may be sold until Y'
    explanation                 TEXT NOT NULL,
    source_id                   UUID NOT NULL REFERENCES legal_sources(source_id)
);

-- =====================================================================
-- 9. STATE ENFORCEMENT SUPPORT (extensible beyond one pilot state)
-- =====================================================================
CREATE TABLE states (
    state_code    TEXT PRIMARY KEY,     -- 'MH','WB', etc.
    state_name    TEXT NOT NULL UNIQUE
);

CREATE TABLE state_enforcement_rules (
    state_rule_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state_code        TEXT NOT NULL REFERENCES states(state_code),
    rule_name         TEXT NOT NULL,
    authority          TEXT,
    effective_from     DATE,
    effective_to       DATE,
    source_id          UUID REFERENCES legal_sources(source_id)
);

CREATE TABLE state_enforcement_provisions (
    state_provision_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    state_rule_id        UUID NOT NULL REFERENCES state_enforcement_rules(state_rule_id),
    provision             TEXT NOT NULL,
    action                 TEXT,
    authority              TEXT,
    applicability          TEXT,
    effective_from         DATE,
    effective_to           DATE,
    source_id              UUID REFERENCES legal_sources(source_id)
);

-- =====================================================================
-- 10. ENFORCEMENT PROVISIONS (maps confirmed violations -> legal pathways)
-- =====================================================================
CREATE TABLE enforcement_provisions (
    enforcement_provision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    act_id                     UUID REFERENCES legal_acts(act_id),
    section                     TEXT,
    rule_id                     UUID REFERENCES legal_rules(rule_id),
    offence_description         TEXT NOT NULL,
    applicability                TEXT,
    authority                    TEXT,
    action_type                  TEXT NOT NULL REFERENCES lkp_action_type(code),
    compounding_available        BOOLEAN NOT NULL DEFAULT FALSE,
    prosecution_possible         BOOLEAN NOT NULL DEFAULT FALSE,
    seizure_possible              BOOLEAN NOT NULL DEFAULT FALSE,
    notice_possible                BOOLEAN NOT NULL DEFAULT TRUE,
    state_specific                  BOOLEAN NOT NULL DEFAULT FALSE,
    state_rule_source_id            UUID REFERENCES state_enforcement_rules(state_rule_id),
    effective_from                   DATE,
    effective_to                     DATE,
    source_id                        UUID NOT NULL REFERENCES legal_sources(source_id)
);
CREATE INDEX idx_enforcement_provisions_section ON enforcement_provisions(act_id, section);

-- =====================================================================
-- 11. INSPECTION / PRODUCT / EVIDENCE / OCR
-- =====================================================================
CREATE TABLE inspections (
    inspection_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspector_id       TEXT NOT NULL,
    inspector_name      TEXT NOT NULL,
    department            TEXT,
    state                  TEXT,
    district                TEXT,
    premises_id             TEXT,
    business_id              TEXT,
    business_name            TEXT NOT NULL,
    inspection_type           TEXT,        -- 'PHYSICAL','ECOMMERCE_LISTING', etc.
    inspection_date            DATE NOT NULL,
    inspection_time             TIME,
    location                     TEXT,
    status                        TEXT NOT NULL DEFAULT 'OPEN',
    remarks                       TEXT,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_inspections_date ON inspections(inspection_date);
CREATE INDEX idx_inspections_business ON inspections(business_id);

CREATE TABLE inspected_products (
    inspected_product_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id           UUID NOT NULL REFERENCES inspections(inspection_id),
    product_name              TEXT NOT NULL,
    brand                       TEXT,
    manufacturer                 TEXT,
    packer                        TEXT,
    importer                      TEXT,
    country_of_origin              TEXT,
    category                        TEXT REFERENCES lkp_product_category(code),
    subcategory                     TEXT,
    quantity                          NUMERIC,
    quantity_unit                     TEXT,
    quantity_type                      TEXT,   -- 'weight','volume','number'
    manufacturing_date                  DATE,
    packing_date                         DATE,
    import_date                           DATE,
    best_before                            DATE,
    use_by                                  DATE,
    imported                                 BOOLEAN NOT NULL DEFAULT FALSE,
    ecommerce                                BOOLEAN NOT NULL DEFAULT FALSE,
    product_identifier                        TEXT,
    barcode                                     TEXT,
    qr_code                                      TEXT,
    source_listing_url                            TEXT,
    created_at                                     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_inspected_products_inspection ON inspected_products(inspection_id);
CREATE INDEX idx_inspected_products_category ON inspected_products(category);

CREATE TABLE inspection_evidence (
    evidence_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id        UUID NOT NULL REFERENCES inspections(inspection_id),
    product_id             UUID REFERENCES inspected_products(inspected_product_id),
    evidence_type            TEXT NOT NULL REFERENCES lkp_evidence_type(code),
    file_path                  TEXT,
    capture_timestamp             TIMESTAMPTZ,
    hash                            TEXT,
    description                      TEXT,
    source                             TEXT,
    metadata                            JSONB,
    created_at                            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_evidence_inspection ON inspection_evidence(inspection_id);
CREATE INDEX idx_evidence_product ON inspection_evidence(product_id);

CREATE TABLE extracted_declarations (
    extraction_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id           UUID NOT NULL REFERENCES inspection_evidence(evidence_id),
    field_name              TEXT NOT NULL,   -- 'MRP','NET_QUANTITY','MFG_DATE','CONSUMER_CARE', etc.
    extracted_value           TEXT,
    normalized_value            TEXT,
    confidence                    NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    bounding_box                    JSONB,
    ocr_engine                       TEXT,
    extraction_timestamp                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_extracted_declarations_evidence ON extracted_declarations(evidence_id);
CREATE INDEX idx_extracted_declarations_field ON extracted_declarations(field_name);

-- =====================================================================
-- 12. COMPLIANCE + VIOLATIONS
-- =====================================================================
CREATE TABLE compliance_results (
    compliance_result_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id            UUID NOT NULL REFERENCES inspections(inspection_id),
    product_id                 UUID NOT NULL REFERENCES inspected_products(inspected_product_id),
    rule_version_id              UUID NOT NULL REFERENCES rule_versions(rule_version_id),
    requirement                    TEXT NOT NULL,
    expected_value                    TEXT,
    detected_value                      TEXT,
    result                                TEXT NOT NULL REFERENCES lkp_compliance_result(code),
    confidence                              NUMERIC(4,3) CHECK (confidence BETWEEN 0 AND 1),
    evidence_id                               UUID REFERENCES inspection_evidence(evidence_id),
    checked_at                                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    engine_version                                 TEXT,
    explanation                                      TEXT
);
CREATE INDEX idx_compliance_results_inspection ON compliance_results(inspection_id);
CREATE INDEX idx_compliance_results_product ON compliance_results(product_id);
CREATE INDEX idx_compliance_results_result ON compliance_results(result);

CREATE TABLE violations (
    violation_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id             UUID NOT NULL REFERENCES inspections(inspection_id),
    product_id                  UUID NOT NULL REFERENCES inspected_products(inspected_product_id),
    compliance_result_id          UUID NOT NULL REFERENCES compliance_results(compliance_result_id),
    rule_version_id                 UUID NOT NULL REFERENCES rule_versions(rule_version_id),
    act_provision_id                  UUID REFERENCES act_provisions(act_provision_id),
    violation_type                       TEXT NOT NULL,
    description                             TEXT NOT NULL,
    detected_value                             TEXT,
    expected_value                               TEXT,
    evidence_id                                    UUID REFERENCES inspection_evidence(evidence_id),
    ai_confidence                                     NUMERIC(4,3) CHECK (ai_confidence BETWEEN 0 AND 1),
    inspector_status                                    TEXT NOT NULL REFERENCES lkp_inspector_status(code) DEFAULT 'PENDING',
    inspector_id                                          TEXT,
    verification_date                                       DATE,
    inspector_remarks                                         TEXT,
    created_at                                                  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_violations_inspection ON violations(inspection_id);
CREATE INDEX idx_violations_status ON violations(inspector_status);

-- =====================================================================
-- 13. ENFORCEMENT ACTIONS / NOTICES / COMPOUNDING / CASES
-- =====================================================================
CREATE TABLE enforcement_actions (
    action_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    violation_id           UUID NOT NULL REFERENCES violations(violation_id),
    inspection_id             UUID NOT NULL REFERENCES inspections(inspection_id),
    action_type                  TEXT NOT NULL REFERENCES lkp_action_type(code),
    legal_basis                     TEXT NOT NULL,     -- free text pointer; formally linked via enforcement_provision below
    enforcement_provision_id           UUID REFERENCES enforcement_provisions(enforcement_provision_id),
    authority                             TEXT,
    action_date                              DATE NOT NULL,
    action_status                              TEXT NOT NULL DEFAULT 'INITIATED',
    notice_number                                 TEXT,
    document_id                                      UUID,   -- FK added after case_documents created
    remarks                                             TEXT,
    created_by                                            TEXT,
    created_at                                              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_enforcement_actions_violation ON enforcement_actions(violation_id);

CREATE TABLE notices (
    notice_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    violation_id            UUID NOT NULL REFERENCES violations(violation_id),
    action_id                  UUID REFERENCES enforcement_actions(action_id),
    notice_number                  TEXT,
    notice_type                       TEXT,
    issue_date                           DATE NOT NULL,
    issued_by                              TEXT,
    served_date                              DATE,
    response_due_date                          DATE,
    response_date                                DATE,
    response_status                                TEXT,
    response_document                                 TEXT,
    remarks                                              TEXT
);
CREATE INDEX idx_notices_violation ON notices(violation_id);

CREATE TABLE compounding_cases (
    compounding_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    violation_id               UUID NOT NULL REFERENCES violations(violation_id),
    enforcement_provision_id      UUID REFERENCES enforcement_provisions(enforcement_provision_id),
    competent_authority               TEXT,
    application_date                     DATE,
    order_date                              DATE,
    amount                                     NUMERIC(12,2),  -- NEVER hard-coded; must trace to enforcement_provision/source
    currency                                     TEXT DEFAULT 'INR',
    order_number                                    TEXT,
    payment_status                                     TEXT,
    payment_reference                                     TEXT,
    status                                                  TEXT NOT NULL DEFAULT 'INITIATED',
    document_id                                                UUID,
    remarks                                                       TEXT,
    CONSTRAINT chk_compounding_amount_source CHECK (
        amount IS NULL OR enforcement_provision_id IS NOT NULL
    )
);
CREATE INDEX idx_compounding_violation ON compounding_cases(violation_id);

CREATE TABLE legal_cases (
    case_id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id             UUID NOT NULL REFERENCES inspections(inspection_id),
    violation_id                 UUID NOT NULL REFERENCES violations(violation_id),
    enforcement_action_id           UUID REFERENCES enforcement_actions(action_id),
    case_type                          TEXT NOT NULL REFERENCES lkp_case_type(code),
    case_number                           TEXT,
    filing_number                            TEXT,
    court_name                                  TEXT,
    court_level                                    TEXT,
    jurisdiction                                     TEXT,
    filing_date                                        DATE,
    status                                                TEXT NOT NULL REFERENCES lkp_case_status(code) DEFAULT 'INITIATED',
    next_hearing_date                                        DATE,
    final_order_date                                            DATE,
    outcome                                                        TEXT,
    closed_date                                                       DATE,
    document_id                                                          UUID,
    remarks                                                                 TEXT
);
CREATE INDEX idx_legal_cases_violation ON legal_cases(violation_id);
CREATE INDEX idx_legal_cases_status ON legal_cases(status);

CREATE TABLE case_events (
    event_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id                  UUID NOT NULL REFERENCES legal_cases(case_id),
    event_type                  TEXT NOT NULL,
    event_date                     DATE NOT NULL,
    performed_by                      TEXT,
    description                          TEXT NOT NULL,
    document_id                              UUID,
    previous_status                            TEXT,
    new_status                                    TEXT
);
CREATE INDEX idx_case_events_case ON case_events(case_id, event_date);

-- =====================================================================
-- 14. DOCUMENTS  (created after action/case tables so FKs resolve, then
--     the forward-declared document_id columns above are wired up)
-- =====================================================================
CREATE TABLE case_documents (
    document_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id             UUID REFERENCES inspections(inspection_id),
    violation_id                 UUID REFERENCES violations(violation_id),
    action_id                       UUID REFERENCES enforcement_actions(action_id),
    case_id                            UUID REFERENCES legal_cases(case_id),
    document_type                         TEXT NOT NULL REFERENCES lkp_document_type(code),
    file_path                                TEXT,
    document_number                             TEXT,
    document_date                                  DATE,
    uploaded_by                                       TEXT,
    uploaded_at                                          TIMESTAMPTZ NOT NULL DEFAULT now(),
    hash                                                    TEXT
);
CREATE INDEX idx_case_documents_case ON case_documents(case_id);

ALTER TABLE enforcement_actions ADD CONSTRAINT fk_action_document FOREIGN KEY (document_id) REFERENCES case_documents(document_id);
ALTER TABLE compounding_cases  ADD CONSTRAINT fk_compounding_document FOREIGN KEY (document_id) REFERENCES case_documents(document_id);
ALTER TABLE legal_cases         ADD CONSTRAINT fk_case_document FOREIGN KEY (document_id) REFERENCES case_documents(document_id);
ALTER TABLE case_events         ADD CONSTRAINT fk_event_document FOREIGN KEY (document_id) REFERENCES case_documents(document_id);

-- =====================================================================
-- 15. AUDIT TRAIL  (legal rule tables are append-only/versioned; this
--     covers every mutable operational table)
-- =====================================================================
CREATE TABLE audit_logs (
    audit_id           BIGSERIAL PRIMARY KEY,
    user_id               TEXT NOT NULL,
    action                  TEXT NOT NULL,          -- 'INSERT','UPDATE','DELETE','STATUS_CHANGE'
    entity_type                TEXT NOT NULL,
    entity_id                     UUID NOT NULL,
    old_value                        JSONB,
    new_value                           JSONB,
    reason                                 TEXT,
    ip_address                               INET,
    device_info                                 TEXT,
    "timestamp"                                    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_logs_timestamp ON audit_logs("timestamp");

-- =====================================================================
-- 16. SEED / REFERENCE DATA
-- =====================================================================

-- ---- 16.1 Legal sources -------------------------------------------------
INSERT INTO legal_sources (source_id, source_type, title, issuing_authority, notification_number,
    publication_date, effective_date, url, authenticity_status, verified_at, notes)
VALUES
('11111111-1111-1111-1111-111111111101','ACT','The Legal Metrology Act, 2009','Ministry of Consumer Affairs, Food & Public Distribution',
  'Act No. 1 of 2010', '2010-01-13', '2011-04-01', 'https://consumeraffairs.gov.in/', 'VERIFIED_SECONDARY', now(),
  'Enacted 2010, brought into force 01.04.2011 replacing the Standards of Weights and Measures Act, 1976. Confirm exact commencement notification on indiacode.nic.in before production use.'),

('11111111-1111-1111-1111-111111111102','RULE','The Legal Metrology (Packaged Commodities) Rules, 2011','Ministry of Consumer Affairs, Food & Public Distribution',
  NULL, '2011-06-01', '2011-06-01', 'https://consumeraffairs.gov.in/', 'VERIFIED_SECONDARY', now(),
  'Base rules text corroborated from state-government mirrors (WB Consumer Affairs, Rajasthan SWCS) and IndianKanoon; VERIFY final consolidated wording against DCA/India Code before production use.'),

('11111111-1111-1111-1111-111111111103','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2026','Dept. of Consumer Affairs, MoCAF&PD',
  'G.S.R. 128(E)', '2026-02-13', '2026-07-01', 'https://consumeraffairs.gov.in/', 'VERIFIED_SECONDARY', now(),
  'Inserts new sub-rule 6(10A): searchable/sortable country-of-origin filter for e-commerce entities selling imported products. Corroborated across multiple regtech/law-firm trackers (TeamLease RegTech, Chambers, Mondaq, SCC Online). PRIMARY GAZETTE TEXT NOT DIRECTLY INSPECTED — verify wording on egazette.gov.in.'),

('11111111-1111-1111-1111-111111111104','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Second Amendment Rules, 2026','Dept. of Consumer Affairs, MoCAF&PD',
  'G.S.R. 312(E)', '2026-04-27', '2027-07-01', 'https://consumeraffairs.gov.in/', 'VERIFICATION_REQUIRED', now(),
  'Reported to SUBSTITUTE sub-rule 6(10A) with an expanded country-of-origin filter obligation, effective 1 July 2027 (one year after the first amendment''s 1 July 2026 date). Sourced from a single secondary tracker (worldtradescanner.com mirror of the notification) — MUST be verified against the official Gazette of India before being treated as final/operative law.');

-- ---- 16.2 Legal Metrology Act, 2009 -------------------------------------
INSERT INTO legal_acts (act_id, act_name, act_number, act_year, enactment_date, commencement_date, source_id, status)
VALUES ('22222222-2222-2222-2222-222222222201','Legal Metrology Act, 2009','Act No. 1 of 2010',2009,'2010-01-13','2011-04-01',
        '11111111-1111-1111-1111-111111111101','IN_FORCE');

INSERT INTO act_provisions (act_id, section_number, title, text_or_paraphrase, relevance, effective_from, source_id, status)
VALUES
('22222222-2222-2222-2222-222222222201','18','Declarations on pre-packaged commodities',
 'Empowers the Central Government to require specified declarations on pre-packaged commodities and prohibits sale/manufacture of non-declaring packages — VERIFICATION REQUIRED for exact operative text.',
 'Statutory basis for all Rule 6 declaration requirements.', '2011-04-01','11111111-1111-1111-1111-111111111101','IN_FORCE'),
('22222222-2222-2222-2222-222222222201','15','Powers of inspection, search and seizure',
 'Confers powers on the Director/Controller/Legal Metrology Officer to enter, inspect, search premises and seize non-conforming weights, measures and packages — VERIFICATION REQUIRED for exact operative text.',
 'Statutory basis for inspection and seizure actions in the enforcement workflow.', '2011-04-01','11111111-1111-1111-1111-111111111101','IN_FORCE'),
('22222222-2222-2222-2222-222222222201','36','Penalty for non-standard packages / declarations',
 'Prescribes penalties for a package not conforming to declaration requirements — VERIFICATION REQUIRED for exact quantum/graded penalty text (do not hard-code fine amounts without primary-source confirmation).',
 'Basis for prosecution pathway on Rule 6 violations.', '2011-04-01','11111111-1111-1111-1111-111111111101','IN_FORCE'),
('22222222-2222-2222-2222-222222222201','48','Compounding of offences',
 'Empowers the Director or authorised officer to compound certain offences on payment of a specified sum, subject to conditions — VERIFICATION REQUIRED for exact eligible-offence list.',
 'Basis for compounding_cases pathway.', '2011-04-01','11111111-1111-1111-1111-111111111101','IN_FORCE'),
('22222222-2222-2222-2222-222222222201','52','Rule-making power',
 'Empowers the Central Government to make rules for carrying out the purposes of the Act, including the Packaged Commodities Rules and their amendments.',
 'Vires for the Packaged Commodities Rules, 2011 and all its amendments.', '2011-04-01','11111111-1111-1111-1111-111111111101','IN_FORCE');

-- ---- 16.3 Rule catalogue (Rule 6 is the focus; others stubbed for extensibility)
INSERT INTO legal_rules (rule_id, rule_set_name, rule_number, short_title, parent_act_id) VALUES
('33333333-3333-3333-3333-333333330006','Legal Metrology (Packaged Commodities) Rules, 2011','6','Declarations to be made on every package','22222222-2222-2222-2222-222222222201'),
('33333333-3333-3333-3333-333333330005','Legal Metrology (Packaged Commodities) Rules, 2011','5','Declaration of quantity','22222222-2222-2222-2222-222222222201'),
('33333333-3333-3333-3333-333333330018','Legal Metrology (Packaged Commodities) Rules, 2011','18','Wholesale/retail sale price declaration','22222222-2222-2222-2222-222222222201'),
('33333333-3333-3333-3333-333333330009','Legal Metrology (Packaged Commodities) Rules, 2011','9','Manner of declaration on principal display panel','22222222-2222-2222-2222-222222222201');

-- ---- 16.4 Amendments (the temporal test-case) ---------------------------
-- Base Rule 6(10A) DID NOT EXIST prior to G.S.R. 128(E); represent as
-- a version whose predecessor is NULL (newly inserted provision).

-- First insert the amendment shells (rule_version FKs filled after versions exist,
-- so we create amendments first with NULLs then UPDATE — simplest for seed data
-- is to create rule_versions first referencing amendment_id as NULL, then amendment,
-- then backfill). Order chosen below avoids circular-FK problems.

INSERT INTO amendments (amendment_id, amendment_title, notification_number, publication_date, effective_date,
    rule_id, sub_rule_changed, change_summary, products_affected, conditions, transition_provision, source_id, verification_status)
VALUES
('44444444-4444-4444-4444-444444440001','Legal Metrology (Packaged Commodities) Amendment Rules, 2026','G.S.R. 128(E)',
  '2026-02-13','2026-07-01','33333333-3333-3333-3333-333333330006','10A',
  'Inserts new sub-rule (10A) after sub-rule (10) of Rule 6: every e-commerce entity selling imported products must provide product listings in a searchable and sortable filter specifying country of origin.',
  'Imported packaged commodities sold via e-commerce', 'Applies only to e-commerce entities selling imported products',
  'Approx. 4.5-month window from Gazette publication (13 Feb 2026) to commencement (1 Jul 2026) for platforms to implement the filter.',
  '11111111-1111-1111-1111-111111111103','VERIFIED_SECONDARY'),

('44444444-4444-4444-4444-444444440002','Legal Metrology (Packaged Commodities) Second Amendment Rules, 2026','G.S.R. 312(E)',
  '2026-04-27','2027-07-01','33333333-3333-3333-3333-333333330006','10A',
  'Substitutes sub-rule (10A) as inserted by G.S.R. 128(E) with an expanded country-of-origin filter obligation for e-commerce entities selling imported products.',
  'Imported packaged commodities sold via e-commerce', 'Applies only to e-commerce entities selling imported products',
  'Reported effective date is 1 July 2027 — one year after the first amendment''s commencement; VERIFY exact transition treatment for listings already compliant with the 128(E) version between 1 Jul 2026 and 30 Jun 2027.',
  '11111111-1111-1111-1111-111111111104','VERIFICATION_REQUIRED');

UPDATE amendments SET supersedes_amendment_id = '44444444-4444-4444-4444-444444440001'
WHERE amendment_id = '44444444-4444-4444-4444-444444440002';

-- ---- 16.5 Rule 6 versions (sample core declarations + the 10A timeline) --
INSERT INTO rule_versions (rule_version_id, rule_id, sub_rule, clause, requirement, legal_text_or_paraphrase,
    requirement_type, is_mandatory, product_scope, effective_from, effective_to, amendment_id, source_id, supersedes, status)
VALUES
-- 6(1)(a) manufacturer/packer/importer name & address — original 2011 text, still in force
('55555555-5555-5555-5555-555555550001','33333333-3333-3333-3333-333333330006','1','(a)',
 'Name and address of manufacturer/packer/importer',
 'Every package must bear the name and complete address of the manufacturer, or where the manufacturer is not the packer, the name and address of the manufacturer and packer, and in case of imported packages, the name and address of the importer.',
 'DECLARATION', TRUE, 'All pre-packaged commodities', '2011-06-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),

-- 6(1) net quantity
('55555555-5555-5555-5555-555555550002','33333333-3333-3333-3333-333333330006','1','(c)',
 'Net quantity in standard units',
 'Every package must declare the net quantity, in terms of standard weight/measure/number, of the commodity contained in the package.',
 'DECLARATION', TRUE, 'All pre-packaged commodities', '2011-06-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),

-- 6(1) MRP
('55555555-5555-5555-5555-555555550003','33333333-3333-3333-3333-333333330006','1','(e)',
 'Retail sale price (MRP), inclusive of all taxes',
 'Every package must declare the retail sale price of the commodity, which shall be inclusive of all taxes, in the manner prescribed.',
 'DECLARATION', TRUE, 'All pre-packaged commodities (subject to Fourth Schedule exclusions)', '2011-06-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),

-- 6(1) month/year of manufacture/pack/import
('55555555-5555-5555-5555-555555550004','33333333-3333-3333-3333-333333330006','1','(d)',
 'Month and year of manufacture/packing/import',
 'The month and year in which the commodity is manufactured, pre-packed or imported shall be mentioned on the package, subject to specified exceptions.',
 'DECLARATION', TRUE, 'All pre-packaged commodities (subject to listed exceptions)', '2011-06-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),

-- 6(2) consumer care details — ORIGINAL 2011 version, superseded 2016
('55555555-5555-5555-5555-555555550005','33333333-3333-3333-3333-333333330006','2', NULL,
 'Consumer-care contact details (original)',
 'Every package shall bear the name and address of the person/office to be contacted in case of consumer complaints (pre-2016 wording).',
 'DECLARATION', TRUE, 'All pre-packaged commodities', '2011-06-01', '2016-01-01', NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'SUPERSEDED'),

-- 6(2) consumer care details — AMENDED 2015 (effective 1.1.2016), substituted wording adds phone/email
('55555555-5555-5555-5555-555555550006','33333333-3333-3333-3333-333333330006','2', NULL,
 'Consumer-care contact details (name, address, telephone, email)',
 'Every package shall bear the name, address, telephone number and e-mail address of the person or office that can be contacted in case of consumer complaints.',
 'DECLARATION', TRUE, 'All pre-packaged commodities', '2016-01-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', '55555555-5555-5555-5555-555555550005', 'IN_FORCE'),

-- 6(10A) — VERSION 1, inserted by G.S.R. 128(E), effective 2026-07-01, superseded by v2 on 2027-07-01
('55555555-5555-5555-5555-555555550007','33333333-3333-3333-3333-333333330006','10A', NULL,
 'Searchable/sortable country-of-origin filter for imported e-commerce listings (v1)',
 'Every e-commerce entity selling imported products shall provide the product listings of such imported products in a searchable and sortable filter specifying the country of origin.',
 'PLATFORM_FUNCTIONALITY', TRUE, 'Imported packaged commodities listed/sold via e-commerce',
 '2026-07-01','2027-07-01','44444444-4444-4444-4444-444444440001',
 '11111111-1111-1111-1111-111111111103', NULL, 'SUPERSEDED'),

-- 6(10A) — VERSION 2, substituted by G.S.R. 312(E), effective 2027-07-01
('55555555-5555-5555-5555-555555550008','33333333-3333-3333-3333-333333330006','10A', NULL,
 'Searchable/sortable country-of-origin filter for imported e-commerce listings (v2, expanded — VERIFICATION REQUIRED)',
 'Reported text: e-commerce entities selling imported products shall ensure that the product listing of such imported products contains a searchable and sortable filter specifying the country of origin, with effect from 1 July 2027 — exact expanded wording not yet confirmed against the primary Gazette notification.',
 'PLATFORM_FUNCTIONALITY', TRUE, 'Imported packaged commodities listed/sold via e-commerce',
 '2027-07-01', NULL, '44444444-4444-4444-4444-444444440002',
 '11111111-1111-1111-1111-111111111104', '55555555-5555-5555-5555-555555550007', 'NOT_YET_IN_FORCE');

UPDATE rule_versions SET superseded_by = '55555555-5555-5555-5555-555555550006'
WHERE rule_version_id = '55555555-5555-5555-5555-555555550005';
UPDATE rule_versions SET superseded_by = '55555555-5555-5555-5555-555555550008'
WHERE rule_version_id = '55555555-5555-5555-5555-555555550007';

UPDATE amendments SET previous_rule_version_id = NULL, new_rule_version_id = '55555555-5555-5555-5555-555555550007'
WHERE amendment_id = '44444444-4444-4444-4444-444444440001';
UPDATE amendments SET previous_rule_version_id = '55555555-5555-5555-5555-555555550007', new_rule_version_id = '55555555-5555-5555-5555-555555550008'
WHERE amendment_id = '44444444-4444-4444-4444-444444440002';

-- ---- 16.6 Transition provision for the 10A timeline ---------------------
INSERT INTO transition_provisions (amendment_id, affected_rule_version_id, affected_product, condition,
    start_date, end_date, old_stock_applicability, explanation, source_id)
VALUES
('44444444-4444-4444-4444-444444440001', '55555555-5555-5555-5555-555555550007',
 'Imported products listed on e-commerce platforms',
 'Platforms had until 1 July 2026 to implement the searchable/sortable country-of-origin filter after the 13 Feb 2026 Gazette notification.',
 '2026-02-13','2026-07-01', 'Not applicable — this is a platform-functionality requirement, not a per-unit labelling requirement.',
 'Approx. 4.5-month compliance runway granted between publication and commencement.', '11111111-1111-1111-1111-111111111103');

-- ---- 16.7 Rule applicability samples -------------------------------------
INSERT INTO rule_applicability (rule_version_id, product_category, ecommerce, imported, condition_expression,
    applicable_result, reason, source_id)
VALUES
-- 6(10A) v1 applies ONLY to ecommerce=true AND imported=true
('55555555-5555-5555-5555-555555550007', NULL, TRUE, TRUE,
 '{"all":[{"field":"ecommerce","eq":true},{"field":"imported","eq":true}]}',
 'REQUIRED','E-commerce sale of an imported product triggers the country-of-origin searchable filter requirement (Rule 6(10A) v1, in force 1 Jul 2026 – 30 Jun 2027).',
 '11111111-1111-1111-1111-111111111103'),
('55555555-5555-5555-5555-555555550007', NULL, FALSE, NULL,
 '{"field":"imported","eq":false}',
 'NOT_APPLICABLE','Domestic (non-imported) products are outside the scope of Rule 6(10A).',
 '11111111-1111-1111-1111-111111111103'),
-- Month/year of mfg — NOT_APPLICABLE example condition (illustrative; exact Fourth-Schedule exclusion list VERIFICATION REQUIRED)
('55555555-5555-5555-5555-555555550004', 'COSMETIC', NULL, NULL,
 '{"field":"category","eq":"COSMETIC"}',
 'CONDITIONAL','Applicability of the month/year-of-manufacture declaration to specific cosmetic sub-categories depends on Fourth Schedule exclusions — VERIFICATION REQUIRED against the consolidated Rules text before treating as REQUIRED or NOT_REQUIRED outright.',
 '11111111-1111-1111-1111-111111111102');

-- ---- 16.8 Enforcement provisions (no invented amounts) -------------------
INSERT INTO enforcement_provisions (act_id, section, offence_description, applicability, authority, action_type,
    compounding_available, prosecution_possible, seizure_possible, notice_possible, source_id)
VALUES
('22222222-2222-2222-2222-222222222201','36','Sale/manufacture of a package not bearing declarations required under Rule 6',
 'Any pre-packaged commodity found without a mandated Rule 6 declaration', 'Legal Metrology Officer / Director',
 'PROSECUTION', TRUE, TRUE, TRUE, TRUE, '11111111-1111-1111-1111-111111111101'),
('22222222-2222-2222-2222-222222222201','48','Compounding of an offence under Section 36 on application, subject to conditions',
 'Offences declared compoundable by the Act/Rules', 'Director / authorised officer',
 'COMPOUNDING', TRUE, FALSE, FALSE, FALSE, '11111111-1111-1111-1111-111111111101');

-- ---- 16.9 MERGED RESEARCH: additional Rule 6 / Rule 26 amendments -------
-- Sourced from a separately produced legal-research dataset and
-- spot-verified against independent citations (a law-firm QR-code
-- writeup for G.S.R. 577(E), and the Chambers/Mondaq footnote for the
-- G.S.R. 128(E) DCA PDF URL) before being merged in here. Exact
-- provision wording not directly read from the Gazette PDF is marked
-- VERIFICATION_REQUIRED — dates and existence are corroborated across
-- multiple independent secondary sources (Lexology, SCC Online, PIB,
-- UL Solutions, Saikrishna & Associates, TeamLease RegTech).

INSERT INTO legal_sources (source_id, source_type, title, issuing_authority, notification_number,
    publication_date, effective_date, url, authenticity_status, verified_at, notes)
VALUES
('11111111-1111-1111-1111-111111111105','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2021','Dept. of Consumer Affairs, MoCAF&PD',
  'G.S.R. 779(E)','2021-11-02','2022-04-01','https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/whatsnews/The_Legal_Metrology_Packaged_Commodities_Amendment_Rule%2C_2021_whatsnews.pdf',
  'VERIFIED_SECONDARY', now(), 'Inserted an original unit-sale-price provision (later superseded by G.S.R. 226(E)). Exact original wording VERIFICATION REQUIRED.'),

('11111111-1111-1111-1111-111111111106','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2022','Dept. of Consumer Affairs, MoCAF&PD',
  'G.S.R. 226(E)','2022-03-28','2022-10-01','https://consumeraffairs.gov.in/public/upload/files/GSR226_1732871458.pdf',
  'VERIFIED_SECONDARY', now(), 'Changed commencement of and substituted the unit-sale-price provision (Rule 6(11)) inserted by G.S.R. 779(E); corroborated by Lexology commentary dated 1 Apr 2022 / 1 Oct 2022 commencement dates.'),

('11111111-1111-1111-1111-111111111107','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) (Second Amendment) Rules, 2022','Dept. of Consumer Affairs, MoCAF&PD',
  'G.S.R. 577(E)','2022-07-14','2022-07-14','https://consumeraffairs.gov.in/public/upload/files/Notification%20-%20%20Legal%20Metrology%20%28QR%20Code%29_1732871487.pdf',
  'VERIFIED_PRIMARY', now(), 'Confirmed via TaxGuru/SCC Online/PIB/Saikrishna & Associates/TeamLease RegTech reproductions of the notification text: inserts a one-year proviso (from 15 Jul 2022) letting electronic-product packages declare manufacturer/packer/importer name, common/generic name, and dimensions via QR code instead of on-package, if not otherwise declared.'),

('11111111-1111-1111-1111-111111111108','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2023','Dept. of Consumer Affairs, MoCAF&PD',
  'G.S.R. 456(E)','2023-06-23','2023-06-23','https://consumeraffairs.gov.in/public/upload/files/2023.6.23%20QR%20Code%20PCR%20amendment_1732871827.pdf',
  'VERIFIED_SECONDARY', now(), 'Follow-on/continuation of the electronic-product QR-code proviso as the original one-year window from G.S.R. 577(E) approached expiry. Exact substituted wording VERIFICATION REQUIRED against primary Gazette text.'),

('11111111-1111-1111-1111-111111111109','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Second Amendment Rules, 2025','Dept. of Consumer Affairs, MoCAF&PD',
  'G.S.R. 881(E)','2025-12-02','2026-02-01','https://consumeraffairs.gov.in/public/upload/files/2nd%20PCR%20Pan%20Masala_1764736734.pdf',
  'VERIFICATION_REQUIRED', now(), 'Reported to add a pan-masala-specific exemption/declaration clause under Rule 26. Sourced from a single secondary dataset with no independent corroboration found during this research pass — verify against the official Gazette before treating as operative law.');

-- Rule 26 (exemptions) catalogue entry — needed to host the pan-masala clause
INSERT INTO legal_rules (rule_id, rule_set_name, rule_number, short_title, parent_act_id) VALUES
('33333333-3333-3333-3333-333333330026','Legal Metrology (Packaged Commodities) Rules, 2011','26','Commodities/conditions exempted from these rules','22222222-2222-2222-2222-222222222201');

-- Amendments
INSERT INTO amendments (amendment_id, amendment_title, notification_number, publication_date, effective_date,
    rule_id, sub_rule_changed, change_summary, products_affected, conditions, transition_provision, source_id, verification_status)
VALUES
('44444444-4444-4444-4444-444444440003','Legal Metrology (Packaged Commodities) Amendment Rules, 2021','G.S.R. 779(E)','2021-11-02','2022-04-01',
  '33333333-3333-3333-3333-333333330006','11','Inserted an original unit-sale-price declaration requirement (Rule 6(11)).',
  'Commodities sold by length/volume/weight/number where a unit sale price is relevant', NULL, NULL,
  '11111111-1111-1111-1111-111111111105','VERIFICATION_REQUIRED'),

('44444444-4444-4444-4444-444444440004','Legal Metrology (Packaged Commodities) Amendment Rules, 2022','G.S.R. 226(E)','2022-03-28','2022-10-01',
  '33333333-3333-3333-3333-333333330006','11','Changed the commencement of and substituted the unit-sale-price provision (Rule 6(11)) inserted by G.S.R. 779(E).',
  'Commodities sold by length/volume/weight/number where a unit sale price is relevant', NULL,
  'The 2021 provision (effective 1 Apr 2022) was substituted before/at the point the 2022 provision commenced (1 Oct 2022); no gap in obligation intended, but exact overlap treatment is VERIFICATION REQUIRED.',
  '11111111-1111-1111-1111-111111111106','VERIFIED_SECONDARY'),

('44444444-4444-4444-4444-444444440005','Legal Metrology (Packaged Commodities) (Second Amendment) Rules, 2022','G.S.R. 577(E)','2022-07-14','2022-07-14',
  '33333333-3333-3333-3333-333333330006','1','Inserted a proviso allowing electronic-product packages to declare manufacturer/packer/importer name, common/generic name, and dimensions via QR code (instead of on-package) for a period of one year from 15 Jul 2022.',
  'Electronic products manufactured/packed/imported after 15 Jul 2022', 'Electronic products only; declaration must otherwise not already be on the package',
  'One-year trial window: 15 Jul 2022 to approx. 15 Jul 2023.',
  '11111111-1111-1111-1111-111111111107','VERIFIED_PRIMARY'),

('44444444-4444-4444-4444-444444440006','Legal Metrology (Packaged Commodities) Amendment Rules, 2023','G.S.R. 456(E)','2023-06-23','2023-06-23',
  '33333333-3333-3333-3333-333333330006','1','Continued/extended the electronic-product QR-code declaration proviso as the one-year window under G.S.R. 577(E) approached expiry.',
  'Electronic products', 'Electronic products only', NULL,
  '11111111-1111-1111-1111-111111111108','VERIFICATION_REQUIRED'),

('44444444-4444-4444-4444-444444440007','Legal Metrology (Packaged Commodities) Second Amendment Rules, 2025','G.S.R. 881(E)','2025-12-02','2026-02-01',
  '33333333-3333-3333-3333-333333330026', NULL,'Reported pan-masala-specific exemption/declaration clause under Rule 26 — VERIFICATION REQUIRED, single-source.',
  'Pan masala', NULL, NULL,
  '11111111-1111-1111-1111-111111111109','VERIFICATION_REQUIRED');

UPDATE amendments SET supersedes_amendment_id = '44444444-4444-4444-4444-444444440003' WHERE amendment_id = '44444444-4444-4444-4444-444444440004';
UPDATE amendments SET supersedes_amendment_id = '44444444-4444-4444-4444-444444440005' WHERE amendment_id = '44444444-4444-4444-4444-444444440006';

-- Rule versions
INSERT INTO rule_versions (rule_version_id, rule_id, sub_rule, clause, requirement, legal_text_or_paraphrase,
    requirement_type, is_mandatory, product_scope, effective_from, effective_to, amendment_id, source_id, supersedes, status)
VALUES
-- 6(1)(b) common/generic name — canonical clause, newly added
('55555555-5555-5555-5555-555555550009','33333333-3333-3333-3333-333333330006','1','(b)',
 'Common or generic name of the commodity',
 'Every package must declare the common or generic name of the commodity contained in it; where the package contains more than one product, the name and number/quantity of each.',
 'DECLARATION', TRUE, 'All pre-packaged commodities', '2011-06-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),

-- 6(1)(f) dimensions where relevant — newly added
('55555555-5555-5555-5555-555555550010','33333333-3333-3333-3333-333333330006','1','(f)',
 'Dimensions of the commodity, where relevant',
 'Where the size/dimensions of the commodity are relevant to the consumer, every package must declare them; for multiple pieces, dimensions of each piece.',
 'DECLARATION', FALSE, 'Commodities where dimension is a relevant characteristic', '2011-06-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),

-- 6(1)(g) other matters specified in the rules — newly added
('55555555-5555-5555-5555-555555550011','33333333-3333-3333-3333-333333330006','1','(g)',
 'Other declarations specified elsewhere in the Rules',
 'Any other matter which the Rules require to be declared on the package, not separately enumerated in clauses (a)-(f).',
 'DECLARATION', FALSE, 'As specified elsewhere in the Rules', '2011-06-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),

-- 6(11) unit sale price — v1 (2021 insertion)
('55555555-5555-5555-5555-555555550012','33333333-3333-3333-3333-333333330006','11', NULL,
 'Unit sale price declaration (v1, original)',
 'Original 2021 unit-sale-price declaration requirement inserted by G.S.R. 779(E) — VERIFICATION REQUIRED for exact wording.',
 'DECLARATION', TRUE, 'Commodities sold by length/volume/weight/number where unit price is relevant',
 '2022-04-01','2022-10-01','44444444-4444-4444-4444-444444440003',
 '11111111-1111-1111-1111-111111111105', NULL, 'SUPERSEDED'),

-- 6(11) unit sale price — v2 (2022 substitution, still in force)
('55555555-5555-5555-5555-555555550013','33333333-3333-3333-3333-333333330006','11', NULL,
 'Unit sale price declaration (v2, current)',
 'Every package shall declare the unit sale price of the commodity according to quantity, length, volume or number, as substituted by G.S.R. 226(E) — VERIFICATION REQUIRED for exact wording.',
 'DECLARATION', TRUE, 'Commodities sold by length/volume/weight/number where unit price is relevant',
 '2022-10-01', NULL, '44444444-4444-4444-4444-444444440004',
 '11111111-1111-1111-1111-111111111106', '55555555-5555-5555-5555-555555550012', 'IN_FORCE'),

-- Electronic-product QR-code proviso — v1 (2022, one-year trial)
('55555555-5555-5555-5555-555555550014','33333333-3333-3333-3333-333333330006','1','QR-proviso(electronic)-v1',
 'Electronic products may declare manufacturer/packer/importer name, common/generic name and dimensions via QR code (trial, v1)',
 'For electronic products manufactured/packed/imported after 15 Jul 2022, the mandatory declarations under clauses (a), (b) and (f) may, for one year, be conveyed via a scannable QR code instead of printed on the package, if not otherwise declared on it.',
 'PLATFORM_FUNCTIONALITY', FALSE, 'Electronic products', '2022-07-14','2023-06-23','44444444-4444-4444-4444-444444440005',
 '11111111-1111-1111-1111-111111111107', NULL, 'SUPERSEDED'),

-- Electronic-product QR-code proviso — v2 (2023 continuation)
('55555555-5555-5555-5555-555555550015','33333333-3333-3333-3333-333333330006','1','QR-proviso(electronic)-v2',
 'Electronic products may declare manufacturer/packer/importer name, common/generic name and dimensions via QR code (continued, v2 — VERIFICATION REQUIRED)',
 'Reported continuation of the electronic-product QR-code declaration proviso from G.S.R. 456(E) — exact substituted text not yet confirmed against the primary Gazette notification.',
 'PLATFORM_FUNCTIONALITY', FALSE, 'Electronic products', '2023-06-23', NULL, '44444444-4444-4444-4444-444444440006',
 '11111111-1111-1111-1111-111111111108', '55555555-5555-5555-5555-555555550014', 'IN_FORCE'),

-- Rule 26 pan masala exemption/declaration clause (2025/2026)
('55555555-5555-5555-5555-555555550016','33333333-3333-3333-3333-333333330026', NULL, NULL,
 'Pan-masala-specific exemption/declaration clause (VERIFICATION REQUIRED)',
 'Reported pan-masala-specific addition to the Rule 26 exemption/declaration framework — exact clause text and scope not yet confirmed against the primary Gazette notification (G.S.R. 881(E)).',
 'DECLARATION', TRUE, 'Pan masala', '2026-02-01', NULL, '44444444-4444-4444-4444-444444440007',
 '11111111-1111-1111-1111-111111111109', NULL, 'IN_FORCE');

UPDATE rule_versions SET superseded_by = '55555555-5555-5555-5555-555555550013' WHERE rule_version_id = '55555555-5555-5555-5555-555555550012';
UPDATE rule_versions SET superseded_by = '55555555-5555-5555-5555-555555550015' WHERE rule_version_id = '55555555-5555-5555-5555-555555550014';

UPDATE amendments SET new_rule_version_id = '55555555-5555-5555-5555-555555550012' WHERE amendment_id = '44444444-4444-4444-4444-444444440003';
UPDATE amendments SET previous_rule_version_id = '55555555-5555-5555-5555-555555550012', new_rule_version_id = '55555555-5555-5555-5555-555555550013' WHERE amendment_id = '44444444-4444-4444-4444-444444440004';
UPDATE amendments SET new_rule_version_id = '55555555-5555-5555-5555-555555550014' WHERE amendment_id = '44444444-4444-4444-4444-444444440005';
UPDATE amendments SET previous_rule_version_id = '55555555-5555-5555-5555-555555550014', new_rule_version_id = '55555555-5555-5555-5555-555555550015' WHERE amendment_id = '44444444-4444-4444-4444-444444440006';
UPDATE amendments SET new_rule_version_id = '55555555-5555-5555-5555-555555550016' WHERE amendment_id = '44444444-4444-4444-4444-444444440007';

-- Applicability for the newly merged provisions
INSERT INTO rule_applicability (rule_version_id, product_category, electronic_product, condition_expression,
    applicable_result, reason, source_id)
VALUES
('55555555-5555-5555-5555-555555550014', 'ELECTRONIC', TRUE, '{"field":"category","eq":"ELECTRONIC"}',
 'CONDITIONAL','Electronic-product QR declaration proviso applies only to electronic products, and only within its one-year trial window (15 Jul 2022 – 23 Jun 2023).', '11111111-1111-1111-1111-111111111107'),
('55555555-5555-5555-5555-555555550014', NULL, FALSE, '{"field":"category","neq":"ELECTRONIC"}',
 'NOT_APPLICABLE','Non-electronic products must make clauses (a)/(b)/(f) declarations on-package as normal; the QR proviso does not apply to them.', '11111111-1111-1111-1111-111111111107'),
('55555555-5555-5555-5555-555555550013', NULL, NULL, '{"field":"quantity_type","in":["weight","volume","length","number"]}',
 'REQUIRED','Unit sale price applies wherever the commodity is sold by length, volume, weight or number and a per-unit comparison is meaningful.', '11111111-1111-1111-1111-111111111106'),
('55555555-5555-5555-5555-555555550016', 'FOOD', NULL, '{"field":"subcategory","eq":"PAN_MASALA"}',
 'NEEDS_REVIEW','Applicability and exact scope of the pan-masala Rule 26 clause is VERIFICATION REQUIRED — route to inspector rather than auto-deciding.', '11111111-1111-1111-1111-111111111109');


-- LEGALAKSHI v2 LEGAL SEED PATCH
-- Apply AFTER legalakshi_schema (1).sql
-- Purpose: correct legal mappings, add missing Rule 6 provisions, applicability,
-- current exceptions, and source provenance needed by the compliance engine.

BEGIN;

-- ---------------------------------------------------------------------
-- A. Product facts required for applicability decisions
-- ---------------------------------------------------------------------
ALTER TABLE inspected_products
    ADD COLUMN IF NOT EXISTS is_prepackaged BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS intended_consumer_type TEXT,
    ADD COLUMN IF NOT EXISTS combination_package BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS group_package BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS multi_piece_package BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS net_quantity_value NUMERIC,
    ADD COLUMN IF NOT EXISTS net_quantity_unit TEXT;

ALTER TABLE inspections
    ADD COLUMN IF NOT EXISTS jurisdiction TEXT DEFAULT 'India (Union)';

-- Product categories needed for current/known special rules.
INSERT INTO lkp_product_category (code, description) VALUES
 ('AGRICULTURAL','Agricultural farm produce'),
 ('MEDICAL_DEVICE','Medical device'),
 ('PAN_MASALA','Pan masala'),
 ('TEXTILE','Textile / ready-made garment')
ON CONFLICT (code) DO NOTHING;

-- ---------------------------------------------------------------------
-- B. Upgrade source provenance to authoritative URLs where confirmed
-- ---------------------------------------------------------------------
UPDATE legal_sources SET
    publication_date = '2010-01-13',
    effective_date = '2011-04-01',
    url = 'https://www.indiacode.nic.in/bitstream/123456789/4892/1/legalmetrology_act_2009.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'India Code copy of the Legal Metrology Act, 2009. Use the Act text as the primary statutory source; do not infer penalty quantum from secondary summaries.'
WHERE source_id = '11111111-1111-1111-1111-111111111101';

UPDATE legal_sources SET
    publication_date = '2011-03-07',
    effective_date = '2011-04-01',
    url = 'https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/whatsnews/Book_on_Legal_Metrology_Packaged_Commodities_Rules%2C2011_with_all_amendments_whatsnews.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Department of Consumer Affairs consolidated publication of the Packaged Commodities Rules, 2011 with amendments. Use the Gazette notifications for amendment-specific effective dates and substituted text.'
WHERE source_id = '11111111-1111-1111-1111-111111111102';

UPDATE legal_sources SET
    url = 'https://consumeraffairs.gov.in/public/upload/files/2026.02.13%20PCR%201st%20COO%20Filter%20on%20e-commerce%20websites_1771231030.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Official DCA Gazette PDF. G.S.R. 128(E), 13 Feb 2026; Rule 6(10A), effective 1 Jul 2026.'
WHERE source_id = '11111111-1111-1111-1111-111111111103';

UPDATE legal_sources SET
    url = 'https://consumeraffairs.gov.in/public/upload/files/2026.4.27%20PCR%202nd%20COO%20from%201.7.2027_1777348487.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Official DCA Gazette PDF. G.S.R. 312(E), 27 Apr 2026; substitutes Rule 6(10A) with effect from 1 Jul 2027.'
WHERE source_id = '11111111-1111-1111-1111-111111111104';

UPDATE legal_sources SET
    url = 'https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/whatsnews/The_Legal_Metrology_Packaged_Commodities_Amendment_Rule%2C_2021_whatsnews.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Official DCA Gazette PDF. G.S.R. 779(E), 2 Nov 2021. Commencement later shifted by G.S.R. 226(E).'
WHERE source_id = '11111111-1111-1111-1111-111111111105';

UPDATE legal_sources SET
    url = 'https://consumeraffairs.gov.in/public/upload/files/GSR226_1732871458.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Official DCA Gazette PDF. G.S.R. 226(E), 28 Mar 2022; commencement of the 2021 amendment shifted to 1 Oct 2022 and Rule 6(11) substituted.'
WHERE source_id = '11111111-1111-1111-1111-111111111106';

UPDATE legal_sources SET
    url = 'https://consumeraffairs.gov.in/public/upload/files/Notification%20-%20%20Legal%20Metrology%20%28QR%20Code%29_1732871487.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Official DCA Gazette PDF. G.S.R. 577(E), 14 Jul 2022; electronic-product QR-code proviso.'
WHERE source_id = '11111111-1111-1111-1111-111111111107';

UPDATE legal_sources SET
    url = 'https://consumeraffairs.gov.in/public/upload/files/2023.6.23%20QR%20Code%20PCR%20amendment_1732871827.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Official DCA Gazette PDF. G.S.R. 456(E), 23 Jun 2023; substitutes the electronic-product QR-code provisos in Rule 6.'
WHERE source_id = '11111111-1111-1111-1111-111111111108';

UPDATE legal_sources SET
    url = 'https://consumeraffairs.gov.in/public/upload/files/2nd%20PCR%20Pan%20Masala_1764736734.pdf',
    authenticity_status = 'VERIFIED_PRIMARY',
    notes = 'Official DCA Gazette PDF. G.S.R. 881(E), 2 Dec 2025; effective 1 Feb 2026. This amendment concerns Rule 26(a) and pan masala; it is NOT a Rule 6 declaration requirement.'
WHERE source_id = '11111111-1111-1111-1111-111111111109';

-- Additional authoritative/near-authoritative sources for historical Rule 6 amendments.
INSERT INTO legal_sources (source_id, source_type, title, issuing_authority, notification_number,
    publication_date, effective_date, url, authenticity_status, verified_at, notes)
VALUES
('11111111-1111-1111-1111-111111111110','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2012','Department of Consumer Affairs','G.S.R. 427(E)','2012-06-05','2012-06-05',
 'https://upload.indiacode.nic.in/showfile?actid=AC_CEN_21_44_00007_201001_1517807327712&filename=6-lm_pckgd_comm_amndt_rls_2012.pdf&type=notification','VERIFIED_PRIMARY',now(),
 'Official India Code-hosted Gazette notification. Inserts Rule 6(7) for genetically modified food, effective 1 Jan 2013, and makes other amendments.'),
('11111111-1111-1111-1111-111111111111','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2014','Department of Consumer Affairs','G.S.R. 137','2014-06-16','2014-07-01',
 'https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/whatsnews/Book_on_Legal_Metrology_Packaged_Commodities_Rules%2C2011_with_all_amendments_whatsnews.pdf','VERIFIED_PRIMARY',now(),
 'Official DCA consolidated Rules identify G.S.R. 137 dated 16 Jun 2014 as the insertion of Rule 6(8), effective 1 Jul 2014.'),
('11111111-1111-1111-1111-111111111112','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2015','Department of Consumer Affairs','G.S.R. 385(E)','2015-05-14','2015-05-14',
 'https://consumeraffairs.gov.in/public/upload/files/8%28x%29_0_1732870750.pdf','VERIFIED_PRIMARY',now(),
 'Official DCA Gazette PDF. Rule 6(2) substituted with name, address, telephone and email; Rule 6(9) permits labels on imported packages.'),
('11111111-1111-1111-1111-111111111113','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2016','Department of Consumer Affairs','G.S.R. 858(E)','2016-09-07','2016-09-07',
 'https://consumeraffairs.gov.in/public/upload/files/8%28xi%29_0_1732871315.pdf','VERIFIED_PRIMARY',now(),
 'Official DCA Gazette PDF. Adds the Essential Commodities Act notified-price proviso to Rule 6(1)(e).'),
('11111111-1111-1111-1111-111111111114','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2017','Department of Consumer Affairs','G.S.R. 629(E)','2017-06-23','2018-01-01',
 'https://consumeraffairs.gov.in/public/upload/files/8%28xii%29_0_1732871346.pdf','VERIFIED_PRIMARY',now(),
 'Official DCA Gazette PDF. Major 2017 amendment effective 1 Jan 2018, including Rule 6 country-of-origin and e-commerce display provisions.'),
('11111111-1111-1111-1111-111111111115','GAZETTE_NOTIFICATION','Legal Metrology (Packaged Commodities) Amendment Rules, 2025','Department of Consumer Affairs','G.S.R. 778(E)','2025-10-23','2025-10-23',
 'https://consumeraffairs.gov.in/public/upload/files/267107_1761404371.pdf','VERIFIED_PRIMARY',now(),
 'Official DCA Gazette PDF. Medical-device packages receive cross-reference treatment under Rule 2(h), Rule 7 and Rule 33.'),
('11111111-1111-1111-1111-111111111116','FAQ','Frequently Asked Questions on Legal Metrology','Department of Consumer Affairs',NULL,NULL,NULL,
 'https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/whatsnews/Frequently_Asked_Questions_on_Legal_Metrology_whatsnews.pdf','VERIFIED_PRIMARY',now(),
 'Official DCA FAQ. Guidance only; not a substitute for the Act, Rules or Gazette. Includes clarification that unit sale price is not required for combination/group/multi-piece packages and that Rule 7 governs minimum character size.'),
('11111111-1111-1111-1111-111111111117','FAQ','FAQs for smooth implementation of G.S.R. 629(E) dated 23.06.2017','Department of Consumer Affairs',NULL,NULL,NULL,
 'https://consumeraffairs.gov.in/public/upload/admin/cmsfiles/whatsnews/FAQs_for_smooth_implementation_of_GSR_629E_dated_23.6.2017_whatsnews.pdf','VERIFIED_PRIMARY',now(),
 'Official DCA FAQ explaining e-commerce declaration display under Rule 6(10). Guidance only; do not treat FAQ text as an independent offence provision.'),
('11111111-1111-1111-1111-111111111118','ADVISORY','Advisory on packages of agriculture farm produce up to 50 kg under the Legal Metrology (Packaged Commodities) Rules, 2011','Department of Consumer Affairs',NULL,'2023-03-06','2023-03-06',
 'https://consumeraffairs.gov.in/pages/legal-metrology-act','VERIFIED_PRIMARY',now(),
 'DCA advisory listed on the official Legal Metrology page; relevant to Rule 3 applicability for agricultural farm produce.'),
('11111111-1111-1111-1111-111111111119','ADVISORY','Provisions of the Legal Metrology (Packaged Commodities) Rules, 2011 on Medical Devices','Department of Consumer Affairs',NULL,'2023-07-10','2023-07-10',
 'https://consumeraffairs.gov.in/pages/legal-metrology-act','VERIFIED_PRIMARY',now(),
 'DCA advisory listed on the official Legal Metrology page; medical devices need special applicability handling.'),
('11111111-1111-1111-1111-111111111120','ADVISORY','SoP for Determination of the Net Quantity of Commodities (Edible Oils & Fats)','Department of Consumer Affairs',NULL,'2023-12-29','2023-12-29',
 'https://consumeraffairs.gov.in/pages/legal-metrology-act','VERIFIED_PRIMARY',now(),
 'DCA SOP listed on the official Legal Metrology page. Relevant to physical quantity verification, not merely label OCR.' )
ON CONFLICT (source_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- C. Amendment chronology needed by the temporal engine
-- ---------------------------------------------------------------------
INSERT INTO amendments (amendment_id, amendment_title, notification_number, publication_date, effective_date,
    rule_id, sub_rule_changed, change_summary, products_affected, conditions, transition_provision, source_id, verification_status)
VALUES
('44444444-4444-4444-4444-444444440008','Legal Metrology (Packaged Commodities) Amendment Rules, 2012','G.S.R. 427(E)',
 '2012-06-05','2012-06-05','33333333-3333-3333-3333-333333330006','7',
 'Inserted Rule 6(7) requiring genetically modified food packages to bear the words GM at the top of the principal display panel, with effect from 1 Jan 2013.',
 'Genetically modified food', 'Food product is genetically modified', NULL,'11111111-1111-1111-1111-111111111110','VERIFIED_PRIMARY'),
('44444444-4444-4444-4444-444444440009','Legal Metrology (Packaged Commodities) Amendment Rules, 2014','G.S.R. 137',
 '2014-06-16','2014-07-01','33333333-3333-3333-3333-333333330006','8',
 'Inserted Rule 6(8) requiring the specified vegetarian/non-vegetarian dot declaration for soap, shampoos, tooth pastes and other cosmetics and toiletries.',
 'Specified cosmetics/toiletries', 'Product falls within Rule 6(8) scope', NULL,'11111111-1111-1111-1111-111111111111','VERIFIED_PRIMARY'),
('44444444-4444-4444-4444-444444440010','Legal Metrology (Packaged Commodities) Amendment Rules, 2015','G.S.R. 385(E)',
 '2015-05-14','2015-05-14','33333333-3333-3333-3333-333333330006','2/9',
 'Substituted Rule 6(2) with name, address, telephone and email for consumer complaints and inserted Rule 6(9) permitting labels on imported packages for required declarations.',
 'All pre-packaged commodities; imported packages', 'Rule 6 applies', NULL,'11111111-1111-1111-1111-111111111112','VERIFIED_PRIMARY'),
('44444444-4444-4444-4444-444444440011','Legal Metrology (Packaged Commodities) Amendment Rules, 2016','G.S.R. 858(E)',
 '2016-09-07','2016-09-07','33333333-3333-3333-3333-333333330006','1(e)',
 'Added proviso that if the retail sale price of an essential commodity is fixed and notified under the Essential Commodities Act, that notified price applies.',
 'Essential commodities with notified price', 'Competent authority has fixed and notified the retail sale price', NULL,'11111111-1111-1111-1111-111111111113','VERIFIED_PRIMARY'),
('44444444-4444-4444-4444-444444440012','Legal Metrology (Packaged Commodities) Amendment Rules, 2017','G.S.R. 629(E)',
 '2017-06-23','2018-01-01','33333333-3333-3333-3333-333333330006','1/10',
 'Major amendment effective 1 Jan 2018 including country-of-origin declaration and e-commerce display requirements under Rule 6, along with revised chapter applicability.',
 'Imported products; e-commerce; general packaged commodities', 'Product is within the amended Chapter scope', NULL,'11111111-1111-1111-1111-111111111114','VERIFIED_PRIMARY'),
('44444444-4444-4444-4444-444444440013','Legal Metrology (Packaged Commodities) Amendment Rules, 2025','G.S.R. 778(E)',
 '2025-10-23','2025-10-23',NULL,'2(h)/7/33',
 'Introduced medical-device cross-references so declaration placement/character dimensions and relaxation provisions defer to the Medical Devices Rules, 2017 where applicable.',
 'Medical devices', 'Package contains a medical device', NULL,'11111111-1111-1111-1111-111111111115','VERIFIED_PRIMARY')
ON CONFLICT (amendment_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- D. Add current Rule 3 and Rule 6 provisions
-- ---------------------------------------------------------------------
INSERT INTO legal_rules (rule_id, rule_set_name, rule_number, short_title, parent_act_id) VALUES
('33333333-3333-3333-3333-333333330003','Legal Metrology (Packaged Commodities) Rules, 2011','3','Application of Chapter','22222222-2222-2222-2222-222222222201'),
('33333333-3333-3333-3333-333333330007','Legal Metrology (Packaged Commodities) Rules, 2011','7','Principal display panel / manner and minimum size of declarations','22222222-2222-2222-2222-222222222201')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO rule_versions (rule_version_id, rule_id, sub_rule, clause, requirement, legal_text_or_paraphrase,
    requirement_type, is_mandatory, product_scope, effective_from, effective_to, amendment_id, source_id, supersedes, status)
VALUES
('55555555-5555-5555-5555-555555550017','33333333-3333-3333-3333-333333330006','1','(aa)',
 'Country of origin/manufacture/assembly for imported products',
 'In case of imported products, the name of the country of origin or manufacture or assembly shall be mentioned on the package.',
 'DECLARATION', TRUE, 'Imported pre-packaged commodities', '2018-01-01', NULL, '44444444-4444-4444-4444-444444440012',
 '11111111-1111-1111-1111-111111111114', NULL, 'IN_FORCE'),
('55555555-5555-5555-5555-555555550018','33333333-3333-3333-3333-333333330006','1','(da)',
 'Best before / use by date where commodity may become unfit for human consumption',
 'Where a package contains a commodity which may become unfit for human consumption after a period of time, the best before or use by date, month and year shall also be mentioned, subject to the rule''s cross-law provisos.',
 'DECLARATION', FALSE, 'Commodities that may become unfit for human consumption over time', '2011-04-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE'),
('55555555-5555-5555-5555-555555550019','33333333-3333-3333-3333-333333330006','7',NULL,
 'GM declaration for genetically modified food',
 'Every package containing genetically modified food shall bear at the top of its principal display panel the words “GM”.',
 'DECLARATION', TRUE, 'Genetically modified food', '2013-01-01', NULL, '44444444-4444-4444-4444-444444440008',
 '11111111-1111-1111-1111-111111111110', NULL, 'IN_FORCE'),
('55555555-5555-5555-5555-555555550020','33333333-3333-3333-3333-333333330006','8',NULL,
 'Vegetarian/non-vegetarian dot for specified cosmetics and toiletries',
 'Packages containing soap, shampoos, tooth pastes and other cosmetics and toiletries shall bear at the top of the principal display panel a red or brown dot for products of non-vegetarian origin and a green dot for products of vegetarian origin.',
 'DECLARATION', TRUE, 'Soap, shampoos, tooth pastes and other specified cosmetics/toiletries', '2014-07-01', NULL, '44444444-4444-4444-4444-444444440009',
 '11111111-1111-1111-1111-111111111111', NULL, 'IN_FORCE'),
('55555555-5555-5555-5555-555555550021','33333333-3333-3333-3333-333333330006','9',NULL,
 'Imported-package supplementary label permitted',
 'A label may be affixed to imported packages for making the declarations required under the Rules, without prejudice to the other provisions of Rule 6.',
 'DECLARATION', FALSE, 'Imported pre-packaged commodities', '2015-05-14', NULL, '44444444-4444-4444-4444-444444440010',
 '11111111-1111-1111-1111-111111111112', NULL, 'IN_FORCE'),
('55555555-5555-5555-5555-555555550022','33333333-3333-3333-3333-333333330006','10',NULL,
 'Mandatory declarations displayed on e-commerce network',
 'An e-commerce entity shall ensure that the mandatory declarations specified in Rule 6(1), except the month and year in which the commodity is manufactured or packed, are displayed on the digital/electronic network used for e-commerce transactions. In marketplace models, responsibility for correctness follows the Rule 6(10) proviso.',
 'PLATFORM_FUNCTIONALITY', TRUE, 'E-commerce listings of pre-packaged commodities', '2018-01-01', NULL, '44444444-4444-4444-4444-444444440012',
 '11111111-1111-1111-1111-111111111114', NULL, 'IN_FORCE'),
('55555555-5555-5555-5555-555555550023','33333333-3333-3333-3333-333333330003','1',NULL,
 'Chapter applicability exclusions',
 'The chapter does not apply to packages containing more than 25 kg or 25 litre; cement, fertilizer and agricultural farm produce sold in bags above 50 kg; and packaged commodities meant for industrial or institutional consumers.',
 'PROCEDURAL', FALSE, 'Chapter-level applicability', '2018-01-01', NULL, '44444444-4444-4444-4444-444444440012',
 '11111111-1111-1111-1111-111111111114', NULL, 'IN_FORCE'),
('55555555-5555-5555-5555-555555550024','33333333-3333-3333-3333-333333330007','2',NULL,
 'Minimum height of numerals and letters',
 'Rule 7 prescribes minimum height requirements for numerals and letters used in declarations; larger type is permitted. For medical devices, the Medical Devices Rules, 2017 cross-reference introduced by G.S.R. 778(E) applies where applicable.',
 'MANNER_OF_DECLARATION', TRUE, 'Pre-packaged commodities subject to Rule 7', '2011-04-01', NULL, NULL,
 '11111111-1111-1111-1111-111111111102', NULL, 'IN_FORCE')
ON CONFLICT (rule_version_id) DO NOTHING;

-- Correct current unit-sale-price text and provenance.
UPDATE rule_versions SET
    requirement = 'Unit sale price declaration',
    legal_text_or_paraphrase = 'The unit sale price shall be declared as: Rs.__ per g for net quantity below 1 kg; Rs.__ per kg for net quantity of 1 kg or more; Rs.__ per cm for net length below 1 m; Rs.__ per meter for net length of 1 m or more; Rs.__ per number; Rs.__ per ml for net volume below 1 litre; and Rs.__ per litre for net volume of 1 litre or more.',
    source_id = '11111111-1111-1111-1111-111111111106',
    status = 'IN_FORCE'
WHERE rule_version_id = '55555555-5555-5555-5555-555555550013';

-- 2023 electronic QR provision: replace the old “VERIFICATION REQUIRED” prose
-- with the confirmed Gazette substance, while keeping it separate from ordinary OCR checks.
UPDATE rule_versions SET
    requirement = 'Electronic-product QR-code information notice',
    legal_text_or_paraphrase = 'For electronic products, the package shall inform consumers to scan the QR code for the manufacturer/packer/importer address and related information where declared through QR code and not on the package; for common/generic name and, where applicable, name and number/quantity of each product; and for dimensions, where those declarations are made through QR code and not on the package itself. Rule 6(2) also requires the package itself to declare telephone number and e-mail address and inform consumers to scan the QR code for other related information where so declared.',
    requirement_type = 'PLATFORM_FUNCTIONALITY',
    source_id = '11111111-1111-1111-1111-111111111108',
    status = 'IN_FORCE'
WHERE rule_version_id = '55555555-5555-5555-5555-555555550015';

-- Pan masala correction: G.S.R. 881(E) is a Rule 26 exemption, NOT a Rule 6 declaration.
UPDATE rule_versions SET
    requirement = 'Rule 26(a) pan-masala exception',
    legal_text_or_paraphrase = 'The provisions of Rule 26(a) do not apply to pan masala, with effect from 1 February 2026.',
    requirement_type = 'PROCEDURAL',
    is_mandatory = FALSE,
    product_scope = 'Pan masala',
    status = 'IN_FORCE',
    source_id = '11111111-1111-1111-1111-111111111109'
WHERE rule_version_id = '55555555-5555-5555-5555-555555550016';

-- ---------------------------------------------------------------------
-- E. Applicability rules: deterministic inputs for the engine
-- ---------------------------------------------------------------------
INSERT INTO rule_applicability (rule_version_id, product_category, imported, ecommerce, food, cosmetic, electronic_product,
    quantity_type, condition_expression, applicable_result, reason, source_id)
VALUES
('55555555-5555-5555-5555-555555550017', NULL, TRUE, NULL, NULL, NULL, NULL,
 NULL, '{"field":"imported","eq":true}', 'REQUIRED', 'Country of origin is required for imported products.', '11111111-1111-1111-1111-111111111114'),
('55555555-5555-5555-5555-555555550018', 'FOOD', NULL, NULL, TRUE, NULL, NULL,
 NULL, '{"field":"may_become_unfit","eq":true}', 'CONDITIONAL', 'Best-before/use-by declaration depends on whether the commodity may become unfit over time and applicable food-law provisos.', '11111111-1111-1111-1111-111111111102'),
('55555555-5555-5555-5555-555555550019', 'FOOD', NULL, NULL, TRUE, NULL, NULL,
 NULL, '{"field":"genetically_modified","eq":true}', 'REQUIRED', 'GM food triggers the Rule 6(7) declaration.', '11111111-1111-1111-1111-111111111110'),
('55555555-5555-5555-5555-555555550020', 'COSMETIC', NULL, NULL, NULL, TRUE, NULL,
 NULL, '{"all":[{"field":"category","eq":"COSMETIC"},{"field":"subcategory","in":["SOAP","SHAMPOO","TOOTHPASTE","COSMETIC_TOILETRY"]}]}', 'CONDITIONAL', 'Rule 6(8) applies to specified cosmetics/toiletries; origin classification determines the dot declaration.', '11111111-1111-1111-1111-111111111111'),
('55555555-5555-5555-5555-555555550021', NULL, TRUE, NULL, NULL, NULL, NULL,
 NULL, '{"field":"imported","eq":true}', 'CONDITIONAL', 'Imported packages may use an affixed label for required declarations; this is a permitted manner, not a blanket exemption.', '11111111-1111-1111-1111-111111111112'),
('55555555-5555-5555-5555-555555550022', NULL, NULL, TRUE, NULL, NULL, NULL,
 NULL, '{"field":"ecommerce","eq":true}', 'REQUIRED', 'E-commerce entities must display the Rule 6(1) mandatory declarations on the digital/electronic network, subject to Rule 6(10) exceptions/provisos.', '11111111-1111-1111-1111-111111111114'),
('55555555-5555-5555-5555-555555550023', NULL, NULL, NULL, NULL, NULL, NULL,
 NULL, '{"any":[{"field":"quantity_kg","gt":25},{"field":"quantity_litre","gt":25},{"all":[{"field":"subcategory","in":["CEMENT","FERTILIZER","AGRICULTURAL_FARM_PRODUCE"]},{"field":"quantity_kg","gt":50}]}]}', 'NOT_APPLICABLE', 'Chapter-level exclusion under Rule 3 for specified quantities/products.', '11111111-1111-1111-1111-111111111114'),
('55555555-5555-5555-5555-555555550024', NULL, NULL, NULL, NULL, NULL, NULL,
 NULL, '{"field":"medical_device","eq":true}', 'CONDITIONAL', 'For medical-device packages, the 2025 amendment cross-references the Medical Devices Rules, 2017 for declaration placement/character dimensions.', '11111111-1111-1111-1111-111111111115')
ON CONFLICT (applicability_id) DO NOTHING;

-- Unit-sale-price applicability: explicit quantity modes and current official FAQ exception.
INSERT INTO rule_applicability (rule_version_id, quantity_type, condition_expression, applicable_result, reason, source_id)
VALUES
('55555555-5555-5555-5555-555555550013','weight','{"all":[{"field":"quantity_type","eq":"weight"},{"field":"combination_package","eq":false},{"field":"group_package","eq":false},{"field":"multi_piece_package","eq":false}]}','REQUIRED','Unit sale price applies to ordinary weight-based pre-packaged commodities; the package-type exceptions must be evaluated separately.','11111111-1111-1111-1111-111111111106'),
('55555555-5555-5555-5555-555555550013','volume','{"all":[{"field":"quantity_type","eq":"volume"},{"field":"combination_package","eq":false},{"field":"group_package","eq":false},{"field":"multi_piece_package","eq":false}]}','REQUIRED','Unit sale price applies to ordinary volume-based pre-packaged commodities.','11111111-1111-1111-1111-111111111106'),
('55555555-5555-5555-5555-555555550013','length','{"all":[{"field":"quantity_type","eq":"length"},{"field":"combination_package","eq":false},{"field":"group_package","eq":false},{"field":"multi_piece_package","eq":false}]}','REQUIRED','Unit sale price applies to ordinary length-based pre-packaged commodities.','11111111-1111-1111-1111-111111111106'),
('55555555-5555-5555-5555-555555550013','number','{"all":[{"field":"quantity_type","eq":"number"},{"field":"combination_package","eq":false},{"field":"group_package","eq":false},{"field":"multi_piece_package","eq":false}]}','REQUIRED','Unit sale price applies to ordinary number-based pre-packaged commodities.','11111111-1111-1111-1111-111111111106'),
('55555555-5555-5555-5555-555555550013',NULL,'{"any":[{"field":"combination_package","eq":true},{"field":"group_package","eq":true},{"field":"multi_piece_package","eq":true}]}','NOT_REQUIRED','Official DCA FAQ states unit sale price is not required for combination, group or multi-piece packages. This is guidance and should be treated as a rule-engine exception only after confirming the current consolidated rule text.','11111111-1111-1111-1111-111111111116')
ON CONFLICT (applicability_id) DO NOTHING;

-- Pan masala exception represented as an exception, not a positive declaration requirement.
INSERT INTO exceptions (exception_id, rule_version_id, condition, affected_product, start_date, explanation, source_id)
VALUES
('66666666-6666-6666-6666-666666660010','55555555-5555-5555-5555-555555550016',
 'product.subcategory = PAN_MASALA','Pan masala','2026-02-01',
 'Rule 26(a) does not apply to pan masala from 1 Feb 2026. Do not interpret this record as an exemption from Rule 6 declarations generally.',
 '11111111-1111-1111-1111-111111111109')
ON CONFLICT (exception_id) DO NOTHING;


-- ---------------------------------------------------------------------
-- J. Version the 2023 electronic-product QR provisos correctly.
-- The pre-2023 clauses remain historical versions; new rows become
-- effective from 23 Jun 2023 and carry the 2023 amendment lineage.
-- ---------------------------------------------------------------------
UPDATE rule_versions SET effective_to='2023-06-23', status='SUPERSEDED'
WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550001',
                          '55555555-5555-5555-5555-555555550009',
                          '55555555-5555-5555-5555-555555550010');
UPDATE rule_versions SET effective_to='2023-06-23', status='SUPERSEDED'
WHERE rule_version_id='55555555-5555-5555-5555-555555550006';

INSERT INTO rule_versions (rule_version_id, rule_id, sub_rule, clause, requirement, legal_text_or_paraphrase,
    requirement_type, is_mandatory, product_scope, effective_from, effective_to, amendment_id, source_id, supersedes, status)
VALUES
('55555555-5555-5555-5555-555555550025','33333333-3333-3333-3333-333333330006','1','(a)',
 'Name and address of manufacturer/packer/importer with electronic-product QR proviso',
 'Every package shall bear the name and address of the manufacturer, or where the manufacturer is not the packer, the name and address of the manufacturer and packer, and for an imported package the name and address of the importer. For electronic products, the package shall declare the manufacturer/packer/importer name on the package itself and inform consumers to scan the QR code for the address and related information where that information is declared through the QR code and not on the package itself.',
 'DECLARATION', TRUE, 'All pre-packaged commodities; electronic-product QR proviso where applicable', '2023-06-23', NULL, '44444444-4444-4444-4444-444444440006', '11111111-1111-1111-1111-111111111108', '55555555-5555-5555-5555-555555550001', 'IN_FORCE'),
('55555555-5555-5555-5555-555555550026','33333333-3333-3333-3333-333333330006','1','(b)',
 'Common/generic name with electronic-product QR proviso',
 'Every package shall bear the common or generic name of the commodity and, where the package contains more than one product, the name and number or quantity of each product. For electronic products, the package shall inform consumers to scan the QR code for this information where it is declared through the QR code and not on the package itself.',
 'DECLARATION', TRUE, 'All pre-packaged commodities; electronic-product QR proviso where applicable', '2023-06-23', NULL, '44444444-4444-4444-4444-444444440006', '11111111-1111-1111-1111-111111111108', '55555555-5555-5555-5555-555555550009', 'IN_FORCE'),
('55555555-5555-5555-5555-555555550027','33333333-3333-3333-3333-333333330006','1','(f)',
 'Dimensions where relevant with electronic-product QR proviso',
 'Where the size/dimensions of the commodity are relevant, the dimensions shall be declared. For electronic products, the package shall inform consumers to scan the QR code for dimensions where that information is declared through the QR code and not on the package itself.',
 'DECLARATION', FALSE, 'Commodities where dimensions are relevant; electronic-product QR proviso where applicable', '2023-06-23', NULL, '44444444-4444-4444-4444-444444440006', '11111111-1111-1111-1111-111111111108', '55555555-5555-5555-5555-555555550010', 'IN_FORCE'),
('55555555-5555-5555-5555-555555550028','33333333-3333-3333-3333-333333330006','2',NULL,
 'Consumer-care details with electronic-product QR proviso',
 'Every package shall bear the name, address, telephone number and e-mail address of the person or office that can be contacted in case of consumer complaints. For electronic products, the package itself shall declare the telephone number and e-mail address and may additionally inform consumers to scan the QR code for other related information where such information is declared through the QR code and not on the package itself.',
 'DECLARATION', TRUE, 'All pre-packaged commodities; electronic-product QR proviso where applicable', '2023-06-23', NULL, '44444444-4444-4444-4444-444444440006', '11111111-1111-1111-1111-111111111108', '55555555-5555-5555-5555-555555550006', 'IN_FORCE');

UPDATE rule_versions SET superseded_by='55555555-5555-5555-5555-555555550025' WHERE rule_version_id='55555555-5555-5555-5555-555555550001';
UPDATE rule_versions SET superseded_by='55555555-5555-5555-5555-555555550026' WHERE rule_version_id='55555555-5555-5555-5555-555555550009';
UPDATE rule_versions SET superseded_by='55555555-5555-5555-5555-555555550027' WHERE rule_version_id='55555555-5555-5555-5555-555555550010';
UPDATE rule_versions SET superseded_by='55555555-5555-5555-5555-555555550028' WHERE rule_version_id='55555555-5555-5555-5555-555555550006';

INSERT INTO rule_applicability (rule_version_id, electronic_product, condition_expression, applicable_result, reason, source_id)
VALUES
('55555555-5555-5555-5555-555555550025',TRUE,'{"all":[{"field":"electronic_product","eq":true},{"field":"qr_declared","eq":true},{"field":"package_qr_notice_present","eq":true}]}','CONDITIONAL','For electronic products, address-related information may be conveyed through QR where the package carries the required QR notice; manufacturer/packer/importer name remains on-package.','11111111-1111-1111-1111-111111111108'),
('55555555-5555-5555-5555-555555550026',TRUE,'{"all":[{"field":"electronic_product","eq":true},{"field":"qr_declared","eq":true},{"field":"package_qr_notice_present","eq":true}]}','CONDITIONAL','For electronic products, common/generic name information may be supplied through QR where the package carries the required notice and the information is not on-package.','11111111-1111-1111-1111-111111111108'),
('55555555-5555-5555-5555-555555550027',TRUE,'{"all":[{"field":"electronic_product","eq":true},{"field":"qr_declared","eq":true},{"field":"package_qr_notice_present","eq":true}]}','CONDITIONAL','For electronic products, dimensions may be supplied through QR where the package carries the required notice and the information is not on-package.','11111111-1111-1111-1111-111111111108'),
('55555555-5555-5555-5555-555555550028',TRUE,'{"field":"electronic_product","eq":true}','CONDITIONAL','For electronic products, telephone number and e-mail address remain on-package; QR may carry other related information when the required notice is present.','11111111-1111-1111-1111-111111111108')
ON CONFLICT (applicability_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- F. Enforcement correction: MRP is Rule 6(1)(e), not 6(1)(f)
-- ---------------------------------------------------------------------
UPDATE enforcement_actions
SET legal_basis = 'Section 36, Legal Metrology Act, 2009 read with Rule 6(1)(e) of the Legal Metrology (Packaged Commodities) Rules, 2011'
WHERE action_id = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01';


-- Temporal/status corrections for Rule 6(10A): v1 remains operative until 30 Jun 2027.
UPDATE rule_versions
SET status = 'IN_FORCE', source_id = '11111111-1111-1111-1111-111111111103'
WHERE rule_version_id = '55555555-5555-5555-5555-555555550007';
UPDATE rule_versions
SET status = 'NOT_YET_IN_FORCE', source_id = '11111111-1111-1111-1111-111111111104'
WHERE rule_version_id = '55555555-5555-5555-5555-555555550008';
UPDATE amendments SET verification_status = 'VERIFIED_PRIMARY'
WHERE amendment_id IN ('44444444-4444-4444-4444-444444440001','44444444-4444-4444-4444-444444440002',
                       '44444444-4444-4444-4444-444444440003','44444444-4444-4444-4444-444444440004',
                       '44444444-4444-4444-4444-444444440005','44444444-4444-4444-4444-444444440006',
                       '44444444-4444-4444-4444-444444440007');

-- Version Rule 6(1)(d) and 6(1)(e) from 1 Oct 2022. The 2021 amendment was
-- originally dated for 1 Apr 2022 but G.S.R. 226(E) moved commencement to 1 Oct 2022.
UPDATE rule_versions SET effective_to='2022-10-01', status='SUPERSEDED'
WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550003','55555555-5555-5555-5555-555555550004');

INSERT INTO rule_versions (rule_version_id, rule_id, sub_rule, clause, requirement, legal_text_or_paraphrase,
    requirement_type, is_mandatory, product_scope, effective_from, effective_to, amendment_id, source_id, supersedes, status)
VALUES
('55555555-5555-5555-5555-555555550029','33333333-3333-3333-3333-333333330006','1','(d)',
 'Month and year of manufacture',
 'The month and year in which the commodity is manufactured shall be mentioned on the package, subject to the applicable provisos/exceptions under Rule 6(1)(d) and other applicable law.',
 'DECLARATION', TRUE, 'All pre-packaged commodities subject to Rule 6(1)(d)', '2022-10-01', NULL, '44444444-4444-4444-4444-444444440004', '11111111-1111-1111-1111-111111111106', '55555555-5555-5555-5555-555555550004', 'IN_FORCE'),
('55555555-5555-5555-5555-555555550030','33333333-3333-3333-3333-333333330006','1','(e)',
 'Retail sale price (MRP) in Indian currency, inclusive of all taxes',
 'The retail sale price of the package shall be declared in Indian currency. The retail sale price is the maximum price at which the commodity in packaged form may be sold to the consumer inclusive of all taxes, subject to the provisos in the Rules, including the special proviso for an essential commodity whose retail sale price is fixed and notified under the Essential Commodities Act, 1955.',
 'DECLARATION', TRUE, 'All pre-packaged commodities subject to applicable Rule 6 exclusions/provisos', '2022-10-01', NULL, '44444444-4444-4444-4444-444444440004', '11111111-1111-1111-1111-111111111106', '55555555-5555-5555-5555-555555550003', 'IN_FORCE');

UPDATE rule_versions SET superseded_by='55555555-5555-5555-5555-555555550029' WHERE rule_version_id='55555555-5555-5555-5555-555555550004';
UPDATE rule_versions SET superseded_by='55555555-5555-5555-5555-555555550030' WHERE rule_version_id='55555555-5555-5555-5555-555555550003';

UPDATE rule_versions
SET amendment_id = '44444444-4444-4444-4444-444444440010', source_id='11111111-1111-1111-1111-111111111112'
WHERE rule_version_id='55555555-5555-5555-5555-555555550006';

-- Current Rule 6(1)(aa) and 6(10) are part of the 2018-onward regime; make their
-- amendment lineage explicit.
UPDATE rule_versions
SET amendment_id = '44444444-4444-4444-4444-444444440012'
WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550017','55555555-5555-5555-5555-555555550022');

-- ---------------------------------------------------------------------
-- G. Enforcement provisions: make legal basis mapping explicit, without
-- inventing fine amounts or assuming every violation has the same remedy.
-- ---------------------------------------------------------------------
UPDATE enforcement_provisions
SET rule_id = '33333333-3333-3333-3333-333333330006',
    offence_description = 'Potential contravention involving a package that does not bear a declaration required by the applicable Packaged Commodities Rules; confirm the exact offence provision and current applicability before action.',
    source_id = '11111111-1111-1111-1111-111111111101'
WHERE enforcement_provision_id = (SELECT enforcement_provision_id FROM enforcement_provisions WHERE section='36' ORDER BY enforcement_provision_id LIMIT 1);

-- ---------------------------------------------------------------------
-- H. Audit-friendly views for the application layer
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_current_rule_requirements AS
SELECT
    rv.rule_version_id,
    lr.rule_number,
    rv.sub_rule,
    rv.clause,
    rv.requirement,
    rv.legal_text_or_paraphrase,
    rv.requirement_type,
    rv.is_mandatory,
    rv.product_scope,
    rv.effective_from,
    rv.effective_to,
    rv.status,
    rv.source_id
FROM rule_versions rv
JOIN legal_rules lr ON lr.rule_id = rv.rule_id
WHERE rv.status = 'IN_FORCE'
  AND rv.effective_from <= CURRENT_DATE
  AND (rv.effective_to IS NULL OR rv.effective_to > CURRENT_DATE);

CREATE OR REPLACE VIEW v_operational_legal_sources AS
SELECT source_id, source_type, title, issuing_authority, notification_number,
       publication_date, effective_date, url, authenticity_status, verified_at
FROM legal_sources
WHERE authenticity_status IN ('VERIFIED_PRIMARY','VERIFIED_SECONDARY');


-- ---------------------------------------------------------------------
-- H2. India state/UT master for inspection jurisdiction selection.
-- This is administrative reference data only; state enforcement provisions
-- must not be inferred from the state name and must be separately sourced.
-- ---------------------------------------------------------------------
INSERT INTO states (state_code, state_name) VALUES
('AP','Andhra Pradesh'),('AR','Arunachal Pradesh'),('AS','Assam'),('BR','Bihar'),
('CG','Chhattisgarh'),('GA','Goa'),('GJ','Gujarat'),('HR','Haryana'),('HP','Himachal Pradesh'),
('JH','Jharkhand'),('KA','Karnataka'),('KL','Kerala'),('MP','Madhya Pradesh'),('MH','Maharashtra'),
('MN','Manipur'),('ML','Meghalaya'),('MZ','Mizoram'),('NL','Nagaland'),('OD','Odisha'),
('PB','Punjab'),('RJ','Rajasthan'),('SK','Sikkim'),('TN','Tamil Nadu'),('TS','Telangana'),
('TR','Tripura'),('UP','Uttar Pradesh'),('UK','Uttarakhand'),('WB','West Bengal'),
('AN','Andaman and Nicobar Islands'),('CH','Chandigarh'),('DN','Dadra and Nagar Haveli and Daman and Diu'),
('DL','Delhi'),('JK','Jammu and Kashmir'),('LA','Ladakh'),('LD','Lakshadweep'),('PY','Puducherry')
ON CONFLICT (state_code) DO NOTHING;

-- ---------------------------------------------------------------------
-- I. Mark the dataset as v2 and state the legal-data safety policy.
-- ---------------------------------------------------------------------
UPDATE dataset_metadata
SET schema_version = '2.0.0',
    notes = 'LegalAkshi v2: Rule 6 operational seed expanded with Rule 3 applicability, Rule 6(aa),(da),(7),(8),(9),(10), current Rule 6(11), e-commerce and QR-code provisions, Rule 26 pan-masala exception, source provenance, official FAQ exceptions, and corrected MRP enforcement mapping. Legal records remain versioned; verification-required records must not drive automatic legal conclusions.';

COMMIT;

-- =====================================================================
-- 17. SAMPLE OPERATIONAL WORKFLOW (inspection -> court)
-- =====================================================================

-- Sample inspection
INSERT INTO inspections (inspection_id, inspector_id, inspector_name, department, state, district,
    business_id, business_name, inspection_type, inspection_date, inspection_time, location, status)
VALUES ('66666666-6666-6666-6666-666666660001','INSP-0231','R. Sharma','Directorate of Legal Metrology','West Bengal','Kolkata',
        'BUS-9981','Sunrise General Store','PHYSICAL','2026-09-12','11:20:00','Kolkata retail market','CLOSED');

-- Sample inspected product (imported cosmetic, NOT sold via e-commerce -> 10A not applicable; MRP missing)
INSERT INTO inspected_products (inspected_product_id, inspection_id, product_name, brand, manufacturer, importer,
    country_of_origin, category, quantity, quantity_unit, quantity_type, imported, ecommerce, barcode)
VALUES ('77777777-7777-7777-7777-777777770001','66666666-6666-6666-6666-666666660001','Glow Herbal Shampoo','GlowCo',
        'GlowCo Manufacturing Pte Ltd','Sunrise Imports Pvt Ltd','Thailand','COSMETIC',200,'ml','volume',TRUE,FALSE,'8901234567890');

-- Evidence
INSERT INTO inspection_evidence (evidence_id, inspection_id, product_id, evidence_type, file_path, capture_timestamp, hash, description)
VALUES ('88888888-8888-8888-8888-888888880001','66666666-6666-6666-6666-666666660001','77777777-7777-7777-7777-777777770001',
        'LABEL_IMAGE','/evidence/2026/09/12/IMG001.jpg','2026-09-12 11:22:00+05:30','sha256:abc123...','Front label photo');

-- OCR / extracted declarations
INSERT INTO extracted_declarations (evidence_id, field_name, extracted_value, normalized_value, confidence, ocr_engine)
VALUES
('88888888-8888-8888-8888-888888880001','MANUFACTURER_ADDRESS','GlowCo Manufacturing Pte Ltd, Bangkok','GlowCo Manufacturing Pte Ltd, Bangkok, Thailand',0.910,'legalakshi-ocr-v1'),
('88888888-8888-8888-8888-888888880001','NET_QUANTITY','200 ml','200 ml',0.980,'legalakshi-ocr-v1'),
('88888888-8888-8888-8888-888888880001','MRP','NOT_DETECTED', NULL, 0.150,'legalakshi-ocr-v1');

-- Compliance results
INSERT INTO compliance_results (compliance_result_id, inspection_id, product_id, rule_version_id, requirement,
    expected_value, detected_value, result, confidence, evidence_id, engine_version, explanation)
VALUES
('99999999-9999-9999-9999-999999990001','66666666-6666-6666-6666-666666660001','77777777-7777-7777-7777-777777770001',
 '55555555-5555-5555-5555-555555550025','Name and address of manufacturer/importer','Present','GlowCo Manufacturing Pte Ltd, Bangkok, Thailand; Importer: Sunrise Imports Pvt Ltd','PASS',0.910,
 '88888888-8888-8888-8888-888888880001','1.0.0','Manufacturer and importer details detected on label.'),
('99999999-9999-9999-9999-999999990002','66666666-6666-6666-6666-666666660001','77777777-7777-7777-7777-777777770001',
 '55555555-5555-5555-5555-555555550030','Retail sale price (MRP)','Present, inclusive of all taxes','NOT_DETECTED','FAIL',0.150,
 '88888888-8888-8888-8888-888888880001','1.0.0','No MRP declaration detected on the label at acceptable confidence.'),
('99999999-9999-9999-9999-999999990003','66666666-6666-6666-6666-666666660001','77777777-7777-7777-7777-777777770001',
 '55555555-5555-5555-5555-555555550007','Country-of-origin searchable e-commerce filter', NULL, NULL,'NOT_APPLICABLE',NULL,
 NULL,'1.0.0','Product was inspected as physical retail stock (ecommerce=false); Rule 6(10A) does not apply.');

-- Potential violation (from the FAIL result)
INSERT INTO violations (violation_id, inspection_id, product_id, compliance_result_id, rule_version_id, act_provision_id,
    violation_type, description, detected_value, expected_value, evidence_id, ai_confidence, inspector_status)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01','66666666-6666-6666-6666-666666660001','77777777-7777-7777-7777-777777770001',
        '99999999-9999-9999-9999-999999990002','55555555-5555-5555-5555-555555550030',
        (SELECT act_provision_id FROM act_provisions WHERE section_number='36' LIMIT 1),
        'MISSING_DECLARATION','Potential missing MRP declaration on imported cosmetic package','NOT_DETECTED',
        'Present, inclusive of all taxes','88888888-8888-8888-8888-888888880001',0.850,'PENDING');

-- Inspector confirms
UPDATE violations SET inspector_status = 'CONFIRMED', inspector_id = 'INSP-0231',
    verification_date = '2026-09-12', inspector_remarks = 'Physically re-checked label; MRP genuinely absent.'
WHERE violation_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01';

-- Enforcement action: notice issued
INSERT INTO enforcement_actions (action_id, violation_id, inspection_id, action_type, legal_basis,
    enforcement_provision_id, authority, action_date, action_status, notice_number, created_by)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01','66666666-6666-6666-6666-666666660001',
        'NOTICE','Section 36, Legal Metrology Act, 2009 read with Rule 6(1)(e) of the Legal Metrology (Packaged Commodities) Rules, 2011',
        (SELECT enforcement_provision_id FROM enforcement_provisions WHERE section='36' LIMIT 1),
        'Directorate of Legal Metrology, West Bengal','2026-09-15','ISSUED','LM/WB/2026/0456','INSP-0231');

INSERT INTO notices (notice_id, violation_id, action_id, notice_number, notice_type, issue_date, issued_by,
    served_date, response_due_date)
VALUES ('cccccccc-cccc-cccc-cccc-cccccccccc01','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01','bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01',
        'LM/WB/2026/0456','SHOW_CAUSE','2026-09-15','R. Sharma','2026-09-16','2026-09-30');

-- Matter escalates to compounding (illustrative — amount NOT invented, left NULL pending order)
INSERT INTO compounding_cases (compounding_id, violation_id, enforcement_provision_id, competent_authority,
    application_date, status)
VALUES ('dddddddd-dddd-dddd-dddd-dddddddddd01','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01',
        (SELECT enforcement_provision_id FROM enforcement_provisions WHERE section='48' LIMIT 1),
        'Director, Legal Metrology, West Bengal','2026-10-02','APPLICATION_RECEIVED');

-- If compounding fails/is declined, illustrative prosecution/court case
INSERT INTO legal_cases (case_id, inspection_id, violation_id, enforcement_action_id, case_type, case_number,
    court_name, court_level, jurisdiction, filing_date, status, next_hearing_date)
VALUES ('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','66666666-6666-6666-6666-666666660001','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01',
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01','PROSECUTION','LM-CC-2026-0089',
        'Chief Judicial Magistrate Court, Kolkata','District/Magistrate','West Bengal','2026-11-05','FILED','2026-12-10');

-- Case timeline
INSERT INTO case_events (case_id, event_type, event_date, performed_by, description, previous_status, new_status) VALUES
('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','SCAN','2026-09-12','LegalAkshi Engine','Package scanned and OCR run', NULL, NULL),
('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','AI_DETECTION','2026-09-12','LegalAkshi Engine','AI flagged potential missing MRP declaration', NULL, NULL),
('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','INSPECTOR_CONFIRMATION','2026-09-12','INSP-0231','Inspector confirmed the violation','PENDING','CONFIRMED'),
('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','NOTICE_ISSUED','2026-09-15','R. Sharma','Show-cause notice issued', NULL,'NOTICE_ISSUED'),
('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','RESPONSE_RECEIVED','2026-09-28','Business owner','Response received disputing the finding','NOTICE_ISSUED','RESPONSE_PENDING'),
('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','COMPOUNDING_APPLIED','2026-10-02','Directorate','Compounding application received','RESPONSE_PENDING','UNDER_REVIEW'),
('eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01','CASE_FILED','2026-11-05','Directorate','Prosecution filed after compounding declined','UNDER_REVIEW','FILED');

-- =====================================================================
-- 18. EXAMPLE QUERIES
-- =====================================================================

-- 18.1 Currently applicable Rule 6 requirements for a given product/date
-- (parameterise :as_of_date, :category, :imported, :ecommerce in application code)
/*
SELECT rv.rule_version_id, lr.rule_number, rv.sub_rule, rv.requirement, rv.legal_text_or_paraphrase,
       ra.applicable_result, ra.reason
FROM rule_versions rv
JOIN legal_rules lr ON lr.rule_id = rv.rule_id
LEFT JOIN rule_applicability ra ON ra.rule_version_id = rv.rule_version_id
WHERE rv.effective_from <= :as_of_date
  AND (rv.effective_to IS NULL OR rv.effective_to > :as_of_date)
  AND (ra.product_category IS NULL OR ra.product_category = :category)
  AND (ra.imported IS NULL OR ra.imported = :imported)
  AND (ra.ecommerce IS NULL OR ra.ecommerce = :ecommerce);
*/

-- 18.2 Requirements applicable to an imported electronic product sold via e-commerce, as of a date
/*
SELECT rv.*, ra.applicable_result, ra.reason
FROM rule_versions rv
JOIN rule_applicability ra ON ra.rule_version_id = rv.rule_version_id
WHERE rv.effective_from <= :as_of_date
  AND (rv.effective_to IS NULL OR rv.effective_to > :as_of_date)
  AND (ra.product_category IS NULL OR ra.product_category = 'ELECTRONIC')
  AND (ra.imported IS NULL OR ra.imported = TRUE)
  AND (ra.ecommerce IS NULL OR ra.ecommerce = TRUE);
*/

-- 18.3 Requirements applicable to a food product, as of a date
/*
SELECT rv.*, ra.applicable_result
FROM rule_versions rv
JOIN rule_applicability ra ON ra.rule_version_id = rv.rule_version_id
WHERE rv.effective_from <= :as_of_date AND (rv.effective_to IS NULL OR rv.effective_to > :as_of_date)
  AND (ra.product_category IS NULL OR ra.product_category = 'FOOD');
*/

-- 18.4 Requirements applicable to shampoo/cosmetics
/*
SELECT rv.*, ra.applicable_result
FROM rule_versions rv
JOIN rule_applicability ra ON ra.rule_version_id = rv.rule_version_id
WHERE rv.effective_from <= CURRENT_DATE AND (rv.effective_to IS NULL OR rv.effective_to > CURRENT_DATE)
  AND (ra.product_category IS NULL OR ra.product_category = 'COSMETIC');
*/

-- 18.5 Which legal version of Rule 6(10A) was applicable on a historical date
SELECT rule_version_id, sub_rule, requirement, effective_from, effective_to, status
FROM rule_versions
WHERE rule_id = '33333333-3333-3333-3333-333333330006'
  AND sub_rule = '10A'
  AND effective_from <= DATE '2026-08-01'
  AND (effective_to IS NULL OR effective_to > DATE '2026-08-01');

-- 18.6 All violations from an inspection
SELECT v.* FROM violations v WHERE v.inspection_id = '66666666-6666-6666-6666-666666660001';

-- 18.7 Legal provision behind a violation
SELECT v.violation_id, rv.requirement, rv.legal_text_or_paraphrase, ap.section_number, ap.title
FROM violations v
JOIN rule_versions rv ON rv.rule_version_id = v.rule_version_id
LEFT JOIN act_provisions ap ON ap.act_provision_id = v.act_provision_id
WHERE v.violation_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01';

-- 18.8 Available enforcement pathways for a confirmed violation
SELECT ep.*
FROM violations v
JOIN act_provisions ap ON ap.act_provision_id = v.act_provision_id
JOIN enforcement_provisions ep ON ep.act_id = ap.act_id AND ep.section = ap.section_number
WHERE v.violation_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa01'
  AND v.inspector_status = 'CONFIRMED';

-- 18.9 All pending inspector verifications
SELECT * FROM violations WHERE inspector_status = 'PENDING';

-- 18.10 All cases currently in court
SELECT * FROM legal_cases WHERE case_type IN ('COURT','PROSECUTION') AND status NOT IN ('CLOSED','DISPOSED');

-- 18.11 All compounded cases
SELECT * FROM compounding_cases WHERE status = 'COMPOUNDED';

-- 18.12 Inspection compliance summary
SELECT i.inspection_id, ip.product_name, cr.requirement, cr.result, rv.sub_rule, ev.evidence_id
FROM inspections i
JOIN inspected_products ip ON ip.inspection_id = i.inspection_id
JOIN compliance_results cr ON cr.product_id = ip.inspected_product_id
JOIN rule_versions rv ON rv.rule_version_id = cr.rule_version_id
LEFT JOIN inspection_evidence ev ON ev.evidence_id = cr.evidence_id
WHERE i.inspection_id = '66666666-6666-6666-6666-666666660001'
ORDER BY rv.sub_rule;

-- 18.13 Case timeline
SELECT event_date, event_type, description, previous_status, new_status
FROM case_events
WHERE case_id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeee01'
ORDER BY event_date;

-- =====================================================================
-- END OF FILE
-- =====================================================================
-- =====================================================================
-- LEGALAKSHI RULE-ENGINE REGISTRY PATCH  (v3.0)
-- Apply AFTER legalakshi_schema_v2.sql
--
-- Purpose: close the gap identified when comparing the standalone
-- "LegalAkshi_Rule_Engine_JSON" package against the Postgres schema —
-- that package had no amendment provenance, no supersedes chain, no
-- clause-level specificity, and used an ID space (LMPC-001…) with no
-- relationship to this database's UUIDs, so the two artifacts could
-- never be kept in sync.
--
-- This patch instead adds the rule-engine's concepts (a stable
-- checkable-requirement identity, a check_type vocabulary, and
-- application-level scoring weights) AS TABLES IN THIS DATABASE, and
-- links every existing rule_versions row to the check it belongs to.
-- The JSON package for the frontend is then GENERATED from these
-- tables (see generate_rule_engine_json.py) instead of hand-authored,
-- so the two can never drift apart again.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- A. Check-type and severity vocabulary
-- ---------------------------------------------------------------------
CREATE TABLE lkp_check_type (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_check_type (code, description) VALUES
 ('FIELD_PRESENT','A required field must be detected with sufficient confidence'),
 ('FIELD_ABSENT','A field must NOT be present (e.g. a prohibited claim)'),
 ('TEXT_MATCH','Detected text must exactly match an expected value'),
 ('TEXT_CONTAINS','Detected text must contain a required substring/phrase'),
 ('REGEX_MATCH','Detected text must match a prescribed pattern'),
 ('NUMERIC_COMPARE','A numeric value must satisfy a comparison (e.g. >= a threshold)'),
 ('UNIT_NORMALIZATION','A quantity must be present and normalizable to a standard unit'),
 ('DATE_VALID','A date field must be present and a syntactically valid date'),
 ('DATE_REQUIRED_IF','A date field is required only when a condition holds (e.g. perishable)'),
 ('CONDITIONAL_FIELD_PRESENT','A field is required only when applicability conditions hold'),
 ('QR_DATA_PRESENT','A decodable QR payload must be present'),
 ('QR_OR_ON_PACKAGE','Information must appear either on-package or via a compliant QR code'),
 ('FORMAT_VALID','A field must be present and match a prescribed format (e.g. currency)'),
 ('CROSS_FIELD_COMPARE','Two or more detected fields must be mutually consistent'),
 ('PLATFORM_FILTER_PRESENT','An e-commerce platform must expose a required searchable/sortable filter'),
 ('MANUAL_REVIEW','Cannot be automated; always routes to inspector review');

CREATE TABLE lkp_scoring_severity (code TEXT PRIMARY KEY, description TEXT NOT NULL);
INSERT INTO lkp_scoring_severity (code, description) VALUES
 ('HIGH','High-severity finding for application-level scoring purposes'),
 ('MEDIUM','Medium-severity finding'),
 ('LOW','Low-severity finding');

-- ---------------------------------------------------------------------
-- B. Engine check registry — the STABLE identity of a checkable
--    requirement (e.g. "the MRP declaration"), independent of which
--    rule_versions row currently defines its legal text. This is the
--    join point between the legal knowledge base and the rule engine.
-- ---------------------------------------------------------------------
CREATE TABLE engine_check_registry (
    check_id            TEXT PRIMARY KEY,        -- stable slug, e.g. 'CHK-MRP'
    rule_id              UUID NOT NULL REFERENCES legal_rules(rule_id),
    sub_rule              TEXT,                   -- groups rule_versions rows into one lineage
    clause                 TEXT,
    title                    TEXT NOT NULL,
    field_name                TEXT NOT NULL,       -- OCR/extracted_declarations.field_name this check reads
    check_type                 TEXT NOT NULL REFERENCES lkp_check_type(code),
    mandatory_default            BOOLEAN NOT NULL DEFAULT TRUE, -- overridden per-product by rule_applicability
    scoring_category              TEXT NOT NULL,
    description                     TEXT,
    UNIQUE (rule_id, sub_rule, clause)
);

-- Link every existing rule_versions row to its check (nullable — not
-- every provision is an OCR-checkable declaration; Rule 3 chapter
-- exclusions and Rule 26 exemptions are procedural, not fields).
ALTER TABLE rule_versions ADD COLUMN check_id TEXT REFERENCES engine_check_registry(check_id);
CREATE INDEX idx_rule_versions_check ON rule_versions(check_id);

-- ---------------------------------------------------------------------
-- C. Scoring policy — application-level only, kept separate from
--    legal truth (a rule can be a legal REQUIRED without carrying any
--    scoring weight, and vice versa is never true: weights never
--    imply a legal conclusion).
-- ---------------------------------------------------------------------
CREATE TABLE scoring_policies (
    policy_id            TEXT PRIMARY KEY,
    name                   TEXT NOT NULL,
    version                  TEXT NOT NULL,
    active                     BOOLEAN NOT NULL DEFAULT TRUE,
    calc_method                 TEXT NOT NULL DEFAULT 'WEIGHTED_FINDINGS',
    review_handling               TEXT NOT NULL DEFAULT 'DO_NOT_FINALIZE_WITHOUT_REVIEW',
    not_applicable_handling         TEXT NOT NULL DEFAULT 'EXCLUDE_FROM_DENOMINATOR',
    note                              TEXT
);

CREATE TABLE scoring_policy_weights (
    policy_id      TEXT NOT NULL REFERENCES scoring_policies(policy_id),
    check_id         TEXT NOT NULL REFERENCES engine_check_registry(check_id),
    weight             NUMERIC NOT NULL CHECK (weight >= 0),
    severity              TEXT NOT NULL REFERENCES lkp_scoring_severity(code),
    PRIMARY KEY (policy_id, check_id)
);

-- ---------------------------------------------------------------------
-- D. Populate the check registry — every OCR-checkable Rule 6/Rule 6(11)
--    declaration currently in the schema, with FULL clause specificity
--    (this is what the standalone JSON package was missing).
-- ---------------------------------------------------------------------
INSERT INTO engine_check_registry (check_id, rule_id, sub_rule, clause, title, field_name, check_type, mandatory_default, scoring_category, description) VALUES
('CHK-MANUFACTURER','33333333-3333-3333-3333-333333330006','1','(a)','Manufacturer / Packer / Importer Details','manufacturer','QR_OR_ON_PACKAGE',TRUE,'MANUFACTURER','Name and address of manufacturer/packer/importer; electronic products may convey the address via QR under the current version.'),
('CHK-COMMON-NAME','33333333-3333-3333-3333-333333330006','1','(b)','Common / Generic Name','common_generic_name','FIELD_PRESENT',TRUE,'COMMON_NAME','Common or generic name of the commodity.'),
('CHK-NET-QTY','33333333-3333-3333-3333-333333330006','1','(c)','Net Quantity','net_quantity','UNIT_NORMALIZATION',TRUE,'NET_QUANTITY','Net quantity in standard weight/measure/number.'),
('CHK-MFG-DATE','33333333-3333-3333-3333-333333330006','1','(d)','Month/Year of Manufacture','mfg_month_year','DATE_VALID',TRUE,'MFG_DATE','Month and year of manufacture/packing/import.'),
('CHK-MRP','33333333-3333-3333-3333-333333330006','1','(e)','Maximum Retail Price','mrp','FIELD_PRESENT',TRUE,'MRP','Retail sale price inclusive of all taxes.'),
('CHK-DIMENSIONS','33333333-3333-3333-3333-333333330006','1','(f)','Dimensions','dimensions','CONDITIONAL_FIELD_PRESENT',FALSE,'DIMENSIONS','Dimensions of the commodity, where relevant.'),
('CHK-OTHER-MATTERS','33333333-3333-3333-3333-333333330006','1','(g)','Other Specified Declarations','other_declarations','MANUAL_REVIEW',FALSE,'OTHER','Any other matter the Rules require, not separately enumerated.'),
('CHK-COUNTRY-ORIGIN','33333333-3333-3333-3333-333333330006','1','(aa)','Country of Origin','country_of_origin','CONDITIONAL_FIELD_PRESENT',FALSE,'COUNTRY_OF_ORIGIN','Country of origin/manufacture/assembly for imported products.'),
('CHK-BEST-BEFORE','33333333-3333-3333-3333-333333330006','1','(da)','Best Before / Use By','best_before','DATE_REQUIRED_IF',FALSE,'BEST_BEFORE','Best-before/use-by date where the commodity may become unfit over time.'),
('CHK-CONSUMER-CARE','33333333-3333-3333-3333-333333330006','2',NULL,'Consumer Care Details','consumer_care','FIELD_PRESENT',TRUE,'CONSUMER_CARE','Name, address, telephone and e-mail for consumer complaints.'),
('CHK-GM-FOOD','33333333-3333-3333-3333-333333330006','7',NULL,'GM Food Declaration','gm_label','CONDITIONAL_FIELD_PRESENT',FALSE,'GM_FOOD','"GM" declaration on the principal display panel for genetically modified food.'),
('CHK-VEG-NONVEG','33333333-3333-3333-3333-333333330006','8',NULL,'Veg / Non-Veg Dot','veg_nonveg_dot','CONDITIONAL_FIELD_PRESENT',FALSE,'VEG_NONVEG','Green/brown-red dot for specified cosmetics/toiletries.'),
('CHK-UNIT-SALE-PRICE','33333333-3333-3333-3333-333333330006','11',NULL,'Unit Sale Price','unit_sale_price','CONDITIONAL_FIELD_PRESENT',FALSE,'UNIT_SALE_PRICE','Unit sale price per prescribed standard unit.'),
('CHK-ECOMMERCE-DECL','33333333-3333-3333-3333-333333330006','10',NULL,'E-commerce Mandatory Declarations','ecommerce_declarations','PLATFORM_FILTER_PRESENT',FALSE,'ECOMMERCE','Rule 6(1) declarations displayed on the e-commerce digital network.'),
('CHK-ECOMMERCE-COO-FILTER','33333333-3333-3333-3333-333333330006','10A',NULL,'E-commerce Country-of-Origin Filter','ecommerce_coo_filter','PLATFORM_FILTER_PRESENT',FALSE,'ECOMMERCE_COO','Searchable/sortable country-of-origin filter for imported products sold via e-commerce.');

-- ---------------------------------------------------------------------
-- E. Link EVERY rule_versions row (across its whole amendment lineage)
--    to its check — this is what gives the JSON export a full,
--    chronologically ordered version history per check, instead of
--    the flat single-version list the standalone package had.
-- ---------------------------------------------------------------------
UPDATE rule_versions SET check_id = 'CHK-MANUFACTURER' WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550001','55555555-5555-5555-5555-555555550025');
UPDATE rule_versions SET check_id = 'CHK-COMMON-NAME'  WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550009','55555555-5555-5555-5555-555555550026');
UPDATE rule_versions SET check_id = 'CHK-NET-QTY'       WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550002');
UPDATE rule_versions SET check_id = 'CHK-MFG-DATE'       WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550004','55555555-5555-5555-5555-555555550029');
UPDATE rule_versions SET check_id = 'CHK-MRP'             WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550003','55555555-5555-5555-5555-555555550030');
UPDATE rule_versions SET check_id = 'CHK-DIMENSIONS'       WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550010','55555555-5555-5555-5555-555555550027');
UPDATE rule_versions SET check_id = 'CHK-OTHER-MATTERS'     WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550011');
UPDATE rule_versions SET check_id = 'CHK-COUNTRY-ORIGIN'     WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550017');
UPDATE rule_versions SET check_id = 'CHK-BEST-BEFORE'         WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550018');
UPDATE rule_versions SET check_id = 'CHK-CONSUMER-CARE'        WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550005','55555555-5555-5555-5555-555555550006','55555555-5555-5555-5555-555555550028');
UPDATE rule_versions SET check_id = 'CHK-GM-FOOD'                WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550019');
UPDATE rule_versions SET check_id = 'CHK-VEG-NONVEG'              WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550020');
UPDATE rule_versions SET check_id = 'CHK-UNIT-SALE-PRICE'          WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550012','55555555-5555-5555-5555-555555550013');
UPDATE rule_versions SET check_id = 'CHK-ECOMMERCE-DECL'            WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550022');
UPDATE rule_versions SET check_id = 'CHK-ECOMMERCE-COO-FILTER'       WHERE rule_version_id IN ('55555555-5555-5555-5555-555555550007','55555555-5555-5555-5555-555555550008');

-- Sanity check: every declaration/platform-functionality rule_version
-- for Rule 6 should now have a check_id, EXCEPT purely procedural rows
-- (Rule 6(9) imported-label permission, Rule 6(1) QR provisos folded
-- into the base clauses, Rule 3/26 exemptions) which stay NULL by design.

-- ---------------------------------------------------------------------
-- F. Scoring policy — DEFAULT-2026, full 15-category coverage
--    (the standalone package only weighted 4 of 9 categories; this
--    covers every check now in the registry, or explicitly excludes it)
-- ---------------------------------------------------------------------
INSERT INTO scoring_policies (policy_id, name, version, active, calc_method, review_handling, not_applicable_handling, note)
VALUES ('DEFAULT-2026','LegalAkshi Compliance Score','1.1',TRUE,'WEIGHTED_FINDINGS','DO_NOT_FINALIZE_WITHOUT_REVIEW','EXCLUDE_FROM_DENOMINATOR',
        'Application-level scoring weights only — never a statutory penalty value, never a substitute for inspector verification. Weight=0 checks are tracked for compliance visibility but excluded from the score.');

INSERT INTO scoring_policy_weights (policy_id, check_id, weight, severity) VALUES
('DEFAULT-2026','CHK-MRP',20,'HIGH'),
('DEFAULT-2026','CHK-MANUFACTURER',15,'HIGH'),
('DEFAULT-2026','CHK-NET-QTY',15,'HIGH'),
('DEFAULT-2026','CHK-CONSUMER-CARE',15,'MEDIUM'),
('DEFAULT-2026','CHK-COUNTRY-ORIGIN',15,'HIGH'),
('DEFAULT-2026','CHK-ECOMMERCE-DECL',15,'HIGH'),
('DEFAULT-2026','CHK-GM-FOOD',15,'HIGH'),
('DEFAULT-2026','CHK-MFG-DATE',10,'MEDIUM'),
('DEFAULT-2026','CHK-BEST-BEFORE',10,'MEDIUM'),
('DEFAULT-2026','CHK-VEG-NONVEG',10,'MEDIUM'),
('DEFAULT-2026','CHK-UNIT-SALE-PRICE',10,'MEDIUM'),
('DEFAULT-2026','CHK-ECOMMERCE-COO-FILTER',10,'MEDIUM'),
('DEFAULT-2026','CHK-COMMON-NAME',5,'LOW'),
('DEFAULT-2026','CHK-DIMENSIONS',5,'LOW'),
('DEFAULT-2026','CHK-OTHER-MATTERS',0,'LOW');

-- ---------------------------------------------------------------------
-- G. Bump dataset metadata
-- ---------------------------------------------------------------------
UPDATE dataset_metadata
SET schema_version = '3.0.0',
    notes = 'LegalAkshi v3: adds the engine_check_registry / scoring_policies layer that the rule-engine JSON package is now GENERATED FROM (see generate_rule_engine_json.py), so the frontend config and the legal database can never drift apart. 15 checkable requirements registered across Rule 6/6(11), full clause specificity, full amendment lineage per check, full scoring coverage.';

COMMIT;
