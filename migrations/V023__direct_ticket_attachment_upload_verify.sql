-- V023 verification: run after V023__direct_ticket_attachment_upload.sql.

SELECT column_name, column_default, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'ticket_attachments'
  AND column_name = 'scan_status';

SELECT conname, pg_get_constraintdef(oid) AS constraint_definition
FROM pg_constraint
WHERE conrelid = 'public.ticket_attachments'::regclass
  AND contype = 'c'
  AND pg_get_constraintdef(oid) ILIKE '%scan_status%';

SELECT scan_status, COUNT(*) AS attachment_count
FROM public.ticket_attachments
GROUP BY scan_status
ORDER BY scan_status;
