-- V023: remove the antivirus dependency from ticket attachment uploads.
-- The legacy scan_status column remains for database compatibility and history.
-- New uploads are recorded as 'not_scanned'; download authorization no longer
-- depends on scan_status. This migration does not delete attachment metadata or files.

BEGIN;

DO $$
DECLARE
    check_constraint RECORD;
BEGIN
    FOR check_constraint IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'public.ticket_attachments'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%scan_status%'
    LOOP
        EXECUTE format(
            'ALTER TABLE public.ticket_attachments DROP CONSTRAINT %I',
            check_constraint.conname
        );
    END LOOP;
END;
$$;

ALTER TABLE public.ticket_attachments
    ALTER COLUMN scan_status SET DEFAULT 'not_scanned';

ALTER TABLE public.ticket_attachments
    ADD CONSTRAINT ticket_attachments_scan_status_check
    CHECK (scan_status IN ('pending', 'clean', 'infected', 'failed', 'not_scanned'));

COMMENT ON COLUMN public.ticket_attachments.scan_status IS
    'Legacy compatibility/history field. New uploads are not antivirus-scanned and use not_scanned.';

COMMIT;
