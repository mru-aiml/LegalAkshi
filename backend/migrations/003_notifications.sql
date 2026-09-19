-- Migration 003: notification inbox (application-level, additive).
-- Real application events (violation detected, review required, complaint
-- filed/status changed, rule sync) write rows here; the frontend bell reads
-- them. No fabricated content: every row traces to a persisted event.
-- audience: 'officer' (all officers), 'consumer' (broadcast), or
-- 'user:<user_id>' (one authenticated user).
CREATE TABLE IF NOT EXISTS notifications (
    notification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    audience        TEXT NOT NULL,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    link            TEXT NOT NULL DEFAULT '',
    read            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_audience
    ON notifications(audience, created_at DESC);
