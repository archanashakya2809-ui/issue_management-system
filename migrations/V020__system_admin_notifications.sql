-- ============================================================
-- V020: System Admin platform notifications
--
-- Builds on V018/V019 and reuses public.notifications.
-- System Admin notifications are stored under the single platform
-- company's company_id; source_company_id identifies an affected tenant.
--
-- Events:
--   * Tenant created / status changed / profile changed
--   * Platform security audit events
--   * Privileged role assignment and SYSTEM_ADMIN permission changes
--   * Platform settings changed
--   * External health monitor reports degraded / recovered incidents
--
-- No ticket ownership, claim, routing, or resolution behavior is changed.
-- ============================================================

BEGIN;

DO $preflight$
DECLARE
    v_platform_company_count integer;
BEGIN
    IF to_regclass('public.notifications') IS NULL
       OR to_regclass('public.companies') IS NULL
       OR to_regclass('public.users') IS NULL
       OR to_regclass('public.roles') IS NULL
       OR to_regclass('public.user_role_assignments') IS NULL
       OR to_regclass('public.role_permissions') IS NULL
       OR to_regclass('public.permissions') IS NULL
       OR to_regclass('public.audit_logs') IS NULL
       OR to_regclass('public.platform_settings') IS NULL THEN
        RAISE EXCEPTION
            'V020 preflight failed: required notification, tenant, identity, audit, or platform settings table is missing. Apply V015, V018 and V019 first.';
    END IF;

    IF to_regprocedure('public.app_current_user_id()') IS NULL THEN
        RAISE EXCEPTION 'V020 preflight failed: app_current_user_id() is missing.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_security_owner')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION 'V020 requires existing roles ticket_security_owner and ticket_app.';
    END IF;

    SELECT count(*)
      INTO v_platform_company_count
      FROM public.companies
     WHERE is_platform = TRUE;

    IF v_platform_company_count <> 1 THEN
        RAISE EXCEPTION
            'V020 requires exactly one platform company; found %.',
            v_platform_company_count;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.roles
        WHERE code = 'SYSTEM_ADMIN'
          AND is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'V020 prerequisite missing: active SYSTEM_ADMIN role.';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.user_role_assignments ura
        JOIN public.roles r ON r.id = ura.role_id
        JOIN public.companies c ON c.id = ura.company_id AND c.is_platform = TRUE
        JOIN public.users u ON u.id = ura.user_id AND u.company_id = ura.company_id
        WHERE r.code = 'SYSTEM_ADMIN'
          AND r.is_active = TRUE
          AND ura.scope_type = 'system'
          AND ura.scope_department_id IS NULL
          AND ura.scope_team_id IS NULL
          AND ura.revoked_at IS NULL
          AND u.status = 'active'
          AND u.deleted_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'V020 prerequisite missing: at least one active system-scoped SYSTEM_ADMIN assignment on the platform company is required.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'notifications'
          AND column_name = 'event_key'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'notifications'
          AND column_name = 'entity_type'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'notifications'
          AND column_name = 'entity_id'
    ) THEN
        RAISE EXCEPTION
            'V020 prerequisite missing: apply V018/V019 notification event_key/entity reference columns first.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'notifications'
          AND indexname = 'uq_notifications_company_recipient_event_key'
    ) THEN
        RAISE EXCEPTION
            'V020 prerequisite missing: notification event idempotency index from V018.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'companies'
          AND column_name IN ('id', 'name', 'code', 'status', 'is_platform')
    ) <> 5 THEN
        RAISE EXCEPTION 'V020 prerequisite missing: expected companies columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'user_role_assignments'
          AND column_name IN (
              'company_id', 'user_id', 'role_id', 'scope_type',
              'scope_department_id', 'scope_team_id', 'revoked_at'
          )
    ) <> 7 THEN
        RAISE EXCEPTION 'V020 prerequisite missing: user_role_assignments scope columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'users'
          AND column_name IN ('id', 'company_id', 'first_name', 'last_name', 'email', 'status', 'deleted_at')
    ) <> 7 THEN
        RAISE EXCEPTION 'V020 prerequisite missing: users identity/status columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'audit_logs'
          AND column_name IN ('id', 'company_id', 'user_id', 'action', 'entity_type', 'entity_id')
    ) <> 6 THEN
        RAISE EXCEPTION 'V020 prerequisite missing: audit_logs event columns.';
    END IF;

    IF (
        SELECT count(DISTINCT column_name)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'platform_settings'
          AND column_name IN (
              'company_id', 'app_name', 'default_sla_hours',
              'session_timeout_minutes', 'max_login_attempts',
              'lockout_minutes', 'password_policy', 'maintenance_mode',
              'email_notifications', 'system_alerts'
          )
    ) <> 10 THEN
        RAISE EXCEPTION 'V020 prerequisite missing: platform_settings columns.';
    END IF;
END;
$preflight$;

-- Platform notification rows remain owned by the platform company's feed.
-- source_company_id points to the tenant (if any) that caused the event.
ALTER TABLE public.notifications
    ADD COLUMN IF NOT EXISTS source_company_id uuid
        REFERENCES public.companies(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_notifications_platform_source
    ON public.notifications(company_id, recipient_user_id, source_company_id, created_at DESC)
    WHERE source_company_id IS NOT NULL;

COMMENT ON COLUMN public.notifications.source_company_id IS
    'Tenant/company that originated a platform-scoped notification. The notification row itself remains in the recipient platform company feed.';

-- SECURITY DEFINER functions run with a locked search_path and fully qualified objects.
GRANT SELECT ON TABLE
    public.companies,
    public.users,
    public.roles,
    public.user_role_assignments,
    public.role_permissions,
    public.permissions,
    public.audit_logs,
    public.platform_settings
TO ticket_security_owner;
GRANT INSERT ON TABLE public.notifications TO ticket_security_owner;
GRANT EXECUTE ON FUNCTION public.app_current_user_id() TO ticket_security_owner;

-- ------------------------------------------------------------
-- Recipient resolver: only active SYSTEM_ADMIN assignments that are
-- explicitly scoped to the single platform company.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.system_admin_notification_recipients(
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
     AND r.code = 'SYSTEM_ADMIN'
     AND r.is_active = TRUE
    JOIN public.users AS u
      ON u.id = ura.user_id
     AND u.company_id = ura.company_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    JOIN public.companies AS platform_company
      ON platform_company.id = ura.company_id
     AND platform_company.is_platform = TRUE
    WHERE ura.scope_type = 'system'
      AND ura.scope_department_id IS NULL
      AND ura.scope_team_id IS NULL
      AND ura.revoked_at IS NULL
      AND ura.user_id IS DISTINCT FROM p_exclude_user_id;
$function$;

ALTER FUNCTION public.system_admin_notification_recipients(uuid)
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.system_admin_notification_recipients(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.system_admin_notification_recipients(uuid)
    TO ticket_security_owner;

-- Shared writer. Notifications always belong to the platform company's feed.
CREATE OR REPLACE FUNCTION public.emit_system_admin_notification(
    p_type varchar(50),
    p_title varchar(255),
    p_message text,
    p_event_key text,
    p_source_company_id uuid DEFAULT NULL,
    p_entity_type varchar(40) DEFAULT NULL,
    p_entity_id uuid DEFAULT NULL,
    p_exclude_user_id uuid DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_platform_company_id uuid;
    v_platform_actor_id uuid;
    v_inserted integer := 0;
BEGIN
    SELECT c.id
      INTO v_platform_company_id
      FROM public.companies AS c
     WHERE c.is_platform = TRUE
     LIMIT 1;

    IF v_platform_company_id IS NULL THEN
        RETURN 0;
    END IF;

    -- notifications.created_by is company-scoped in this schema. Preserve the
    -- actor only when the actor belongs to the platform company; tenant actors
    -- are represented by the audit source event instead of a cross-tenant FK.
    IF p_exclude_user_id IS NOT NULL THEN
        SELECT u.id
          INTO v_platform_actor_id
          FROM public.users AS u
         WHERE u.id = p_exclude_user_id
           AND u.company_id = v_platform_company_id;
    END IF;

    INSERT INTO public.notifications (
        company_id,
        recipient_user_id,
        type,
        title,
        message,
        created_by,
        event_key,
        entity_type,
        entity_id,
        source_company_id
    )
    SELECT
        v_platform_company_id,
        recipients.recipient_user_id,
        p_type,
        p_title,
        p_message,
        v_platform_actor_id,
        p_event_key,
        p_entity_type,
        p_entity_id,
        p_source_company_id
    FROM public.system_admin_notification_recipients(p_exclude_user_id) AS recipients
    ON CONFLICT (company_id, recipient_user_id, event_key)
        WHERE event_key IS NOT NULL
    DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;
    RETURN v_inserted;
END;
$function$;

ALTER FUNCTION public.emit_system_admin_notification(
    varchar, varchar, text, text, uuid, varchar, uuid, uuid
) OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.emit_system_admin_notification(
    varchar, varchar, text, text, uuid, varchar, uuid, uuid
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.emit_system_admin_notification(
    varchar, varchar, text, text, uuid, varchar, uuid, uuid
) TO ticket_security_owner;

-- ------------------------------------------------------------
-- 1. Tenant lifecycle: created, status changed, or identifying details changed.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_system_admin_tenant_lifecycle()
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
BEGIN
    IF NEW.is_platform = TRUE THEN
        RETURN NEW;
    END IF;

    v_actor_id := public.app_current_user_id();

    IF TG_OP = 'INSERT' THEN
        v_type := 'system_tenant_created';
        v_title := 'New tenant registered';
        v_message := pg_catalog.format(
            'Tenant "%s" (%s) was created with status %s.',
            NEW.name, NEW.code, NEW.status
        );
        v_event_key := pg_catalog.format('system-tenant:created:%s', NEW.id);
    ELSIF OLD.is_platform = TRUE THEN
        RETURN NEW;
    ELSIF OLD.status IS DISTINCT FROM NEW.status THEN
        v_type := 'system_tenant_status_changed';
        v_title := 'Tenant status changed';
        v_message := pg_catalog.format(
            'Tenant "%s" (%s) changed status from %s to %s.',
            NEW.name, NEW.code, OLD.status, NEW.status
        );
        v_event_key := pg_catalog.format(
            'system-tenant:status:%s:%s', NEW.id, pg_catalog.txid_current()
        );
    ELSIF OLD.name IS DISTINCT FROM NEW.name OR OLD.code IS DISTINCT FROM NEW.code THEN
        v_type := 'system_tenant_profile_updated';
        v_title := 'Tenant details updated';
        v_message := pg_catalog.format(
            'Tenant details were updated: "%s" (%s).', NEW.name, NEW.code
        );
        v_event_key := pg_catalog.format(
            'system-tenant:profile:%s:%s', NEW.id, pg_catalog.txid_current()
        );
    ELSE
        RETURN NEW;
    END IF;

    PERFORM public.emit_system_admin_notification(
        v_type, v_title, v_message, v_event_key,
        NEW.id, 'platform_company', NEW.id, v_actor_id
    );

    RETURN NEW;
END;
$function$;

ALTER FUNCTION public.notify_system_admin_tenant_lifecycle()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_system_admin_tenant_lifecycle() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_system_admin_tenant_lifecycle()
    TO ticket_security_owner;

DROP TRIGGER IF EXISTS trg_notify_system_admin_tenant_created ON public.companies;
CREATE TRIGGER trg_notify_system_admin_tenant_created
AFTER INSERT ON public.companies
FOR EACH ROW
EXECUTE FUNCTION public.notify_system_admin_tenant_lifecycle();

DROP TRIGGER IF EXISTS trg_notify_system_admin_tenant_updated ON public.companies;
CREATE TRIGGER trg_notify_system_admin_tenant_updated
AFTER UPDATE OF status, name, code ON public.companies
FOR EACH ROW
WHEN (
    OLD.status IS DISTINCT FROM NEW.status
    OR OLD.name IS DISTINCT FROM NEW.name
    OR OLD.code IS DISTINCT FROM NEW.code
)
EXECUTE FUNCTION public.notify_system_admin_tenant_lifecycle();

-- ------------------------------------------------------------
-- 2. Platform settings: notify when persisted platform settings change.
-- Sensitive values (password settings) are not copied into notification text.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_system_admin_platform_settings()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
BEGIN
    IF ROW(
        OLD.app_name, OLD.default_sla_hours, OLD.session_timeout_minutes,
        OLD.max_login_attempts, OLD.lockout_minutes, OLD.password_policy,
        OLD.maintenance_mode, OLD.email_notifications, OLD.system_alerts
    ) IS NOT DISTINCT FROM ROW(
        NEW.app_name, NEW.default_sla_hours, NEW.session_timeout_minutes,
        NEW.max_login_attempts, NEW.lockout_minutes, NEW.password_policy,
        NEW.maintenance_mode, NEW.email_notifications, NEW.system_alerts
    ) THEN
        RETURN NEW;
    END IF;

    v_actor_id := public.app_current_user_id();
    PERFORM public.emit_system_admin_notification(
        'system_platform_configuration_updated',
        'Platform configuration updated',
        pg_catalog.format(
            'Platform settings were updated. Application: %s. Maintenance mode: %s.',
            NEW.app_name,
            CASE WHEN NEW.maintenance_mode THEN 'enabled' ELSE 'disabled' END
        ),
        pg_catalog.format('system-platform-settings:%s:%s', NEW.company_id, pg_catalog.txid_current()),
        NEW.company_id,
        'platform_settings',
        NEW.company_id,
        v_actor_id
    );

    RETURN NEW;
END;
$function$;

ALTER FUNCTION public.notify_system_admin_platform_settings()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_system_admin_platform_settings() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_system_admin_platform_settings()
    TO ticket_security_owner;

DROP TRIGGER IF EXISTS trg_notify_system_admin_platform_settings ON public.platform_settings;
CREATE TRIGGER trg_notify_system_admin_platform_settings
AFTER UPDATE ON public.platform_settings
FOR EACH ROW
EXECUTE FUNCTION public.notify_system_admin_platform_settings();

-- ------------------------------------------------------------
-- 3. Account status/security events across all tenant companies.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_system_admin_user_security_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_user_name text;
    v_type varchar(50);
    v_title varchar(255);
    v_event_key text;
    v_message text;
BEGIN
    IF OLD.status IS NOT DISTINCT FROM NEW.status
       AND OLD.deleted_at IS NOT DISTINCT FROM NEW.deleted_at THEN
        RETURN NEW;
    END IF;

    IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN
        v_type := 'system_user_deactivated';
        v_title := 'User account deleted';
        v_event_key := pg_catalog.format('system-user:deleted:%s:%s', NEW.id, pg_catalog.txid_current());
    ELSIF OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'locked' THEN
        v_type := 'system_user_locked';
        v_title := 'User account locked';
        v_event_key := pg_catalog.format('system-user:locked:%s:%s', NEW.id, pg_catalog.txid_current());
    ELSIF OLD.status IS DISTINCT FROM NEW.status AND NEW.status <> 'active' THEN
        v_type := 'system_user_deactivated';
        v_title := 'User account deactivated';
        v_event_key := pg_catalog.format('system-user:status:%s:%s:%s', NEW.id, NEW.status, pg_catalog.txid_current());
    ELSIF (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL)
       OR (OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'active') THEN
        v_type := 'system_user_reactivated';
        v_title := 'User account reactivated';
        v_event_key := pg_catalog.format('system-user:reactivated:%s:%s', NEW.id, pg_catalog.txid_current());
    ELSE
        RETURN NEW;
    END IF;

    SELECT pg_catalog.btrim(COALESCE(NEW.first_name, '') || ' ' || COALESCE(NEW.last_name, ''))
      INTO v_user_name;
    v_actor_id := public.app_current_user_id();
    v_message := pg_catalog.format(
        'Account for %s (%s) changed to status %s.',
        COALESCE(NULLIF(v_user_name, ''), 'a user'), NEW.email, NEW.status
    );

    PERFORM public.emit_system_admin_notification(
        v_type, v_title, v_message, v_event_key,
        NEW.company_id, 'platform_user', NEW.id, v_actor_id
    );

    RETURN NEW;
END;
$function$;

ALTER FUNCTION public.notify_system_admin_user_security_event()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_system_admin_user_security_event() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_system_admin_user_security_event()
    TO ticket_security_owner;

DROP TRIGGER IF EXISTS trg_notify_system_admin_user_security_update ON public.users;
CREATE TRIGGER trg_notify_system_admin_user_security_update
AFTER UPDATE OF status, deleted_at ON public.users
FOR EACH ROW
WHEN (
    OLD.status IS DISTINCT FROM NEW.status
    OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
)
EXECUTE FUNCTION public.notify_system_admin_user_security_event();

-- ------------------------------------------------------------
-- 4. Platform security / configuration audit events from all tenants.
-- Ordinary successful auth.login/auth.logout noise is excluded.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_system_admin_audit_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_actor_id uuid;
    v_company_name text;
    v_type varchar(50);
    v_title varchar(255);
    v_message text;
BEGIN
    IF NOT (
        (NEW.action LIKE 'auth.%' AND NEW.action NOT IN ('auth.login', 'auth.logout'))
        OR NEW.action LIKE 'session.%'
        OR NEW.action LIKE 'permission.%'
        OR NEW.action LIKE 'security.%'
        OR NEW.action LIKE 'system.%'
        OR NEW.action LIKE 'workflow.%'
        OR NEW.action LIKE 'platform.%'
    ) THEN
        RETURN NEW;
    END IF;

    -- platform.settings.update has a dedicated trigger above.
    IF NEW.action = 'platform.settings.update' THEN
        RETURN NEW;
    END IF;

    v_actor_id := NEW.user_id;
    SELECT c.name
      INTO v_company_name
      FROM public.companies AS c
     WHERE c.id = NEW.company_id;

    IF NEW.action LIKE 'auth.%'
       OR NEW.action LIKE 'session.%'
       OR NEW.action LIKE 'security.%'
       OR NEW.action LIKE 'permission.%' THEN
        v_type := 'system_security_event';
        v_title := 'Platform security event';
    ELSE
        v_type := 'system_configuration_audit_event';
        v_title := 'Platform configuration event';
    END IF;

    v_message := pg_catalog.format(
        'Audit event "%s" was recorded for tenant "%s". Entity: %s.',
        NEW.action,
        COALESCE(v_company_name, 'Platform'),
        COALESCE(NEW.entity_type, 'unspecified')
    );

    PERFORM public.emit_system_admin_notification(
        v_type,
        v_title,
        v_message,
        pg_catalog.format('system-audit:%s', NEW.id),
        NEW.company_id,
        'platform_audit_event',
        NEW.id,
        v_actor_id
    );

    RETURN NEW;
END;
$function$;

ALTER FUNCTION public.notify_system_admin_audit_event()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_system_admin_audit_event() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_system_admin_audit_event()
    TO ticket_security_owner;

DROP TRIGGER IF EXISTS trg_notify_system_admin_audit_event ON public.audit_logs;
CREATE TRIGGER trg_notify_system_admin_audit_event
AFTER INSERT ON public.audit_logs
FOR EACH ROW
EXECUTE FUNCTION public.notify_system_admin_audit_event();

-- ------------------------------------------------------------
-- 5. Privileged role changes across tenants (COMPANY_ADMIN/SYSTEM_ADMIN).
-- Lower-impact agent role grants remain within Company Admin notifications.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_system_admin_privileged_role_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_old_role_code text;
    v_new_role_code text;
    v_role_label text;
    v_user_name text;
    v_company_name text;
    v_actor_id uuid;
    v_action text;
    v_message text;
    v_event_key text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT r.code INTO v_old_role_code
        FROM public.roles AS r
        WHERE r.id = OLD.role_id;
    END IF;

    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT r.code INTO v_new_role_code
        FROM public.roles AS r
        WHERE r.id = NEW.role_id;
    END IF;

    IF COALESCE(v_old_role_code, '') NOT IN ('COMPANY_ADMIN', 'SYSTEM_ADMIN')
       AND COALESCE(v_new_role_code, '') NOT IN ('COMPANY_ADMIN', 'SYSTEM_ADMIN') THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;

    -- Ignore insertion of an already-revoked historical assignment.
    IF TG_OP = 'INSERT' AND NEW.revoked_at IS NOT NULL THEN
        RETURN NEW;
    END IF;

    SELECT pg_catalog.btrim(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, ''))
      INTO v_user_name
      FROM public.users AS u
     WHERE u.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.user_id ELSE NEW.user_id END
       AND u.company_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.company_id ELSE NEW.company_id END;

    SELECT c.name
      INTO v_company_name
      FROM public.companies AS c
     WHERE c.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.company_id ELSE NEW.company_id END;

    v_actor_id := public.app_current_user_id();

    IF TG_OP = 'DELETE' THEN
        v_action := 'deleted';
        v_role_label := COALESCE(v_old_role_code, 'privileged role');
        v_event_key := pg_catalog.format(
            'system-privileged-role:delete:%s:%s:%s:%s',
            OLD.company_id, OLD.user_id, OLD.role_id, pg_catalog.txid_current()
        );
    ELSIF TG_OP = 'INSERT' THEN
        v_action := 'granted';
        v_role_label := COALESCE(v_new_role_code, 'privileged role');
        v_event_key := pg_catalog.format(
            'system-privileged-role:insert:%s:%s:%s:%s',
            NEW.company_id, NEW.user_id, NEW.role_id, pg_catalog.txid_current()
        );
    ELSE
        v_role_label := CASE
            WHEN v_old_role_code IS DISTINCT FROM v_new_role_code
                THEN COALESCE(v_old_role_code, 'none') || ' -> ' || COALESCE(v_new_role_code, 'none')
            ELSE COALESCE(v_new_role_code, v_old_role_code, 'privileged role')
        END;

        IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL THEN
            v_action := 'revoked';
        ELSIF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
            v_action := 'restored';
        ELSIF OLD.role_id IS DISTINCT FROM NEW.role_id THEN
            v_action := 'changed';
        ELSE
            v_action := 'scope changed';
        END IF;

        v_event_key := pg_catalog.format(
            'system-privileged-role:update:%s:%s:%s:%s',
            NEW.company_id, NEW.user_id, NEW.id, pg_catalog.txid_current()
        );
    END IF;

    v_message := pg_catalog.format(
        'Privileged role assignment %s was %s for %s in tenant %s.',
        v_role_label,
        v_action,
        COALESCE(NULLIF(v_user_name, ''), 'a user'),
        COALESCE(v_company_name, 'an unknown tenant')
    );

    PERFORM public.emit_system_admin_notification(
        'system_privileged_role_changed',
        'Privileged role assignment changed',
        v_message,
        v_event_key,
        CASE WHEN TG_OP = 'DELETE' THEN OLD.company_id ELSE NEW.company_id END,
        'platform_user',
        CASE WHEN TG_OP = 'DELETE' THEN OLD.user_id ELSE NEW.user_id END,
        v_actor_id
    );

    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$function$;

ALTER FUNCTION public.notify_system_admin_privileged_role_change()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_system_admin_privileged_role_change() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_system_admin_privileged_role_change()
    TO ticket_security_owner;

DROP TRIGGER IF EXISTS trg_notify_system_admin_privileged_role_insert ON public.user_role_assignments;
CREATE TRIGGER trg_notify_system_admin_privileged_role_insert
AFTER INSERT ON public.user_role_assignments
FOR EACH ROW
EXECUTE FUNCTION public.notify_system_admin_privileged_role_change();

DROP TRIGGER IF EXISTS trg_notify_system_admin_privileged_role_update ON public.user_role_assignments;
CREATE TRIGGER trg_notify_system_admin_privileged_role_update
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
EXECUTE FUNCTION public.notify_system_admin_privileged_role_change();

-- ------------------------------------------------------------
-- 6. Permissions changed on the SYSTEM_ADMIN role.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.notify_system_admin_role_permission_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_role_id uuid;
    v_permission_id uuid;
    v_role_code text;
    v_permission_code text;
    v_actor_id uuid;
    v_platform_company_id uuid;
    v_action text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_role_id := OLD.role_id;
        v_permission_id := OLD.permission_id;
        v_action := 'revoked';
    ELSE
        v_role_id := NEW.role_id;
        v_permission_id := NEW.permission_id;
        v_action := 'granted';
    END IF;

    SELECT r.code INTO v_role_code
      FROM public.roles AS r
     WHERE r.id = v_role_id;

    IF v_role_code <> 'SYSTEM_ADMIN' THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END IF;

    SELECT p.code INTO v_permission_code
      FROM public.permissions AS p
     WHERE p.id = v_permission_id;

    SELECT c.id INTO v_platform_company_id
      FROM public.companies AS c
     WHERE c.is_platform = TRUE
     LIMIT 1;

    v_actor_id := public.app_current_user_id();

    PERFORM public.emit_system_admin_notification(
        'system_role_permission_changed',
        'System Admin permissions changed',
        pg_catalog.format(
            'Permission %s was %s on the SYSTEM_ADMIN role.',
            COALESCE(v_permission_code, v_permission_id::text), v_action
        ),
        pg_catalog.format(
            'system-role-permission:%s:%s:%s:%s',
            v_role_id, v_permission_id, v_action, pg_catalog.txid_current()
        ),
        v_platform_company_id,
        'platform_permission',
        v_permission_id,
        v_actor_id
    );

    IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$function$;

ALTER FUNCTION public.notify_system_admin_role_permission_change()
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.notify_system_admin_role_permission_change() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.notify_system_admin_role_permission_change()
    TO ticket_security_owner;

DROP TRIGGER IF EXISTS trg_notify_system_admin_role_permission_insert ON public.role_permissions;
CREATE TRIGGER trg_notify_system_admin_role_permission_insert
AFTER INSERT ON public.role_permissions
FOR EACH ROW
EXECUTE FUNCTION public.notify_system_admin_role_permission_change();

DROP TRIGGER IF EXISTS trg_notify_system_admin_role_permission_delete ON public.role_permissions;
CREATE TRIGGER trg_notify_system_admin_role_permission_delete
AFTER DELETE ON public.role_permissions
FOR EACH ROW
EXECUTE FUNCTION public.notify_system_admin_role_permission_change();

-- ------------------------------------------------------------
-- 7. Health incident ingestion function. A separate monitoring system calls
-- this only after a real service-state transition. It emits idempotent
-- degraded/recovered events to System Admins.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.emit_system_admin_health_notification(
    p_service text,
    p_state text,
    p_incident_key text,
    p_summary text,
    p_source_company_id uuid DEFAULT NULL
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_type varchar(50);
    v_title varchar(255);
    v_event_key text;
    v_message text;
    v_source_company_name text;
BEGIN
    IF p_service IS NULL OR pg_catalog.length(pg_catalog.btrim(p_service)) NOT BETWEEN 1 AND 80 THEN
        RAISE EXCEPTION 'Health service name must contain 1 to 80 characters.';
    END IF;

    IF p_state IS NULL OR p_state NOT IN ('degraded', 'recovered') THEN
        RAISE EXCEPTION 'Health state must be degraded or recovered.';
    END IF;

    IF p_incident_key IS NULL OR pg_catalog.length(pg_catalog.btrim(p_incident_key)) NOT BETWEEN 1 AND 160 THEN
        RAISE EXCEPTION 'Incident key must contain 1 to 160 characters.';
    END IF;

    IF p_summary IS NULL OR pg_catalog.length(pg_catalog.btrim(p_summary)) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'Health summary must contain 1 to 500 characters.';
    END IF;

    IF p_source_company_id IS NOT NULL THEN
        SELECT c.name
          INTO v_source_company_name
          FROM public.companies AS c
         WHERE c.id = p_source_company_id;
        IF v_source_company_name IS NULL THEN
            RAISE EXCEPTION 'Source company does not exist.';
        END IF;
    END IF;

    IF p_state = 'degraded' THEN
        v_type := 'system_health_degraded';
        v_title := 'Platform service degraded';
    ELSE
        v_type := 'system_health_recovered';
        v_title := 'Platform service recovered';
    END IF;

    v_event_key := pg_catalog.format(
        'system-health:%s:%s:%s',
        pg_catalog.btrim(p_incident_key),
        pg_catalog.btrim(p_service),
        p_state
    );
    v_message := pg_catalog.format(
        '%s — %s%s',
        pg_catalog.btrim(p_service),
        pg_catalog.btrim(p_summary),
        CASE WHEN v_source_company_name IS NULL THEN ''
             ELSE pg_catalog.format(' (tenant: %s)', v_source_company_name) END
    );

    RETURN public.emit_system_admin_notification(
        v_type,
        v_title,
        v_message,
        v_event_key,
        p_source_company_id,
        'system_health',
        NULL,
        NULL
    );
END;
$function$;

ALTER FUNCTION public.emit_system_admin_health_notification(text, text, text, text, uuid)
    OWNER TO ticket_security_owner;
REVOKE ALL ON FUNCTION public.emit_system_admin_health_notification(text, text, text, text, uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.emit_system_admin_health_notification(text, text, text, text, uuid)
    TO ticket_app;
COMMENT ON FUNCTION public.emit_system_admin_health_notification(text, text, text, text, uuid) IS
    'Callable by ticket_app only through the token-protected external monitor ingestion endpoint. Do not expose as a user-authenticated route.';

-- The internal helper and recipient selector are not directly callable by the app role.
REVOKE ALL ON FUNCTION public.system_admin_notification_recipients(uuid) FROM ticket_app;
REVOKE ALL ON FUNCTION public.emit_system_admin_notification(
    varchar, varchar, text, text, uuid, varchar, uuid, uuid
) FROM ticket_app;

COMMIT;
