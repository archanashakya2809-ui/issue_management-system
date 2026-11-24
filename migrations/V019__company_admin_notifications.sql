-- ============================================================
-- V019: Company Admin operational and administration notifications
--
-- Extends the V018 notification event model. Reuses public.notifications,
-- event_key idempotency, existing per-user read state, and existing RLS.
-- Does not create a second notification table.
--
-- Events:
--   * HIGH / URGENT ticket created or upgraded
--   * Ticket escalated / reopened
--   * No eligible Support Agent for a ticket
--   * Resolution SLA due within 4 hours / breached
--   * Unclaimed department-queue ticket older than 4 hours
--   * User created / deactivated / reactivated
--   * User role assignment granted / revoked
--   * Department / team / domain created or materially updated
--   * Company settings changed
--
-- Recipients:
--   Active COMPANY_ADMIN role assignments scoped to the same tenant company,
--   with active ticket.view permission on that role. Actor is excluded.
--
-- This migration does not modify ticket claim or resolution authorization.
-- ============================================================

BEGIN;

DO $preflight$
BEGIN
    IF to_regclass('public.notifications') IS NULL
       OR to_regclass('public.tickets') IS NULL
       OR to_regclass('public.ticket_priorities') IS NULL
       OR to_regclass('public.ticket_statuses') IS NULL
       OR to_regclass('public.ticket_sla') IS NULL
       OR to_regclass('public.ticket_department_queue') IS NULL
       OR to_regclass('public.ticket_claims') IS NULL
       OR to_regclass('public.user_role_assignments') IS NULL
       OR to_regclass('public.role_permissions') IS NULL
       OR to_regclass('public.permissions') IS NULL
       OR to_regclass('public.roles') IS NULL
       OR to_regclass('public.users') IS NULL
       OR to_regclass('public.departments') IS NULL
       OR to_regclass('public.teams') IS NULL
       OR to_regclass('public.domains') IS NULL
       OR to_regclass('public.company_settings') IS NULL
       OR to_regclass('public.companies') IS NULL
       OR to_regclass('public.audit_logs') IS NULL THEN
        RAISE EXCEPTION
            'V019 preflight failed: a required notification, ticket, queue, claim, organization, settings, role, or permission table is missing. Apply the approved prerequisites first.';
    END IF;

    IF to_regprocedure('public.app_current_user_id()') IS NULL
       OR to_regprocedure('public.app_current_user_company_id()') IS NULL
       OR to_regprocedure('public.app_is_eligible_department_agent(uuid,uuid)') IS NULL THEN
        RAISE EXCEPTION
            'V019 preflight failed: required identity or Support Agent eligibility function is missing.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_security_owner')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION
            'V019 requires existing roles ticket_security_owner and ticket_app.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.roles
        WHERE code = 'COMPANY_ADMIN' AND is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'V019 prerequisite missing: active COMPANY_ADMIN role.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.roles r
        JOIN public.role_permissions rp ON rp.role_id = r.id
        JOIN public.permissions p ON p.id = rp.permission_id
        WHERE r.code = 'COMPANY_ADMIN'
          AND r.is_active = TRUE
          AND p.code = 'ticket.view'
          AND p.is_active = TRUE
    ) THEN
        RAISE EXCEPTION
            'V019 prerequisite missing: COMPANY_ADMIN must have active ticket.view permission.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'notifications'
          AND column_name = 'event_key'
    ) THEN
        RAISE EXCEPTION
            'V019 prerequisite missing: notifications.event_key from V018.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'notifications'
          AND indexname = 'uq_notifications_company_recipient_event_key'
    ) THEN
        RAISE EXCEPTION
            'V019 prerequisite missing: V018 unique event-key index.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tickets'
          AND column_name = 'version'
    ) THEN
        RAISE EXCEPTION 'V019 prerequisite missing: tickets.version.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ticket_priorities'
          AND column_name IN ('name', 'is_active')
        GROUP BY table_name
        HAVING count(DISTINCT column_name) = 2
    ) THEN
        RAISE EXCEPTION 'V019 prerequisite missing: ticket_priorities.name/is_active.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'company_settings'
          AND column_name IN (
              'company_id', 'display_name', 'timezone',
              'default_sla_hours', 'reply_to', 'notify_company_admins'
          )
    ) <> 6 THEN
        RAISE EXCEPTION 'V019 prerequisite missing: required company_settings columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_claims'
          AND column_name IN ('company_id', 'ticket_id', 'released_at')
    ) <> 3 THEN
        RAISE EXCEPTION 'V019 prerequisite missing: ticket_claims company/ticket/released columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_sla'
          AND column_name IN ('ticket_id', 'resolution_due_at')
    ) <> 2 THEN
        RAISE EXCEPTION 'V019 prerequisite missing: ticket_sla ticket_id/resolution_due_at.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_department_queue'
          AND column_name = 'queue_status'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_department_queue'
          AND column_name = 'created_at'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_department_queue'
          AND column_name = 'company_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_department_queue'
          AND column_name = 'ticket_id'
    ) THEN
        RAISE EXCEPTION 'V019 prerequisite missing: ticket_department_queue.queue_status/created_at/company_id/ticket_id.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tickets'
          AND column_name = 'destination_department_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tickets'
          AND column_name = 'domain_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tickets'
          AND column_name = 'ticket_number'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tickets'
          AND column_name = 'subject'
    ) THEN
        RAISE EXCEPTION 'V019 prerequisite missing: required ticket routing/description columns.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'user_role_assignments'
          AND column_name = 'scope_type'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'user_role_assignments'
          AND column_name = 'scope_department_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'user_role_assignments'
          AND column_name = 'scope_team_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'user_role_assignments'
          AND column_name = 'revoked_at'
    ) THEN
        RAISE EXCEPTION 'V019 prerequisite missing: user_role_assignments scope columns.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs'
          AND column_name = 'id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs'
          AND column_name = 'company_id'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs'
          AND column_name = 'action'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs'
          AND column_name = 'entity_type'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'audit_logs'
          AND column_name = 'entity_id'
    ) THEN
        RAISE EXCEPTION 'V019 prerequisite missing: audit_logs action/entity_type/entity_id.';
    END IF;
END;
$preflight$;

-- Generic entity references allow notifications to deep-link to company
-- resources without assuming that every notification belongs to a ticket.
ALTER TABLE public.notifications
    ADD COLUMN IF NOT EXISTS entity_type varchar(40),
    ADD COLUMN IF NOT EXISTS entity_id uuid;

CREATE INDEX IF NOT EXISTS idx_notifications_company_admin_entity
    ON public.notifications(company_id, recipient_user_id, entity_type, entity_id)
    WHERE entity_id IS NOT NULL;

-- The eligibility helper is used only from trusted SECURITY DEFINER triggers.
GRANT EXECUTE ON FUNCTION public.app_is_eligible_department_agent(uuid, uuid)
    TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_current_user_id()
    TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_current_user_company_id()
    TO ticket_security_owner;

GRANT SELECT ON TABLE
    public.roles,
    public.permissions,
    public.role_permissions,
    public.user_role_assignments,
    public.users,
    public.companies,
    public.departments,
    public.teams,
    public.domains,
    public.company_settings,
    public.ticket_priorities,
    public.ticket_statuses,
    public.ticket_sla,
    public.ticket_department_queue,
    public.ticket_claims
TO ticket_security_owner;
GRANT INSERT ON TABLE public.notifications TO ticket_security_owner;

-- Efficiently find long-waiting OPEN queue rows during the notification feed poll.
CREATE INDEX IF NOT EXISTS idx_ticket_department_queue_company_open_created
    ON public.ticket_department_queue(company_id, created_at)
    WHERE queue_status = 'OPEN';

-- ------------------------------------------------------------
-- Shared recipient resolver. It returns only active Company Admins in the
-- supplied tenant who have a company-scoped role assignment and ticket.view.
-- It is not executable by PUBLIC or ticket_app.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.company_admin_notification_recipients(
    p_company_id uuid,
    p_exclude_user_id uuid DEFAULT NULL
)
RETURNS TABLE(recipient_user_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
    SELECT DISTINCT ura.user_id
    FROM public.user_role_assignments AS ura
    JOIN public.roles AS r
      ON r.id = ura.role_id
     AND r.code = 'COMPANY_ADMIN'
     AND r.is_active = TRUE
    JOIN public.role_permissions AS rp
      ON rp.role_id = r.id
    JOIN public.permissions AS perm
      ON perm.id = rp.permission_id
     AND perm.code = 'ticket.view'
     AND perm.is_active = TRUE
    JOIN public.users AS u
      ON u.id = ura.user_id
     AND u.company_id = ura.company_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    WHERE ura.company_id = p_company_id
      AND ura.scope_type = 'company'
      AND ura.scope_department_id IS NULL
      AND ura.scope_team_id IS NULL
      AND ura.revoked_at IS NULL
      AND ura.user_id IS DISTINCT FROM p_exclude_user_id
      AND EXISTS (
          SELECT 1
          FROM public.companies AS c
          WHERE c.id = p_company_id
            AND c.is_platform = FALSE
      )
      AND COALESCE((
          SELECT cs.notify_company_admins
          FROM public.company_settings AS cs
          WHERE cs.company_id = p_company_id
      ), TRUE) IS TRUE;
$function$;

ALTER FUNCTION public.company_admin_notification_recipients(uuid, uuid)
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.company_admin_notification_recipients(uuid, uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.company_admin_notification_recipients(uuid, uuid)
    TO ticket_security_owner;

-- Return the current actor only when the actor belongs to the event tenant.
-- A platform System Admin creating/updating tenant resources is not a tenant user.
CREATE OR REPLACE FUNCTION public.company_admin_notification_actor(
    p_company_id uuid
)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
    SELECT u.id
    FROM public.users AS u
    WHERE u.id = public.app_current_user_id()
      AND u.company_id = p_company_id
    LIMIT 1;
$function$;

ALTER FUNCTION public.company_admin_notification_actor(uuid)
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.company_admin_notification_actor(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.company_admin_notification_actor(uuid)
    TO ticket_security_owner;

-- ------------------------------------------------------------
-- 1. HIGH / URGENT ticket notification.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_priority_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_new_priority text;
    v_old_priority text;
    v_notify boolean := FALSE;
    v_actor_id uuid;
    v_event_key text;
BEGIN
    SELECT upper(pg_catalog.btrim(p.name))
      INTO v_new_priority
      FROM public.ticket_priorities AS p
     WHERE p.company_id = NEW.company_id
       AND p.id = NEW.priority_id
       AND p.is_active = TRUE;

    IF COALESCE(v_new_priority NOT IN ('HIGH', 'URGENT'), TRUE) THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'INSERT' THEN
        v_notify := TRUE;
    ELSE
        SELECT upper(pg_catalog.btrim(p.name))
          INTO v_old_priority
          FROM public.ticket_priorities AS p
         WHERE p.company_id = OLD.company_id
           AND p.id = OLD.priority_id;

        v_notify := v_old_priority IS DISTINCT FROM v_new_priority
            AND (
                v_old_priority IS NULL
                OR v_old_priority NOT IN ('HIGH', 'URGENT')
                OR (v_old_priority = 'HIGH' AND v_new_priority = 'URGENT')
            );
    END IF;

    IF NOT v_notify THEN
        RETURN NEW;
    END IF;

    v_actor_id := public.company_admin_notification_actor(NEW.company_id);
    v_event_key := pg_catalog.format(
        'company-ticket:priority:%s:%s', NEW.id, COALESCE(NEW.version, 1)
    );

    INSERT INTO public.notifications (
        company_id, ticket_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        NEW.company_id,
        NEW.id,
        recipient.recipient_user_id,
        'company_ticket_high_priority',
        CASE WHEN v_new_priority = 'URGENT'
             THEN 'Urgent ticket received'
             ELSE 'High-priority ticket received' END,
        pg_catalog.format(
            'Ticket #%s: %s was created or upgraded to %s priority.',
            NEW.ticket_number, NEW.subject, v_new_priority
        ),
        v_actor_id,
        v_event_key,
        'ticket',
        NEW.id
    FROM public.company_admin_notification_recipients(NEW.company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_priority_insert ON public.tickets;
CREATE TRIGGER trg_notify_company_admin_priority_insert
AFTER INSERT ON public.tickets
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_priority_event();

DROP TRIGGER IF EXISTS trg_notify_company_admin_priority_update ON public.tickets;
CREATE TRIGGER trg_notify_company_admin_priority_update
AFTER UPDATE OF priority_id ON public.tickets
FOR EACH ROW
WHEN (OLD.priority_id IS DISTINCT FROM NEW.priority_id)
EXECUTE FUNCTION public.notify_company_admin_priority_event();

-- ------------------------------------------------------------
-- 2. Escalation and reopening notifications.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_ticket_status_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_old_status varchar(50);
    v_new_status varchar(50);
    v_type varchar(50);
    v_title varchar(255);
    v_message text;
    v_actor_id uuid;
    v_event_key text;
BEGIN
    IF NEW.status_id IS NOT DISTINCT FROM OLD.status_id THEN
        RETURN NEW;
    END IF;

    SELECT s.code INTO v_old_status
    FROM public.ticket_statuses AS s
    WHERE s.company_id = OLD.company_id AND s.id = OLD.status_id;

    SELECT s.code INTO v_new_status
    FROM public.ticket_statuses AS s
    WHERE s.company_id = NEW.company_id AND s.id = NEW.status_id;

    IF v_new_status = 'ESCALATED' THEN
        v_type := 'company_ticket_escalated';
        v_title := 'Company ticket escalated';
        v_message := pg_catalog.format(
            'Ticket #%s: %s has been escalated and requires company-level attention.',
            NEW.ticket_number, NEW.subject
        );
    ELSIF v_old_status IN ('RESOLVED', 'CLOSED')
       AND v_new_status IN ('OPEN', 'ASSIGNED', 'IN_PROGRESS', 'REOPENED') THEN
        v_type := 'company_ticket_reopened';
        v_title := 'Company ticket reopened';
        v_message := pg_catalog.format(
            'Ticket #%s: %s has been reopened.', NEW.ticket_number, NEW.subject
        );
    ELSE
        RETURN NEW;
    END IF;

    v_actor_id := public.company_admin_notification_actor(NEW.company_id);
    v_event_key := pg_catalog.format(
        'company-ticket:%s:%s:%s', NEW.id, v_type, COALESCE(NEW.version, 1)
    );

    INSERT INTO public.notifications (
        company_id, ticket_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        NEW.company_id,
        NEW.id,
        recipient.recipient_user_id,
        v_type,
        v_title,
        v_message,
        v_actor_id,
        v_event_key,
        'ticket',
        NEW.id
    FROM public.company_admin_notification_recipients(NEW.company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_ticket_status ON public.tickets;
CREATE TRIGGER trg_notify_company_admin_ticket_status
AFTER UPDATE OF status_id ON public.tickets
FOR EACH ROW
WHEN (OLD.status_id IS DISTINCT FROM NEW.status_id)
EXECUTE FUNCTION public.notify_company_admin_ticket_status_event();

-- ------------------------------------------------------------
-- 3. Notify Company Admins when no eligible Support Agent can handle a ticket.
--    Eligibility is delegated to the existing approved helper; this does not
--    assign a ticket, create a claim, or change queue ownership.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_no_eligible_agent()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_status_code varchar(50);
    v_actor_id uuid;
    v_event_key text;
BEGIN
    IF NEW.destination_department_id IS NULL OR NEW.domain_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT s.code INTO v_status_code
    FROM public.ticket_statuses AS s
    WHERE s.company_id = NEW.company_id AND s.id = NEW.status_id;

    IF v_status_code IS NULL OR v_status_code IN ('DRAFT', 'RESOLVED', 'CLOSED', 'CANCELLED') THEN
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.user_role_assignments AS ura
        JOIN public.roles AS r
          ON r.id = ura.role_id
         AND r.code = 'SUPPORT_AGENT'
         AND r.is_active = TRUE
        JOIN public.users AS u
          ON u.id = ura.user_id
         AND u.company_id = ura.company_id
         AND u.status = 'active'
         AND u.deleted_at IS NULL
        WHERE ura.company_id = NEW.company_id
          AND ura.scope_type = 'team'
          AND ura.scope_team_id IS NOT NULL
          AND ura.revoked_at IS NULL
          AND public.app_is_eligible_department_agent(NEW.id, ura.user_id) IS TRUE
    ) THEN
        RETURN NEW;
    END IF;

    v_actor_id := public.company_admin_notification_actor(NEW.company_id);
    v_event_key := pg_catalog.format(
        'company-ticket:no-eligible-agent:%s:%s:%s',
        NEW.id,
        NEW.destination_department_id,
        NEW.domain_id
    );

    INSERT INTO public.notifications (
        company_id, ticket_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        NEW.company_id,
        NEW.id,
        recipient.recipient_user_id,
        'company_ticket_no_eligible_agent',
        'No eligible Support Agent available',
        pg_catalog.format(
            'Ticket #%s: %s has entered the department queue, but no active Support Agent currently has the required team and domain access.',
            NEW.ticket_number, NEW.subject
        ),
        v_actor_id,
        v_event_key,
        'ticket',
        NEW.id
    FROM public.company_admin_notification_recipients(NEW.company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_no_eligible_agent_insert ON public.tickets;
CREATE TRIGGER trg_notify_company_admin_no_eligible_agent_insert
AFTER INSERT ON public.tickets
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_no_eligible_agent();

DROP TRIGGER IF EXISTS trg_notify_company_admin_no_eligible_agent_update ON public.tickets;
CREATE TRIGGER trg_notify_company_admin_no_eligible_agent_update
AFTER UPDATE OF status_id, destination_department_id, domain_id ON public.tickets
FOR EACH ROW
WHEN (
    OLD.status_id IS DISTINCT FROM NEW.status_id
    OR OLD.destination_department_id IS DISTINCT FROM NEW.destination_department_id
    OR OLD.domain_id IS DISTINCT FROM NEW.domain_id
)
EXECUTE FUNCTION public.notify_company_admin_no_eligible_agent();

-- ------------------------------------------------------------
-- 4. User account creation, deactivation and reactivation.
--    Password/hash changes are deliberately not reported.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_user_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_type varchar(50);
    v_title varchar(255);
    v_message text;
    v_event_key text;
    v_name text;
BEGIN
    v_actor_id := public.company_admin_notification_actor(NEW.company_id);
    v_name := pg_catalog.btrim(
        COALESCE(NEW.first_name, '') || ' ' || COALESCE(NEW.last_name, '')
    );

    IF TG_OP = 'INSERT' THEN
        v_type := 'company_user_created';
        v_title := 'New user account created';
        v_message := pg_catalog.format(
            'A new account for %s was created in your company.',
            COALESCE(NULLIF(v_name, ''), 'a user')
        );
        v_event_key := pg_catalog.format(
            'company-user:created:%s', NEW.id
        );
    ELSE
        IF (OLD.status = 'active' AND OLD.deleted_at IS NULL)
           AND (NEW.status <> 'active' OR NEW.deleted_at IS NOT NULL) THEN
            v_type := 'company_user_deactivated';
            v_title := 'User account deactivated';
            v_message := pg_catalog.format(
                'The account for %s has been deactivated.',
                COALESCE(NULLIF(v_name, ''), 'a user')
            );
        ELSIF (OLD.status <> 'active' OR OLD.deleted_at IS NOT NULL)
           AND (NEW.status = 'active' AND NEW.deleted_at IS NULL) THEN
            v_type := 'company_user_reactivated';
            v_title := 'User account reactivated';
            v_message := pg_catalog.format(
                'The account for %s has been reactivated.',
                COALESCE(NULLIF(v_name, ''), 'a user')
            );
        ELSE
            RETURN NEW;
        END IF;

        v_event_key := pg_catalog.format(
            'company-user:%s:%s:%s',
            NEW.id, v_type, pg_catalog.txid_current()
        );
    END IF;

    INSERT INTO public.notifications (
        company_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        NEW.company_id,
        recipient.recipient_user_id,
        v_type,
        v_title,
        v_message,
        v_actor_id,
        v_event_key,
        'user',
        NEW.id
    FROM public.company_admin_notification_recipients(NEW.company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_user_created ON public.users;
CREATE TRIGGER trg_notify_company_admin_user_created
AFTER INSERT ON public.users
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_user_event();

DROP TRIGGER IF EXISTS trg_notify_company_admin_user_status ON public.users;
CREATE TRIGGER trg_notify_company_admin_user_status
AFTER UPDATE OF status, deleted_at ON public.users
FOR EACH ROW
WHEN (
    OLD.status IS DISTINCT FROM NEW.status
    OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
)
EXECUTE FUNCTION public.notify_company_admin_user_event();

-- ------------------------------------------------------------
-- 5. User role assignment grants and revocations.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_role_assignment_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_role_code text;
    v_user_name text;
    v_scope text;
    v_type varchar(50);
    v_title varchar(255);
    v_message text;
    v_event_key text;
BEGIN
    v_actor_id := public.company_admin_notification_actor(NEW.company_id);

    SELECT r.code,
           pg_catalog.btrim(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, ''))
      INTO v_role_code, v_user_name
      FROM public.roles AS r
      JOIN public.users AS u
        ON u.id = NEW.user_id AND u.company_id = NEW.company_id
     WHERE r.id = NEW.role_id;

    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    v_scope := CASE NEW.scope_type
        WHEN 'team' THEN 'team-scoped'
        WHEN 'department' THEN 'department-scoped'
        WHEN 'company' THEN 'company-wide'
        WHEN 'system' THEN 'system-scoped'
        ELSE NEW.scope_type
    END;

    IF TG_OP = 'INSERT' THEN
        IF NEW.revoked_at IS NOT NULL THEN
            RETURN NEW;
        END IF;
        v_type := 'company_user_role_granted';
        v_title := 'User role assigned';
        v_message := pg_catalog.format(
            '%s was granted the %s role (%s).',
            COALESCE(NULLIF(v_user_name, ''), 'A user'),
            COALESCE(v_role_code, 'role'), v_scope
        );
    ELSIF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
        v_type := 'company_user_role_granted';
        v_title := 'User role restored';
        v_message := pg_catalog.format(
            'The %s role was restored for %s.',
            COALESCE(v_role_code, 'role'),
            COALESCE(NULLIF(v_user_name, ''), 'a user')
        );
    ELSIF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL THEN
        v_type := 'company_user_role_revoked';
        v_title := 'User role revoked';
        v_message := pg_catalog.format(
            'The %s role was revoked from %s.',
            COALESCE(v_role_code, 'role'),
            COALESCE(NULLIF(v_user_name, ''), 'a user')
        );
    ELSIF ROW(OLD.role_id, OLD.scope_type, OLD.scope_department_id, OLD.scope_team_id)
          IS DISTINCT FROM
          ROW(NEW.role_id, NEW.scope_type, NEW.scope_department_id, NEW.scope_team_id) THEN
        v_type := 'company_user_role_changed';
        v_title := 'User role scope changed';
        v_message := pg_catalog.format(
            'The %s role assignment for %s was changed.',
            COALESCE(v_role_code, 'role'),
            COALESCE(NULLIF(v_user_name, ''), 'a user')
        );
    ELSE
        RETURN NEW;
    END IF;

    v_event_key := pg_catalog.format(
        'company-user-role:%s:%s:%s',
        NEW.user_id, v_type, pg_catalog.txid_current()
    );

    INSERT INTO public.notifications (
        company_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        NEW.company_id,
        recipient.recipient_user_id,
        v_type,
        v_title,
        v_message,
        v_actor_id,
        v_event_key,
        'user',
        NEW.user_id
    FROM public.company_admin_notification_recipients(NEW.company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_role_assignment_insert ON public.user_role_assignments;
CREATE TRIGGER trg_notify_company_admin_role_assignment_insert
AFTER INSERT ON public.user_role_assignments
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_role_assignment_event();

DROP TRIGGER IF EXISTS trg_notify_company_admin_role_assignment_update ON public.user_role_assignments;
CREATE TRIGGER trg_notify_company_admin_role_assignment_update
AFTER UPDATE OF revoked_at, role_id, scope_type, scope_department_id, scope_team_id
ON public.user_role_assignments
FOR EACH ROW
WHEN (
    OLD.revoked_at IS DISTINCT FROM NEW.revoked_at
    OR OLD.role_id IS DISTINCT FROM NEW.role_id
    OR OLD.scope_type IS DISTINCT FROM NEW.scope_type
    OR OLD.scope_department_id IS DISTINCT FROM NEW.scope_department_id
    OR OLD.scope_team_id IS DISTINCT FROM NEW.scope_team_id
)
EXECUTE FUNCTION public.notify_company_admin_role_assignment_event();

-- ------------------------------------------------------------
-- 6. Department, team, and domain create/update events.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_organization_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_entity_id uuid;
    v_entity_name text;
    v_entity_type text;
    v_event_type text;
    v_title varchar(255);
    v_message text;
    v_event_key text;
    v_company_id uuid;
BEGIN
    v_actor_id := public.company_admin_notification_actor(NEW.company_id);
    v_company_id := NEW.company_id;
    v_entity_id := (pg_catalog.to_jsonb(NEW)->>'id')::uuid;
    v_entity_name := COALESCE(NULLIF(pg_catalog.to_jsonb(NEW)->>'name', ''), 'Unnamed item');
    v_entity_type := CASE TG_TABLE_NAME
        WHEN 'departments' THEN 'department'
        WHEN 'teams' THEN 'team'
        WHEN 'domains' THEN 'domain'
        ELSE 'organization'
    END;

    IF TG_OP = 'INSERT' THEN
        v_event_type := 'created';
    ELSE
        IF pg_catalog.to_jsonb(OLD)->>'name' IS NOT DISTINCT FROM pg_catalog.to_jsonb(NEW)->>'name'
           AND pg_catalog.to_jsonb(OLD)->>'status' IS NOT DISTINCT FROM pg_catalog.to_jsonb(NEW)->>'status'
           AND pg_catalog.to_jsonb(OLD)->>'department_id' IS NOT DISTINCT FROM pg_catalog.to_jsonb(NEW)->>'department_id' THEN
            RETURN NEW;
        END IF;
        v_event_type := 'updated';
    END IF;

    v_title := pg_catalog.initcap(v_entity_type) || ' ' || v_event_type;
    v_message := pg_catalog.format(
        'The %s "%s" was %s in your company.',
        v_entity_type, v_entity_name, v_event_type
    );
    v_event_key := pg_catalog.format(
        'company-org:%s:%s:%s:%s',
        v_entity_type, v_entity_id, v_event_type, pg_catalog.txid_current()
    );

    INSERT INTO public.notifications (
        company_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        v_company_id,
        recipient.recipient_user_id,
        'company_' || v_entity_type || '_' || v_event_type,
        v_title,
        v_message,
        v_actor_id,
        v_event_key,
        v_entity_type,
        v_entity_id
    FROM public.company_admin_notification_recipients(v_company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_department_event ON public.departments;
CREATE TRIGGER trg_notify_company_admin_department_event
AFTER INSERT OR UPDATE OF name, status ON public.departments
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_organization_event();

DROP TRIGGER IF EXISTS trg_notify_company_admin_team_event ON public.teams;
CREATE TRIGGER trg_notify_company_admin_team_event
AFTER INSERT OR UPDATE OF name, status, department_id ON public.teams
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_organization_event();

DROP TRIGGER IF EXISTS trg_notify_company_admin_domain_event ON public.domains;
CREATE TRIGGER trg_notify_company_admin_domain_event
AFTER INSERT OR UPDATE OF name, status, department_id ON public.domains
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_organization_event();

-- ------------------------------------------------------------
-- 7. Company settings changes.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_settings_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_event_key text;
BEGIN
    IF ROW(
        OLD.display_name, OLD.timezone, OLD.default_sla_hours,
        OLD.reply_to, OLD.notify_company_admins
    ) IS NOT DISTINCT FROM ROW(
        NEW.display_name, NEW.timezone, NEW.default_sla_hours,
        NEW.reply_to, NEW.notify_company_admins
    ) THEN
        RETURN NEW;
    END IF;

    v_actor_id := public.company_admin_notification_actor(NEW.company_id);
    v_event_key := pg_catalog.format(
        'company-settings:%s:%s', NEW.company_id, pg_catalog.txid_current()
    );

    INSERT INTO public.notifications (
        company_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        NEW.company_id,
        recipient.recipient_user_id,
        'company_settings_updated',
        'Company settings updated',
        'Company configuration, SLA defaults, reply-to address, or notification preferences were changed.',
        v_actor_id,
        v_event_key,
        'company_settings',
        NEW.company_id
    FROM public.company_admin_notification_recipients(NEW.company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_settings_update ON public.company_settings;
CREATE TRIGGER trg_notify_company_admin_settings_update
AFTER UPDATE OF display_name, timezone, default_sla_hours, reply_to, notify_company_admins
ON public.company_settings
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_settings_event();

-- ------------------------------------------------------------
-- 8. Security-relevant audit events (tenant scope only).
--    Normal auth.login/auth.logout events are intentionally excluded to
--    prevent notification noise. User role assignments are handled by the
--    dedicated role-assignment trigger above.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_company_admin_security_audit_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_title varchar(255);
    v_message text;
    v_event_key text;
BEGIN
    IF NOT (
        (NEW.action LIKE 'auth.%' AND NEW.action NOT IN ('auth.login', 'auth.logout'))
        OR NEW.action LIKE 'session.%'
        OR NEW.action LIKE 'permission.%'
    ) THEN
        RETURN NEW;
    END IF;

    v_actor_id := public.company_admin_notification_actor(NEW.company_id);

    v_title := CASE
        WHEN NEW.action LIKE 'auth.%' THEN 'Authentication security event'
        WHEN NEW.action LIKE 'session.%' THEN 'User session security event'
        ELSE 'Permission security event'
    END;

    v_message := pg_catalog.format(
        'A security-related event (%s) was recorded for %s %s.',
        NEW.action,
        COALESCE(NULLIF(NEW.entity_type, ''), 'entity'),
        COALESCE(NEW.entity_id::text, 'unknown')
    );
    v_event_key := pg_catalog.format('company-security-audit:%s', NEW.id);

    INSERT INTO public.notifications (
        company_id, recipient_user_id, type, title, message,
        created_by, event_key, entity_type, entity_id
    )
    SELECT
        NEW.company_id,
        recipient.recipient_user_id,
        'company_security_event',
        v_title,
        v_message,
        v_actor_id,
        v_event_key,
        'audit_event',
        NEW.id
    FROM public.company_admin_notification_recipients(NEW.company_id, v_actor_id) AS recipient
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_notify_company_admin_security_audit ON public.audit_logs;
CREATE TRIGGER trg_notify_company_admin_security_audit
AFTER INSERT ON public.audit_logs
FOR EACH ROW
EXECUTE FUNCTION public.notify_company_admin_security_audit_event();

-- ------------------------------------------------------------
-- 9. Company Admin SLA and stale unclaimed-queue notification evaluator.

--    Called only when the authenticated user's notification feed is fetched.
--    It evaluates only the caller's own active COMPANY_ADMIN assignment.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.emit_company_admin_operational_notifications()
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

    IF NOT EXISTS (
        SELECT 1
        FROM public.company_admin_notification_recipients(v_company_id, NULL) AS ca
        WHERE ca.recipient_user_id = v_user_id
    ) THEN
        RETURN 0;
    END IF;

    WITH sla_candidates AS (
        SELECT
            t.company_id,
            t.id AS ticket_id,
            t.ticket_number,
            t.subject,
            tsl.resolution_due_at,
            CASE
                WHEN tsl.resolution_due_at < now()
                    THEN 'company_ticket_sla_breached'
                ELSE 'company_ticket_sla_due_soon'
            END AS notification_type
        FROM public.tickets AS t
        JOIN public.ticket_statuses AS s
          ON s.company_id = t.company_id
         AND s.id = t.status_id
        JOIN public.ticket_sla AS tsl
          ON tsl.ticket_id = t.id
        WHERE t.company_id = v_company_id
          AND s.code NOT IN ('RESOLVED', 'CLOSED', 'CANCELLED')
          AND tsl.resolution_due_at IS NOT NULL
          AND tsl.resolution_due_at <= now() + interval '4 hours'
    ),
    sla_inserted AS (
        INSERT INTO public.notifications (
            company_id, ticket_id, recipient_user_id, type, title, message,
            created_by, event_key, entity_type, entity_id
        )
        SELECT
            c.company_id,
            c.ticket_id,
            v_user_id,
            c.notification_type,
            CASE
                WHEN c.notification_type = 'company_ticket_sla_breached'
                    THEN 'Company ticket SLA breached'
                ELSE 'Company ticket SLA due soon'
            END,
            CASE
                WHEN c.notification_type = 'company_ticket_sla_breached'
                    THEN pg_catalog.format(
                        'Ticket #%s: %s has passed its resolution deadline.',
                        c.ticket_number, c.subject
                    )
                ELSE pg_catalog.format(
                    'Ticket #%s: %s is due for resolution within 4 hours.',
                    c.ticket_number, c.subject
                )
            END,
            NULL,
            pg_catalog.md5(pg_catalog.concat_ws(
                '|', c.notification_type, c.ticket_id::text, v_user_id::text,
                extract(epoch FROM c.resolution_due_at)::text
            )),
            'ticket',
            c.ticket_id
        FROM sla_candidates AS c
        ON CONFLICT (company_id, recipient_user_id, event_key)
            WHERE event_key IS NOT NULL
        DO NOTHING
        RETURNING 1
    ),
    backlog_candidates AS (
        SELECT
            t.company_id,
            t.id AS ticket_id,
            t.ticket_number,
            t.subject,
            q.created_at
        FROM public.ticket_department_queue AS q
        JOIN public.tickets AS t
          ON t.company_id = q.company_id
         AND t.id = q.ticket_id
        JOIN public.ticket_statuses AS s
          ON s.company_id = t.company_id
         AND s.id = t.status_id
        WHERE t.company_id = v_company_id
          AND q.queue_status = 'OPEN'
          AND q.created_at <= now() - interval '4 hours'
          AND s.code NOT IN ('RESOLVED', 'CLOSED', 'CANCELLED')
          AND NOT EXISTS (
              SELECT 1
              FROM public.ticket_claims AS tc
              WHERE tc.company_id = t.company_id
                AND tc.ticket_id = t.id
                AND tc.released_at IS NULL
          )
    ),
    backlog_inserted AS (
        INSERT INTO public.notifications (
            company_id, ticket_id, recipient_user_id, type, title, message,
            created_by, event_key, entity_type, entity_id
        )
        SELECT
            c.company_id,
            c.ticket_id,
            v_user_id,
            'company_ticket_queue_waiting_4h',
            'Ticket waiting in the department queue',
            pg_catalog.format(
                'Ticket #%s: %s has remained unclaimed in the shared department queue for more than 4 hours.',
                c.ticket_number, c.subject
            ),
            NULL,
            pg_catalog.md5(pg_catalog.concat_ws(
                '|', 'company_ticket_queue_waiting_4h', c.ticket_id::text,
                v_user_id::text, CURRENT_DATE::text
            )),
            'ticket',
            c.ticket_id
        FROM backlog_candidates AS c
        ON CONFLICT (company_id, recipient_user_id, event_key)
            WHERE event_key IS NOT NULL
        DO NOTHING
        RETURNING 1
    )
    SELECT
        (SELECT count(*)::integer FROM sla_inserted)
        + (SELECT count(*)::integer FROM backlog_inserted)
    INTO v_inserted;

    RETURN COALESCE(v_inserted, 0);
END;
$function$;

-- All trusted routines are owned by the documented NOLOGIN security owner.
ALTER FUNCTION public.notify_company_admin_priority_event() OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_company_admin_ticket_status_event() OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_company_admin_no_eligible_agent() OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_company_admin_user_event() OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_company_admin_role_assignment_event() OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_company_admin_organization_event() OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_company_admin_settings_event() OWNER TO ticket_security_owner;
ALTER FUNCTION public.notify_company_admin_security_audit_event() OWNER TO ticket_security_owner;
ALTER FUNCTION public.emit_company_admin_operational_notifications() OWNER TO ticket_security_owner;
ALTER FUNCTION public.company_admin_notification_actor(uuid) OWNER TO ticket_security_owner;

REVOKE ALL ON FUNCTION public.notify_company_admin_priority_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_company_admin_ticket_status_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_company_admin_no_eligible_agent() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_company_admin_user_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_company_admin_role_assignment_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_company_admin_organization_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_company_admin_settings_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.notify_company_admin_security_audit_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.emit_company_admin_operational_notifications() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.company_admin_notification_actor(uuid) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION public.notify_company_admin_priority_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_company_admin_ticket_status_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_company_admin_no_eligible_agent() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_company_admin_user_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_company_admin_role_assignment_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_company_admin_organization_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_company_admin_settings_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.notify_company_admin_security_audit_event() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.emit_company_admin_operational_notifications() TO ticket_app;
GRANT EXECUTE ON FUNCTION public.company_admin_notification_actor(uuid) TO ticket_security_owner;

COMMENT ON FUNCTION public.notify_company_admin_security_audit_event() IS
'Alerts same-company Company Admins about audit events under auth.*, session.*, and permission.*; normal login/logout and user-role assignment events are excluded.';
COMMENT ON FUNCTION public.company_admin_notification_actor(uuid) IS
'Returns the authenticated actor only if that actor belongs to the event company; platform actors are stored as NULL in tenant notifications.';
COMMENT ON FUNCTION public.company_admin_notification_recipients(uuid, uuid) IS
'Returns active company-scoped Company Admin recipients with ticket.view for a tenant. Callable only by ticket_security_owner.';
COMMENT ON FUNCTION public.notify_company_admin_no_eligible_agent() IS
'Alerts same-company Company Admins when a ticket has no eligible Support Agent. Does not assign or claim the ticket.';
COMMENT ON FUNCTION public.emit_company_admin_operational_notifications() IS
'Idempotently materializes resolution-SLA and >4-hour unclaimed shared-queue notifications for the authenticated Company Admin.';

COMMIT;
