-- Migration 005: declaration corrections (Stage 2 human-in-the-loop).
-- Append-only officer correction history for Package Intelligence fields.
-- Original extraction evidence is NEVER overwritten: each correction row
-- keeps original_value + evidence_snapshot alongside corrected_value.
-- Only verified=true rows may enter the trusted learning dataset
-- (application-level rule, enforced in the learning service).
-- Additive only: no authoritative table is altered.
CREATE TABLE IF NOT EXISTS declaration_corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    inspection_id UUID NOT NULL,
    product_id UUID NOT NULL,
    field_key TEXT NOT NULL,
    original_value TEXT,
    corrected_value TEXT,
    original_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
    original_confidence DOUBLE PRECISION,
    source TEXT NOT NULL DEFAULT 'officer-review',
    evidence_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    officer_user_id TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    verified_by TEXT NOT NULL DEFAULT '',
    verified_at TIMESTAMPTZ,
    correction_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_corrections_inspection
    ON declaration_corrections(inspection_id);
CREATE INDEX IF NOT EXISTS idx_corrections_product
    ON declaration_corrections(product_id);
CREATE INDEX IF NOT EXISTS idx_corrections_field
    ON declaration_corrections(field_key);
CREATE INDEX IF NOT EXISTS idx_corrections_verified
    ON declaration_corrections(verified);
