-- V023 rollback: restores the previous scan_status values and default.
-- This does not reintroduce ClamAV application code or restore the download gate.

BEGIN;

UPDATE public.ticket_attachments
SET scan_status = 'pending'
WHERE scan_status = 'not_scanned';

ALTER TABLE public.ticket_attachments
    ALTER COLUMN scan_status SET DEFAULT 'pending';

ALTER TABLE public.ticket_attachments
    DROP CONSTRAINT IF EXISTS ticket_attachments_scan_status_check;

ALTER TABLE public.ticket_attachments
    ADD CONSTRAINT ticket_attachments_scan_status_check
    CHECK (scan_status IN ('pending', 'clean', 'infected', 'failed'));

COMMENT ON COLUMN public.ticket_attachments.scan_status IS
    'Attachment malware scan status.';

COMMIT;
