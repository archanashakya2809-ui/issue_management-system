-- ============================================================
-- V010: Generate in-app notifications for ticket activity
-- Apply with an authorized database administrator after V009.
-- Trigger functions are owned by ticket_security_owner and execute
-- with the trusted database owner's privileges (SECURITY DEFINER).
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 1. Notify active members when a ticket is assigned to a team
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_ticket_assignment()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_ticket_number bigint;
    v_subject text;
BEGIN
    SELECT t.ticket_number, t.subject
      INTO v_ticket_number, v_subject
      FROM public.tickets AS t
     WHERE t.company_id = NEW.company_id
       AND t.id = NEW.ticket_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    INSERT INTO public.notifications (
        company_id,
        ticket_id,
        recipient_user_id,
        type,
        title,
        message,
        created_by
    )
    SELECT
        NEW.company_id,
        NEW.ticket_id,
        recipients.user_id,
        'ticket_assigned',
        'New ticket assigned',
        pg_catalog.format(
            'Ticket #%s: %s has been assigned to your team.',
            v_ticket_number,
            v_subject
        ),
        NEW.assigned_by
    FROM (
        SELECT tm.user_id
          FROM public.team_members AS tm
         WHERE tm.company_id = NEW.company_id
           AND tm.team_id = NEW.assigned_team_id
           AND tm.left_at IS NULL
        UNION
        SELECT NEW.assigned_user_id
         WHERE NEW.assigned_user_id IS NOT NULL
    ) AS recipients
    JOIN public.users AS u
      ON u.company_id = NEW.company_id
     AND u.id = recipients.user_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    WHERE recipients.user_id IS DISTINCT FROM NEW.assigned_by;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_ticket_assignment
    ON public.ticket_assignments;

CREATE TRIGGER trg_notify_ticket_assignment
AFTER INSERT ON public.ticket_assignments
FOR EACH ROW
EXECUTE FUNCTION public.notify_ticket_assignment();

-- ------------------------------------------------------------
-- 2. Notify requester, current assignees, team members and watchers
--    when ticket status changes. Do not notify the actor.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_ticket_status_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_ticket_number bigint;
    v_status_code varchar(50);
BEGIN
    IF NEW.status_id IS NOT DISTINCT FROM OLD.status_id THEN
        RETURN NEW;
    END IF;

    SELECT t.ticket_number, s.code
      INTO v_ticket_number, v_status_code
      FROM public.tickets AS t
      JOIN public.ticket_statuses AS s
        ON s.company_id = t.company_id
       AND s.id = NEW.status_id
     WHERE t.company_id = NEW.company_id
       AND t.id = NEW.id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    INSERT INTO public.notifications (
        company_id,
        ticket_id,
        recipient_user_id,
        type,
        title,
        message,
        created_by
    )
    SELECT
        NEW.company_id,
        NEW.id,
        recipients.user_id,
        'ticket_status_changed',
        'Ticket status updated',
        pg_catalog.format(
            'Ticket #%s has moved to %s.',
            v_ticket_number,
            v_status_code
        ),
        public.app_current_user_id()
    FROM (
        SELECT NEW.requester_id AS user_id
        UNION
        SELECT ta.assigned_user_id
          FROM public.ticket_assignments AS ta
         WHERE ta.company_id = NEW.company_id
           AND ta.ticket_id = NEW.id
           AND ta.unassigned_at IS NULL
           AND ta.assigned_user_id IS NOT NULL
        UNION
        SELECT tm.user_id
          FROM public.ticket_assignments AS ta
          JOIN public.team_members AS tm
            ON tm.company_id = ta.company_id
           AND tm.team_id = ta.assigned_team_id
           AND tm.left_at IS NULL
         WHERE ta.company_id = NEW.company_id
           AND ta.ticket_id = NEW.id
           AND ta.unassigned_at IS NULL
        UNION
        SELECT tw.user_id
          FROM public.ticket_watchers AS tw
         WHERE tw.company_id = NEW.company_id
           AND tw.ticket_id = NEW.id
    ) AS recipients
    JOIN public.users AS u
      ON u.company_id = NEW.company_id
     AND u.id = recipients.user_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    WHERE recipients.user_id IS NOT NULL
      AND recipients.user_id IS DISTINCT FROM public.app_current_user_id();

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_ticket_status_change
    ON public.tickets;

CREATE TRIGGER trg_notify_ticket_status_change
AFTER UPDATE OF status_id ON public.tickets
FOR EACH ROW
WHEN (OLD.status_id IS DISTINCT FROM NEW.status_id)
EXECUTE FUNCTION public.notify_ticket_status_change();

-- ------------------------------------------------------------
-- 3. Notify requester/assignees/team/watchers about comments.
--    Internal notes are never sent to the requester or general watchers.
--    The comment body itself is not copied into the notification.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_ticket_comment()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_ticket_number bigint;
    v_title varchar(255);
    v_message text;
BEGIN
    SELECT t.ticket_number
      INTO v_ticket_number
      FROM public.tickets AS t
     WHERE t.company_id = NEW.company_id
       AND t.id = NEW.ticket_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    IF NEW.is_internal THEN
        v_title := 'Internal ticket note added';
        v_message := pg_catalog.format(
            'An internal note was added to Ticket #%s.',
            v_ticket_number
        );
    ELSE
        v_title := 'New ticket comment';
        v_message := pg_catalog.format(
            'A new comment was added to Ticket #%s.',
            v_ticket_number
        );
    END IF;

    INSERT INTO public.notifications (
        company_id,
        ticket_id,
        recipient_user_id,
        type,
        title,
        message,
        created_by
    )
    SELECT
        NEW.company_id,
        NEW.ticket_id,
        recipients.user_id,
        CASE
            WHEN NEW.is_internal THEN 'ticket_internal_note'
            ELSE 'ticket_comment'
        END,
        v_title,
        v_message,
        NEW.user_id
    FROM (
        SELECT ta.assigned_user_id AS user_id
          FROM public.ticket_assignments AS ta
         WHERE ta.company_id = NEW.company_id
           AND ta.ticket_id = NEW.ticket_id
           AND ta.unassigned_at IS NULL
           AND ta.assigned_user_id IS NOT NULL
        UNION
        SELECT tm.user_id
          FROM public.ticket_assignments AS ta
          JOIN public.team_members AS tm
            ON tm.company_id = ta.company_id
           AND tm.team_id = ta.assigned_team_id
           AND tm.left_at IS NULL
         WHERE ta.company_id = NEW.company_id
           AND ta.ticket_id = NEW.ticket_id
           AND ta.unassigned_at IS NULL
        UNION
        SELECT t.requester_id
          FROM public.tickets AS t
         WHERE NOT NEW.is_internal
           AND t.company_id = NEW.company_id
           AND t.id = NEW.ticket_id
        UNION
        SELECT tw.user_id
          FROM public.ticket_watchers AS tw
         WHERE NOT NEW.is_internal
           AND tw.company_id = NEW.company_id
           AND tw.ticket_id = NEW.ticket_id
    ) AS recipients
    JOIN public.users AS u
      ON u.company_id = NEW.company_id
     AND u.id = recipients.user_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    WHERE recipients.user_id IS NOT NULL
      AND recipients.user_id IS DISTINCT FROM NEW.user_id;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_ticket_comment
    ON public.ticket_comments;

CREATE TRIGGER trg_notify_ticket_comment
AFTER INSERT ON public.ticket_comments
FOR EACH ROW
EXECUTE FUNCTION public.notify_ticket_comment();

-- The documented deployment model uses this NOLOGIN owner for trusted
-- SECURITY DEFINER functions. This also keeps ticket_app from owning them.
DO $block$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_roles
         WHERE rolname = 'ticket_security_owner'
    ) THEN
        RAISE EXCEPTION
            'Required role ticket_security_owner does not exist; create the documented NOLOGIN security owner before V010';
    END IF;
END;
$block$;

ALTER FUNCTION public.notify_ticket_assignment()
    OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_ticket_status_change()
    OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_ticket_comment()
    OWNER TO ticket_security_owner;

-- Trigger execution is performed by the application DML statements. Keep the
-- trigger functions unavailable to PUBLIC while allowing the app role to fire
-- the attached triggers. Direct invocation is not useful because they return trigger.
REVOKE ALL ON FUNCTION public.notify_ticket_assignment() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_ticket_status_change() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_ticket_comment() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_ticket_assignment() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_ticket_status_change() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_ticket_comment() TO ticket_app;

COMMENT ON FUNCTION public.notify_ticket_assignment() IS
'Creates in-app notifications for active members of the assigned team and individual assignee.';
COMMENT ON FUNCTION public.notify_ticket_status_change() IS
'Creates status-change notifications for requester, current assignees, team members and watchers, excluding the actor.';
COMMENT ON FUNCTION public.notify_ticket_comment() IS
'Creates comment notifications; internal-note events are limited to agents/teams and never sent to the requester or general watchers.';

COMMIT;
