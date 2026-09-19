-- Migration 002: enforce the complaint lifecycle statuses at the DB level.
-- (001 created the tables; this adds the CHECK from the lifecycle model
-- app/models/lifecycle.py so illegal statuses cannot be persisted even
-- outside the repository layer.)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'complaints_status_lifecycle') THEN
        ALTER TABLE complaints ADD CONSTRAINT complaints_status_lifecycle
            CHECK (status IN ('SUBMITTED','ACKNOWLEDGED','UNDER_REVIEW',
                'INSPECTION_SCHEDULED','INSPECTION_COMPLETED',
                'ACTION_TAKEN','RESOLVED','CLOSED'));
    END IF;
END $$;
