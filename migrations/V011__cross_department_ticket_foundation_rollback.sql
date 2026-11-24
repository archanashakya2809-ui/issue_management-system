-- V011 rollback. Review and run only if no V011 responsibility data must be retained.
-- This removes the new table and destination column; it does not alter existing
-- ticket_assignments history or requester_department_id.

BEGIN;

DROP TRIGGER IF EXISTS trg_ticket_destination_department_default ON public.tickets;
DROP FUNCTION IF EXISTS public.set_ticket_destination_department_from_domain();

DROP TABLE IF EXISTS public.ticket_responsibilities;

ALTER TABLE public.tickets
    DROP CONSTRAINT IF EXISTS fk_tickets_destination_department_company;

DROP INDEX IF EXISTS public.idx_tickets_company_destination_department_created;

ALTER TABLE public.tickets
    DROP COLUMN IF EXISTS destination_department_id;

COMMIT;
