-- V011: Cross-department ticket foundation
-- PostgreSQL 15+ | Apply only after reviewing against the target database.
-- This migration does NOT change ticket routing functions or assignment behavior.
-- It preserves requester_department_id and derives historical destination departments
-- from each ticket's existing domain.department_id.

BEGIN;

DO $preflight$
BEGIN
    IF to_regclass('public.tickets') IS NULL
       OR to_regclass('public.departments') IS NULL
       OR to_regclass('public.domains') IS NULL
       OR to_regclass('public.users') IS NULL
       OR to_regclass('public.ticket_assignments') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_security_owner')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION 'V011 preflight failed: expected schema objects or ticket_security_owner are missing';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.tickets t
        LEFT JOIN public.domains d
          ON d.company_id = t.company_id
         AND d.id = t.domain_id
        WHERE d.id IS NULL OR d.department_id IS NULL
    ) THEN
        RAISE EXCEPTION 'V011 preflight failed: one or more existing tickets have no valid domain department';
    END IF;
END;
$preflight$;

ALTER TABLE public.tickets
    ADD COLUMN destination_department_id uuid;

-- Backward compatibility: during frontend/backend rollout, legacy API versions do
-- not send destination_department_id. Derive it from the domain only when omitted.
-- An explicitly supplied destination is never silently rewritten; V013 validates it.
GRANT USAGE ON SCHEMA public TO ticket_security_owner;
GRANT CREATE ON SCHEMA public TO ticket_security_owner;

UPDATE public.tickets t
SET destination_department_id = d.department_id,
    version = t.version + 1
FROM public.domains d
WHERE d.company_id = t.company_id
  AND d.id = t.domain_id;

-- Install the immutability/compatibility trigger only AFTER the historical
-- backfill. Otherwise the trigger correctly rejects this one-time migration
-- update as an ordinary post-creation destination change.
CREATE OR REPLACE FUNCTION public.set_ticket_destination_department_from_domain()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_department_id uuid;
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.destination_department_id IS DISTINCT FROM OLD.destination_department_id THEN
        RAISE EXCEPTION 'Ticket destination department is immutable after creation; use a dedicated move workflow'
            USING ERRCODE = '23514';
    END IF;

    SELECT d.department_id
      INTO v_department_id
      FROM public.domains d
     WHERE d.company_id = NEW.company_id
       AND d.id = NEW.domain_id;

    IF v_department_id IS NULL THEN
        RAISE EXCEPTION 'Cannot derive destination department from ticket domain'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.destination_department_id IS NULL THEN
        NEW.destination_department_id := v_department_id;
    ELSIF NEW.destination_department_id IS DISTINCT FROM v_department_id THEN
        RAISE EXCEPTION 'Ticket destination department must match the selected domain department'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$function$;

ALTER FUNCTION public.set_ticket_destination_department_from_domain()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.set_ticket_destination_department_from_domain() FROM PUBLIC;

CREATE TRIGGER trg_ticket_destination_department_default
BEFORE INSERT OR UPDATE OF domain_id, destination_department_id ON public.tickets
FOR EACH ROW
EXECUTE FUNCTION public.set_ticket_destination_department_from_domain();

ALTER TABLE public.tickets
    ALTER COLUMN destination_department_id SET NOT NULL;

ALTER TABLE public.tickets
    ADD CONSTRAINT fk_tickets_destination_department_company
    FOREIGN KEY (company_id, destination_department_id)
    REFERENCES public.departments(company_id, id)
    ON DELETE RESTRICT;

CREATE INDEX idx_tickets_company_destination_department_created
    ON public.tickets(company_id, destination_department_id, created_at DESC);

COMMENT ON COLUMN public.tickets.requester_department_id IS
    'Department the requester belongs to when the ticket is created; not the handling department.';
COMMENT ON COLUMN public.tickets.destination_department_id IS
    'Department responsible for handling the ticket; must match the selected domain department.';

-- One row per non-agent responsibility assignment event. The Support Agent remains
-- authoritative in ticket_assignments.assigned_user_id to avoid duplicate state.
CREATE TABLE public.ticket_responsibilities (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id          uuid NOT NULL,
    ticket_id           uuid NOT NULL,
    responsibility_type varchar(40) NOT NULL,
    user_id             uuid NOT NULL,
    assigned_by         uuid NOT NULL,
    assigned_at         timestamptz NOT NULL DEFAULT now(),
    unassigned_at       timestamptz,
    reason              text,

    CONSTRAINT chk_ticket_responsibility_type
        CHECK (responsibility_type IN ('TEAM_LEAD', 'DEPARTMENT_MANAGER')),
    CONSTRAINT chk_ticket_responsibility_dates
        CHECK (unassigned_at IS NULL OR unassigned_at >= assigned_at),
    CONSTRAINT fk_ticket_responsibilities_ticket_company
        FOREIGN KEY (company_id, ticket_id)
        REFERENCES public.tickets(company_id, id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ticket_responsibilities_user_company
        FOREIGN KEY (company_id, user_id)
        REFERENCES public.users(company_id, id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_ticket_responsibilities_actor_company
        FOREIGN KEY (company_id, assigned_by)
        REFERENCES public.users(company_id, id)
        ON DELETE RESTRICT
);

CREATE UNIQUE INDEX uq_ticket_responsibility_active_type
    ON public.ticket_responsibilities(ticket_id, responsibility_type)
    WHERE unassigned_at IS NULL;

CREATE INDEX idx_ticket_responsibilities_company_user_active
    ON public.ticket_responsibilities(company_id, user_id, assigned_at DESC)
    WHERE unassigned_at IS NULL;

COMMENT ON TABLE public.ticket_responsibilities IS
    'History of ticket Team Lead and Department Manager responsibilities. Support Agent remains in ticket_assignments.';

ALTER TABLE public.ticket_responsibilities ENABLE ROW LEVEL SECURITY;

CREATE POLICY ticket_responsibilities_select_policy
ON public.ticket_responsibilities
FOR SELECT
USING (public.app_ticket_access(ticket_id, 'ticket.view'));

-- No direct INSERT/UPDATE/DELETE policy or grants for ticket_app.
-- A later routing function owned by ticket_security_owner will write responsibility
-- history atomically after validating team lead and department manager configuration.

ALTER TABLE public.ticket_responsibilities OWNER TO ticket_security_owner;
GRANT SELECT ON public.ticket_responsibilities TO ticket_app;
REVOKE CREATE ON SCHEMA public FROM ticket_security_owner;

COMMIT;
