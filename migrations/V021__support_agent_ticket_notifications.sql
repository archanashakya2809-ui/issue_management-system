-- ============================================================
-- V021: Support Agent shared-queue notifications
--
-- Reuses public.notifications, its event_key unique index, the existing
-- app_is_eligible_department_agent(uuid,uuid) predicate, and the existing
-- department queue. It does not create assignments or claims.
--
-- Events:
--   1. A ticket enters (or returns to) the OPEN department queue.
--   2. A user becomes newly eligible for an already-OPEN queue ticket due
--      to a Support Agent role/scope, team membership, domain grant, role
--      domain grant, or activation of the relevant user/team/domain/role.
--
-- Important: This migration deliberately does not modify V012 or V014.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------
-- 0. Preconditions against the known post-rollback baseline.
-- ------------------------------------------------------------
DO $preflight$
DECLARE
    v_force_rls boolean;
BEGIN
    IF to_regclass('public.notifications') IS NULL
       OR to_regclass('public.tickets') IS NULL
       OR to_regclass('public.ticket_department_queue') IS NULL
       OR to_regclass('public.users') IS NULL
       OR to_regclass('public.user_role_assignments') IS NULL
       OR to_regclass('public.team_members') IS NULL
       OR to_regclass('public.user_domains') IS NULL
       OR to_regclass('public.role_domains') IS NULL
       OR to_regclass('public.teams') IS NULL
       OR to_regclass('public.domains') IS NULL
       OR to_regclass('public.roles') IS NULL THEN
        RAISE EXCEPTION
            'V021 preflight failed: one or more required notification, ticket, queue, or eligibility tables are missing.';
    END IF;

    IF to_regprocedure('public.app_is_eligible_department_agent(uuid,uuid)') IS NULL
       OR to_regprocedure('public.app_current_user_id()') IS NULL THEN
        RAISE EXCEPTION
            'V021 preflight failed: canonical agent eligibility or actor-context function is missing.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_security_owner')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION
            'V021 requires the existing ticket_security_owner and ticket_app roles.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_roles r ON r.oid = c.relowner
        WHERE n.nspname = 'public'
          AND c.relname = 'notifications'
          AND r.rolname = 'ticket_security_owner'
    ) THEN
        RAISE EXCEPTION
            'V021 expects ticket_security_owner to own public.notifications, matching the verified live baseline.';
    END IF;

    SELECT c.relforcerowsecurity
      INTO v_force_rls
      FROM pg_catalog.pg_class c
      JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname = 'notifications';

    IF COALESCE(v_force_rls, TRUE) THEN
        RAISE EXCEPTION
            'V021 requires notifications FORCE ROW LEVEL SECURITY to remain disabled for the trusted table-owner trigger insert path. Review the security design before proceeding.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'notifications'
          AND indexname = 'uq_notifications_company_recipient_event_key'
    ) THEN
        RAISE EXCEPTION
            'V021 prerequisite missing: uq_notifications_company_recipient_event_key from the existing notification event model.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tickets'
          AND column_name IN ('id', 'company_id', 'domain_id', 'ticket_number', 'subject')
    ) <> 5 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: required tickets columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ticket_department_queue'
          AND column_name IN ('company_id', 'ticket_id', 'queue_status')
    ) <> 3 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: ticket_department_queue.company_id/ticket_id/queue_status.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'notifications'
          AND column_name IN (
              'company_id', 'ticket_id', 'recipient_user_id', 'type',
              'title', 'message', 'created_by', 'event_key', 'entity_type', 'entity_id'
          )
    ) <> 10 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: required notifications columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'user_role_assignments'
          AND column_name IN ('company_id', 'user_id', 'role_id', 'scope_type', 'scope_team_id', 'revoked_at')
    ) <> 6 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: user_role_assignments columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'team_members'
          AND column_name IN ('company_id', 'team_id', 'user_id', 'left_at')
    ) <> 4 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: team_members columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'user_domains'
          AND column_name IN ('company_id', 'user_id', 'domain_id', 'revoked_at')
    ) <> 4 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: user_domains columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'role_domains'
          AND column_name IN ('company_id', 'role_id', 'domain_id')
    ) <> 3 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: role_domains columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'roles'
          AND column_name IN ('id', 'code', 'is_active', 'domain_access_mode')
    ) <> 4 THEN
        RAISE EXCEPTION 'V021 prerequisite missing: roles columns.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.roles
        WHERE code = 'SUPPORT_AGENT' AND is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'V021 prerequisite missing: active SUPPORT_AGENT role.';
    END IF;
END;
$preflight$;

-- Match the existing trusted notification-trigger permission model.
GRANT EXECUTE ON FUNCTION public.app_is_eligible_department_agent(uuid, uuid)
    TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_current_user_id()
    TO ticket_security_owner;
GRANT SELECT ON TABLE
    public.tickets,
    public.ticket_department_queue,
    public.users,
    public.roles,
    public.user_role_assignments
TO ticket_security_owner;
GRANT INSERT ON TABLE public.notifications TO ticket_security_owner;

-- ------------------------------------------------------------
-- 1. Idempotent materializer. Eligibility remains authoritative in
--    app_is_eligible_department_agent(); this routine only narrows the
--    candidate set and writes one event per company/ticket/recipient.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.emit_support_agent_ticket_available_notifications(
    p_company_id uuid DEFAULT NULL,
    p_user_id uuid DEFAULT NULL,
    p_role_id uuid DEFAULT NULL,
    p_team_id uuid DEFAULT NULL,
    p_domain_id uuid DEFAULT NULL,
    p_ticket_id uuid DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_inserted integer := 0;
BEGIN
    INSERT INTO public.notifications (
        company_id,
        ticket_id,
        recipient_user_id,
        type,
        title,
        message,
        created_by,
        event_key,
        entity_type,
        entity_id
    )
    SELECT
        t.company_id,
        t.id,
        u.id,
        'ticket_available_for_support_agent',
        'New ticket available to claim',
        pg_catalog.format(
            'Ticket #%s: %s is available in your department queue. Open it to review and claim it.',
            t.ticket_number,
            t.subject
        ),
        public.app_current_user_id(),
        pg_catalog.format('support-agent-ticket-available:%s', t.id),
        'ticket',
        t.id
    FROM public.ticket_department_queue AS q
    JOIN public.tickets AS t
      ON t.company_id = q.company_id
     AND t.id = q.ticket_id
    JOIN public.users AS u
      ON u.company_id = t.company_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    WHERE q.queue_status = 'OPEN'
      AND (p_company_id IS NULL OR t.company_id = p_company_id)
      AND (p_user_id IS NULL OR u.id = p_user_id)
      AND (p_ticket_id IS NULL OR t.id = p_ticket_id)
      AND (p_domain_id IS NULL OR t.domain_id = p_domain_id)
      AND EXISTS (
          SELECT 1
          FROM public.user_role_assignments AS ura
          JOIN public.roles AS r
            ON r.id = ura.role_id
           AND r.code = 'SUPPORT_AGENT'
           AND r.is_active = TRUE
          WHERE ura.company_id = t.company_id
            AND ura.user_id = u.id
            AND ura.scope_type = 'team'
            AND ura.scope_team_id IS NOT NULL
            AND ura.revoked_at IS NULL
            AND (p_role_id IS NULL OR ura.role_id = p_role_id)
            AND (p_team_id IS NULL OR ura.scope_team_id = p_team_id)
      )
      AND public.app_is_eligible_department_agent(t.id, u.id) IS TRUE
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RETURN COALESCE(v_inserted, 0);
END;
$function$;

ALTER FUNCTION public.emit_support_agent_ticket_available_notifications(
    uuid, uuid, uuid, uuid, uuid, uuid
) OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.emit_support_agent_ticket_available_notifications(
    uuid, uuid, uuid, uuid, uuid, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.emit_support_agent_ticket_available_notifications(
    uuid, uuid, uuid, uuid, uuid, uuid
) TO ticket_security_owner;

COMMENT ON FUNCTION public.emit_support_agent_ticket_available_notifications(
    uuid, uuid, uuid, uuid, uuid, uuid
) IS
'Idempotently notifies only eligible active Support Agents for tickets in the OPEN shared department queue. Eligibility is delegated to app_is_eligible_department_agent; the function does not assign or claim tickets.';

-- ------------------------------------------------------------
-- 2. Queue event: notification happens once the shared queue row exists.
--    Also handles a ticket returning to OPEN after a release/reopen flow.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_support_agents_from_department_queue()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
    IF NEW.queue_status = 'OPEN' THEN
        PERFORM public.emit_support_agent_ticket_available_notifications(
            p_company_id => NEW.company_id,
            p_ticket_id => NEW.ticket_id
        );
    END IF;
    RETURN NEW;
END;
$function$;

ALTER FUNCTION public.notify_support_agents_from_department_queue()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_support_agents_from_department_queue() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_support_agents_from_department_queue() TO ticket_app;

DROP TRIGGER IF EXISTS trg_notify_support_agents_queue_insert
    ON public.ticket_department_queue;
CREATE TRIGGER trg_notify_support_agents_queue_insert
AFTER INSERT ON public.ticket_department_queue
FOR EACH ROW
WHEN (NEW.queue_status = 'OPEN')
EXECUTE FUNCTION public.notify_support_agents_from_department_queue();

DROP TRIGGER IF EXISTS trg_notify_support_agents_queue_reopened
    ON public.ticket_department_queue;
CREATE TRIGGER trg_notify_support_agents_queue_reopened
AFTER UPDATE OF queue_status ON public.ticket_department_queue
FOR EACH ROW
WHEN (OLD.queue_status IS DISTINCT FROM NEW.queue_status AND NEW.queue_status = 'OPEN')
EXECUTE FUNCTION public.notify_support_agents_from_department_queue();

-- ------------------------------------------------------------
-- 3. Eligibility-change event dispatcher. Only grant/activation states
--    invoke the materializer. Revocations do not generate availability alerts.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_newly_eligible_support_agents()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_company_id uuid := NULL;
    v_user_id uuid := NULL;
    v_role_id uuid := NULL;
    v_team_id uuid := NULL;
    v_domain_id uuid := NULL;
BEGIN
    IF TG_TABLE_NAME = 'user_role_assignments' THEN
        v_company_id := NEW.company_id;
        v_user_id := NEW.user_id;
        v_role_id := NEW.role_id;
        v_team_id := NEW.scope_team_id;
    ELSIF TG_TABLE_NAME = 'team_members' THEN
        v_company_id := NEW.company_id;
        v_user_id := NEW.user_id;
        v_team_id := NEW.team_id;
    ELSIF TG_TABLE_NAME = 'user_domains' THEN
        v_company_id := NEW.company_id;
        v_user_id := NEW.user_id;
        v_domain_id := NEW.domain_id;
    ELSIF TG_TABLE_NAME = 'role_domains' THEN
        v_company_id := NEW.company_id;
        v_role_id := NEW.role_id;
        v_domain_id := NEW.domain_id;
    ELSIF TG_TABLE_NAME = 'teams' THEN
        v_company_id := NEW.company_id;
        v_team_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'domains' THEN
        v_company_id := NEW.company_id;
        v_domain_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'roles' THEN
        v_role_id := NEW.id;
    ELSIF TG_TABLE_NAME = 'users' THEN
        v_company_id := NEW.company_id;
        v_user_id := NEW.id;
    ELSE
        RAISE EXCEPTION 'Unsupported table for Support Agent eligibility notification: %', TG_TABLE_NAME;
    END IF;

    PERFORM public.emit_support_agent_ticket_available_notifications(
        p_company_id => v_company_id,
        p_user_id => v_user_id,
        p_role_id => v_role_id,
        p_team_id => v_team_id,
        p_domain_id => v_domain_id
    );

    RETURN NEW;
END;
$function$;

ALTER FUNCTION public.notify_newly_eligible_support_agents()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_newly_eligible_support_agents() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_newly_eligible_support_agents() TO ticket_app;

-- Role assignment: newly active SUPPORT_AGENT role/scope for this user.
DROP TRIGGER IF EXISTS trg_notify_support_agent_role_grant
    ON public.user_role_assignments;
CREATE TRIGGER trg_notify_support_agent_role_grant
AFTER INSERT OR UPDATE OF revoked_at, role_id, scope_type, scope_team_id
ON public.user_role_assignments
FOR EACH ROW
WHEN (NEW.revoked_at IS NULL AND NEW.scope_type = 'team' AND NEW.scope_team_id IS NOT NULL)
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

-- Team membership: newly active membership can satisfy the role's team scope.
DROP TRIGGER IF EXISTS trg_notify_support_agent_team_membership
    ON public.team_members;
CREATE TRIGGER trg_notify_support_agent_team_membership
AFTER INSERT OR UPDATE OF left_at, team_id, user_id
ON public.team_members
FOR EACH ROW
WHEN (NEW.left_at IS NULL)
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

-- Direct user-domain grant or restoration.
DROP TRIGGER IF EXISTS trg_notify_support_agent_user_domain_grant
    ON public.user_domains;
CREATE TRIGGER trg_notify_support_agent_user_domain_grant
AFTER INSERT OR UPDATE OF revoked_at, domain_id, user_id
ON public.user_domains
FOR EACH ROW
WHEN (NEW.revoked_at IS NULL)
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

-- Role-to-domain grant. Deletion/revocation cannot make a user newly eligible.
DROP TRIGGER IF EXISTS trg_notify_support_agent_role_domain_grant
    ON public.role_domains;
CREATE TRIGGER trg_notify_support_agent_role_domain_grant
AFTER INSERT OR UPDATE OF role_id, domain_id, company_id
ON public.role_domains
FOR EACH ROW
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

-- Reactivating a team or domain can make existing scoped grants effective again.
DROP TRIGGER IF EXISTS trg_notify_support_agent_team_activated
    ON public.teams;
CREATE TRIGGER trg_notify_support_agent_team_activated
AFTER UPDATE OF status ON public.teams
FOR EACH ROW
WHEN (OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'active')
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

DROP TRIGGER IF EXISTS trg_notify_support_agent_domain_activated
    ON public.domains;
CREATE TRIGGER trg_notify_support_agent_domain_activated
AFTER UPDATE OF status ON public.domains
FOR EACH ROW
WHEN (OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'active')
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

-- Reactivating the Support Agent role or changing its domain access mode.
DROP TRIGGER IF EXISTS trg_notify_support_agent_role_activated
    ON public.roles;
CREATE TRIGGER trg_notify_support_agent_role_activated
AFTER UPDATE OF is_active, domain_access_mode, code ON public.roles
FOR EACH ROW
WHEN (
    NEW.code = 'SUPPORT_AGENT'
    AND NEW.is_active = TRUE
    AND (
        OLD.is_active IS DISTINCT FROM NEW.is_active
        OR OLD.domain_access_mode IS DISTINCT FROM NEW.domain_access_mode
        OR OLD.code IS DISTINCT FROM NEW.code
    )
)
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

-- Reactivating a user account can restore their queue eligibility.
DROP TRIGGER IF EXISTS trg_notify_support_agent_user_activated
    ON public.users;
CREATE TRIGGER trg_notify_support_agent_user_activated
AFTER UPDATE OF status, deleted_at ON public.users
FOR EACH ROW
WHEN (
    NEW.status = 'active'
    AND NEW.deleted_at IS NULL
    AND (
        OLD.status IS DISTINCT FROM NEW.status
        OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
    )
)
EXECUTE FUNCTION public.notify_newly_eligible_support_agents();

COMMENT ON FUNCTION public.notify_newly_eligible_support_agents() IS
'On Support Agent role, team membership, domain authorization, team/domain activation, role activation, or user reactivation, emits idempotent availability notices for existing OPEN queue tickets where the canonical eligibility function returns true.';

COMMIT;
