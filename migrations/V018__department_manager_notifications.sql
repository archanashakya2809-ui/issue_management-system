-- ============================================================
-- V018: Department Manager operational notifications
--
-- Extends the existing notifications table and V010 event system.
-- Does not introduce automatic ticket assignment or alter claims.
--
-- Events:
--   * HIGH / URGENT ticket created or upgraded
--   * Ticket escalated
--   * Ticket reopened
--   * Resolution SLA due within 4 hours
--   * Resolution SLA breached
--
-- Recipients are active DEPARTMENT_MANAGER role assignments scoped
-- to the ticket's destination department and holding ticket.view.
-- ============================================================

BEGIN;

DO $preflight$
BEGIN
    IF to_regclass('public.notifications') IS NULL
       OR to_regclass('public.tickets') IS NULL
       OR to_regclass('public.ticket_priorities') IS NULL
       OR to_regclass('public.ticket_statuses') IS NULL
       OR to_regclass('public.ticket_sla') IS NULL
       OR to_regclass('public.user_role_assignments') IS NULL
       OR to_regclass('public.role_permissions') IS NULL
       OR to_regclass('public.permissions') IS NULL
       OR to_regclass('public.roles') IS NULL
       OR to_regclass('public.users') IS NULL THEN
        RAISE EXCEPTION
            'V018 preflight failed: one or more required notification, ticket, SLA, role, or permission tables are missing.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'ticket_security_owner'
    ) THEN
        RAISE EXCEPTION
            'V018 requires the existing NOLOGIN role ticket_security_owner.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles
        WHERE rolname = 'ticket_app'
    ) THEN
        RAISE EXCEPTION
            'V018 requires the existing application role ticket_app.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tickets'
          AND column_name = 'destination_department_id'
    ) THEN
        RAISE EXCEPTION
            'V018 requires tickets.destination_department_id from the cross-department ticket foundation.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_sla'
          AND column_name = 'resolution_due_at'
    ) THEN
        RAISE EXCEPTION
            'V018 requires ticket_sla.resolution_due_at.';
    END IF;
END;
$preflight$;

-- Stable event keys make trigger retries and repeated SLA scans idempotent.
ALTER TABLE public.notifications
    ADD COLUMN IF NOT EXISTS event_key text;

CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_company_recipient_event_key
    ON public.notifications(company_id, recipient_user_id, event_key)
    WHERE event_key IS NOT NULL;

-- Support the bounded resolution-deadline scan.
CREATE INDEX IF NOT EXISTS idx_ticket_sla_resolution_due_at
    ON public.ticket_sla(resolution_due_at, ticket_id)
    WHERE resolution_due_at IS NOT NULL;

GRANT SELECT ON TABLE public.ticket_sla TO ticket_security_owner;

-- ------------------------------------------------------------
-- Shared recipient selector is expressed inside each trusted function:
-- active DEPARTMENT_MANAGER role assignment, department scope, active user,
-- and that assigned role itself carries ticket.view.
-- ------------------------------------------------------------

-- ------------------------------------------------------------
-- 1. Notify Department Managers when a HIGH / URGENT ticket is created
--    or its priority is upgraded into / within the urgent band.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_department_manager_priority_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_new_priority_name text;
    v_old_priority_name text;
    v_should_notify boolean := false;
    v_actor_id uuid;
    v_event_key text;
BEGIN
    SELECT upper(pg_catalog.btrim(p.name))
      INTO v_new_priority_name
      FROM public.ticket_priorities AS p
     WHERE p.company_id = NEW.company_id
       AND p.id = NEW.priority_id
       AND p.is_active = true;

    IF v_new_priority_name NOT IN ('HIGH', 'URGENT') THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'INSERT' THEN
        v_should_notify := true;
    ELSIF TG_OP = 'UPDATE' THEN
        SELECT upper(pg_catalog.btrim(p.name))
          INTO v_old_priority_name
          FROM public.ticket_priorities AS p
         WHERE p.company_id = OLD.company_id
           AND p.id = OLD.priority_id;

        v_should_notify :=
            v_old_priority_name IS DISTINCT FROM v_new_priority_name
            AND (
                v_old_priority_name IS NULL
                OR v_old_priority_name NOT IN ('HIGH', 'URGENT')
                OR (v_old_priority_name = 'HIGH' AND v_new_priority_name = 'URGENT')
            );
    END IF;

    IF NOT v_should_notify THEN
        RETURN NEW;
    END IF;

    v_actor_id := public.app_current_user_id();
    v_event_key := pg_catalog.format(
        'department-ticket:high-priority:%s:%s',
        NEW.id,
        COALESCE(NEW.version, 1)
    );

    INSERT INTO public.notifications (
        company_id,
        ticket_id,
        recipient_user_id,
        type,
        title,
        message,
        created_by,
        event_key
    )
    SELECT DISTINCT
        NEW.company_id,
        NEW.id,
        u.id,
        'department_ticket_high_priority',
        CASE
            WHEN v_new_priority_name = 'URGENT' THEN 'Urgent department ticket received'
            ELSE 'High-priority department ticket received'
        END,
        pg_catalog.format(
            'Ticket #%s: %s was created or upgraded to %s priority.',
            NEW.ticket_number,
            NEW.subject,
            v_new_priority_name
        ),
        v_actor_id,
        v_event_key
    FROM public.user_role_assignments AS ura
    JOIN public.roles AS r
      ON r.id = ura.role_id
     AND r.is_active = true
     AND r.code = 'DEPARTMENT_MANAGER'
    JOIN public.role_permissions AS rp
      ON rp.role_id = r.id
    JOIN public.permissions AS perm
      ON perm.id = rp.permission_id
     AND perm.code = 'ticket.view'
     AND perm.is_active = true
    JOIN public.users AS u
      ON u.company_id = ura.company_id
     AND u.id = ura.user_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    WHERE ura.company_id = NEW.company_id
      AND ura.scope_type = 'department'
      AND ura.scope_department_id = NEW.destination_department_id
      AND ura.revoked_at IS NULL
      AND u.id IS DISTINCT FROM v_actor_id
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_department_manager_priority_insert
    ON public.tickets;
CREATE TRIGGER trg_notify_department_manager_priority_insert
AFTER INSERT ON public.tickets
FOR EACH ROW
EXECUTE FUNCTION public.notify_department_manager_priority_event();

DROP TRIGGER IF EXISTS trg_notify_department_manager_priority_update
    ON public.tickets;
CREATE TRIGGER trg_notify_department_manager_priority_update
AFTER UPDATE OF priority_id ON public.tickets
FOR EACH ROW
WHEN (OLD.priority_id IS DISTINCT FROM NEW.priority_id)
EXECUTE FUNCTION public.notify_department_manager_priority_event();

-- ------------------------------------------------------------
-- 2. Notify Department Managers when a ticket is escalated or reopened.
--    Reopen is detected when a previously terminal ticket moves back into
--    an active workflow status (including an explicit REOPENED status).
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_department_manager_ticket_status_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_old_status_code varchar(50);
    v_new_status_code varchar(50);
    v_event_type varchar(50);
    v_title varchar(255);
    v_message text;
    v_actor_id uuid;
    v_event_key text;
BEGIN
    IF NEW.status_id IS NOT DISTINCT FROM OLD.status_id THEN
        RETURN NEW;
    END IF;

    SELECT s.code
      INTO v_old_status_code
      FROM public.ticket_statuses AS s
     WHERE s.company_id = OLD.company_id
       AND s.id = OLD.status_id;

    SELECT s.code
      INTO v_new_status_code
      FROM public.ticket_statuses AS s
     WHERE s.company_id = NEW.company_id
       AND s.id = NEW.status_id;

    IF v_new_status_code = 'ESCALATED' THEN
        v_event_type := 'department_ticket_escalated';
        v_title := 'Department ticket escalated';
        v_message := pg_catalog.format(
            'Ticket #%s: %s has been escalated and requires supervisory attention.',
            NEW.ticket_number,
            NEW.subject
        );
    ELSIF v_old_status_code IN ('RESOLVED', 'CLOSED')
       AND v_new_status_code IN ('OPEN', 'ASSIGNED', 'IN_PROGRESS', 'REOPENED') THEN
        v_event_type := 'department_ticket_reopened';
        v_title := 'Department ticket reopened';
        v_message := pg_catalog.format(
            'Ticket #%s: %s has been reopened.',
            NEW.ticket_number,
            NEW.subject
        );
    ELSE
        RETURN NEW;
    END IF;

    v_actor_id := public.app_current_user_id();
    v_event_key := pg_catalog.format(
        'department-ticket:%s:%s:%s',
        NEW.id,
        v_event_type,
        COALESCE(NEW.version, 1)
    );

    INSERT INTO public.notifications (
        company_id,
        ticket_id,
        recipient_user_id,
        type,
        title,
        message,
        created_by,
        event_key
    )
    SELECT DISTINCT
        NEW.company_id,
        NEW.id,
        u.id,
        v_event_type,
        v_title,
        v_message,
        v_actor_id,
        v_event_key
    FROM public.user_role_assignments AS ura
    JOIN public.roles AS r
      ON r.id = ura.role_id
     AND r.is_active = true
     AND r.code = 'DEPARTMENT_MANAGER'
    JOIN public.role_permissions AS rp
      ON rp.role_id = r.id
    JOIN public.permissions AS perm
      ON perm.id = rp.permission_id
     AND perm.code = 'ticket.view'
     AND perm.is_active = true
    JOIN public.users AS u
      ON u.company_id = ura.company_id
     AND u.id = ura.user_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    WHERE ura.company_id = NEW.company_id
      AND ura.scope_type = 'department'
      AND ura.scope_department_id = NEW.destination_department_id
      AND ura.revoked_at IS NULL
      AND u.id IS DISTINCT FROM v_actor_id
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_department_manager_ticket_status
    ON public.tickets;
CREATE TRIGGER trg_notify_department_manager_ticket_status
AFTER UPDATE OF status_id ON public.tickets
FOR EACH ROW
WHEN (OLD.status_id IS DISTINCT FROM NEW.status_id)
EXECUTE FUNCTION public.notify_department_manager_ticket_status_event();

-- ------------------------------------------------------------
-- 3. SLA evaluator for the authenticated Department Manager.
--    It evaluates only the caller's active department scopes. The endpoint
--    invokes it when notifications are fetched; the UI polls while a manager
--    is signed in. Dedupe keys prevent repeated alerts for the same deadline.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.emit_department_manager_sla_notifications()
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_user_id uuid;
    v_company_id uuid;
    v_inserted integer := 0;
BEGIN
    v_user_id := public.app_current_user_id();
    v_company_id := public.app_current_user_company_id();

    IF v_user_id IS NULL OR v_company_id IS NULL THEN
        RETURN 0;
    END IF;

    WITH manager_departments AS MATERIALIZED (
        SELECT DISTINCT
            ura.company_id,
            ura.scope_department_id AS department_id,
            ura.user_id
        FROM public.user_role_assignments AS ura
        JOIN public.roles AS r
          ON r.id = ura.role_id
         AND r.code = 'DEPARTMENT_MANAGER'
         AND r.is_active = true
        JOIN public.role_permissions AS rp
          ON rp.role_id = r.id
        JOIN public.permissions AS perm
          ON perm.id = rp.permission_id
         AND perm.code = 'ticket.view'
         AND perm.is_active = true
        JOIN public.users AS u
          ON u.company_id = ura.company_id
         AND u.id = ura.user_id
         AND u.status = 'active'
         AND u.deleted_at IS NULL
        WHERE ura.user_id = v_user_id
          AND ura.company_id = v_company_id
          AND ura.scope_type = 'department'
          AND ura.scope_department_id IS NOT NULL
          AND ura.revoked_at IS NULL
    ),
    sla_candidates AS (
        SELECT
            md.company_id,
            md.user_id AS recipient_user_id,
            t.id AS ticket_id,
            t.ticket_number,
            t.subject,
            tsl.resolution_due_at,
            CASE
                WHEN tsl.resolution_due_at < now()
                    THEN 'department_ticket_sla_breached'
                ELSE 'department_ticket_sla_due_soon'
            END AS notification_type
        FROM manager_departments AS md
        JOIN public.tickets AS t
          ON t.company_id = md.company_id
         AND t.destination_department_id = md.department_id
        JOIN public.ticket_statuses AS s
          ON s.company_id = t.company_id
         AND s.id = t.status_id
        JOIN public.ticket_sla AS tsl
          ON tsl.ticket_id = t.id
        WHERE s.code NOT IN ('RESOLVED', 'CLOSED', 'CANCELLED')
          AND tsl.resolution_due_at IS NOT NULL
          AND tsl.resolution_due_at <= now() + interval '4 hours'
    ),
    inserted_rows AS (
        INSERT INTO public.notifications (
            company_id,
            ticket_id,
            recipient_user_id,
            type,
            title,
            message,
            created_by,
            event_key
        )
        SELECT
            c.company_id,
            c.ticket_id,
            c.recipient_user_id,
            c.notification_type,
            CASE
                WHEN c.notification_type = 'department_ticket_sla_breached'
                    THEN 'Department ticket SLA breached'
                ELSE 'Department ticket SLA due soon'
            END,
            CASE
                WHEN c.notification_type = 'department_ticket_sla_breached'
                    THEN pg_catalog.format(
                        'Ticket #%s: %s has passed its resolution deadline.',
                        c.ticket_number,
                        c.subject
                    )
                ELSE pg_catalog.format(
                    'Ticket #%s: %s is due for resolution within 4 hours.',
                    c.ticket_number,
                    c.subject
                )
            END,
            NULL,
            pg_catalog.md5(pg_catalog.concat_ws(
                '|',
                c.notification_type,
                c.ticket_id::text,
                c.recipient_user_id::text,
                extract(epoch FROM c.resolution_due_at)::text
            ))
        FROM sla_candidates AS c
        ON CONFLICT (company_id, recipient_user_id, event_key)
            WHERE event_key IS NOT NULL
        DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::integer
      INTO v_inserted
      FROM inserted_rows;

    RETURN COALESCE(v_inserted, 0);
END;
$function$;

-- Assign the trusted, non-login owner to every SECURITY DEFINER function.
ALTER FUNCTION public.notify_department_manager_priority_event()
    OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_department_manager_ticket_status_event()
    OWNER TO ticket_security_owner;
ALTER FUNCTION public.emit_department_manager_sla_notifications()
    OWNER TO ticket_security_owner;

REVOKE ALL ON FUNCTION public.notify_department_manager_priority_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_department_manager_ticket_status_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.emit_department_manager_sla_notifications() FROM PUBLIC;

-- Trigger functions are attached to trusted ticket DML. The app role can fire
-- those triggers; it cannot call the SLA evaluator directly through PUBLIC.
GRANT EXECUTE ON FUNCTION public.notify_department_manager_priority_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_department_manager_ticket_status_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.emit_department_manager_sla_notifications() TO ticket_app;

COMMENT ON FUNCTION public.notify_department_manager_priority_event() IS
'Notifies active Department Managers scoped to the ticket destination department when a ticket enters HIGH or URGENT priority. It does not assign tickets.';
COMMENT ON FUNCTION public.notify_department_manager_ticket_status_event() IS
'Notifies active Department Managers scoped to the ticket destination department on escalation and reopening; it grants no ticket action permissions.';
COMMENT ON FUNCTION public.emit_department_manager_sla_notifications() IS
'Idempotently materializes due-soon and breached resolution-SLA notifications for the authenticated Department Manager''s own department scopes.';

COMMIT;
