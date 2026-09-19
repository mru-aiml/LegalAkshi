-- Migration 004: consumer suggestion intake.
-- Consumers suggest improvements (authenticity, verification, awareness,
-- reporting). Suggestions live BEFORE any official workflow and follow
-- their own 5-status lifecycle, so they get a dedicated table rather than
-- distorting complaints or the authoritative legal model. Additive only:
-- no authoritative table is altered.
CREATE TABLE IF NOT EXISTS consumer_suggestions (
    suggestion_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    consumer_user_id TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    category      TEXT NOT NULL DEFAULT 'Other',
    description   TEXT NOT NULL DEFAULT '',
    context       TEXT NOT NULL DEFAULT '',
    location      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'SUBMITTED'
                  CHECK (status IN ('SUBMITTED','UNDER_REVIEW',
                      'ACKNOWLEDGED','ACTIONED','CLOSED')),
    reviewed_by   TEXT NOT NULL DEFAULT '',
    officer_note  TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_suggestions_consumer
    ON consumer_suggestions(consumer_user_id);
CREATE INDEX IF NOT EXISTS idx_suggestions_status
    ON consumer_suggestions(status);

CREATE TABLE IF NOT EXISTS suggestion_events (
    event_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    suggestion_id UUID NOT NULL REFERENCES consumer_suggestions(suggestion_id)
                  ON DELETE CASCADE,
    event_type    TEXT NOT NULL,
    from_status   TEXT,
    to_status     TEXT,
    actor_id      TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_suggestion_events_suggestion
    ON suggestion_events(suggestion_id);
