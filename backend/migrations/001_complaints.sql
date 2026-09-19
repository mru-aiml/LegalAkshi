-- Migration 001: consumer complaint intake.
-- The authoritative schema models post-inspection enforcement (legal_cases,
-- enforcement_actions) but has no consumer intake entity: a complaint exists
-- BEFORE any inspection and follows its own 8-status lifecycle, so mapping it
-- onto lkp_case_status would distort the authoritative model. This additive
-- migration does not alter any authoritative table.
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reporter_id    TEXT NOT NULL DEFAULT '',
    product_name   TEXT NOT NULL DEFAULT '',
    retailer       TEXT NOT NULL DEFAULT '',
    city           TEXT NOT NULL DEFAULT '',
    severity       TEXT NOT NULL DEFAULT 'Medium',
    description    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'SUBMITTED'
                   CHECK (status IN ('SUBMITTED','ACKNOWLEDGED','UNDER_REVIEW',
                       'INSPECTION_SCHEDULED','INSPECTION_COMPLETED',
                       'ACTION_TAKEN','RESOLVED','CLOSED')),
    inspection_id  UUID REFERENCES inspections(inspection_id),
    violation_id   UUID REFERENCES violations(violation_id),
    evidence       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_complaints_reporter ON complaints(reporter_id);
CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status);

CREATE TABLE IF NOT EXISTS complaint_events (
    event_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    complaint_id   UUID NOT NULL REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    event_type     TEXT NOT NULL,
    from_status    TEXT,
    to_status      TEXT,
    actor_id       TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_complaint_events_complaint ON complaint_events(complaint_id);
