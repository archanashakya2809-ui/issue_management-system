-- ============================================================
-- V020 verification: System Admin platform notifications
-- Read-only. Run after V020__system_admin_notifications.sql.
-- ============================================================

DO $$
DECLARE
    v_missing text[] := ARRAY[]::text[];
    v_platform_count integer;
    v_admin_count integer;
    v_trigger_count integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'notifications'
          AND column_name = 'source_company_id'
    ) THEN
        v_missing := array_append(v_missing, 'notifications.source_company_id');
    END IF;

    IF to_regprocedure('public.system_admin_notification_recipients(uuid)') IS NULL THEN
        v_missing := array_append(v_missing, 'system_admin_notification_recipients(uuid)');
    END IF;
    IF to_regprocedure('public.emit_system_admin_notification(character varying,character varying,text,text,uuid,character varying,uuid,uuid)') IS NULL THEN
        v_missing := array_append(v_missing, 'emit_system_admin_notification(...)');
    END IF;
    IF to_regprocedure('public.emit_system_admin_health_notification(text,text,text,text,uuid)') IS NULL THEN
        v_missing := array_append(v_missing, 'emit_system_admin_health_notification(...)');
    END IF;

    SELECT count(*) INTO v_platform_count
    FROM public.companies WHERE is_platform = TRUE;

    IF v_platform_count <> 1 THEN
        v_missing := array_append(v_missing, 'exactly one platform company');
    END IF;

    SELECT count(DISTINCT ura.user_id)
      INTO v_admin_count
      FROM public.user_role_assignments ura
      JOIN public.roles r ON r.id = ura.role_id AND r.code = 'SYSTEM_ADMIN' AND r.is_active
      JOIN public.users u ON u.id = ura.user_id AND u.company_id = ura.company_id
        AND u.status = 'active' AND u.deleted_at IS NULL
      JOIN public.companies c ON c.id = ura.company_id AND c.is_platform
     WHERE ura.scope_type = 'system'
       AND ura.scope_department_id IS NULL
       AND ura.scope_team_id IS NULL
       AND ura.revoked_at IS NULL;

    IF v_admin_count = 0 THEN
        v_missing := array_append(v_missing, 'active platform-scoped SYSTEM_ADMIN recipient');
    END IF;

    SELECT count(*) INTO v_trigger_count
    FROM pg_trigger tg
    JOIN pg_class tbl ON tbl.oid = tg.tgrelid
    JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
    WHERE NOT tg.tgisinternal
      AND ns.nspname = 'public'
      AND tg.tgname IN (
        'trg_notify_system_admin_tenant_created',
        'trg_notify_system_admin_tenant_updated',
        'trg_notify_system_admin_platform_settings',
        'trg_notify_system_admin_audit_event',
        'trg_notify_system_admin_user_security_update',
        'trg_notify_system_admin_privileged_role_insert',
        'trg_notify_system_admin_privileged_role_update',
        'trg_notify_system_admin_role_permission_insert',
        'trg_notify_system_admin_role_permission_delete'
      );

    IF v_trigger_count <> 9 THEN
        v_missing := array_append(v_missing, format('nine System Admin event triggers (found %s)', v_trigger_count));
    END IF;

    IF cardinality(v_missing) > 0 THEN
        RAISE EXCEPTION 'V020 verification failed: %', array_to_string(v_missing, ', ');
    END IF;
END $$;

-- Function security/ownership verification.
SELECT
    p.oid::regprocedure AS function_name,
    p.prosecdef AS security_definer,
    p.provolatile AS volatility,
    pg_get_userbyid(p.proowner) AS function_owner
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
    'system_admin_notification_recipients',
    'emit_system_admin_notification',
    'notify_system_admin_tenant_lifecycle',
    'notify_system_admin_platform_settings',
    'notify_system_admin_audit_event',
    'notify_system_admin_user_security_event',
    'notify_system_admin_privileged_role_change',
    'notify_system_admin_role_permission_change',
    'emit_system_admin_health_notification'
  )
ORDER BY p.proname;

-- Platform company and currently eligible notification recipients.
SELECT
    c.id AS platform_company_id,
    c.name AS platform_company_name,
    count(DISTINCT ura.user_id) AS active_system_admin_recipients
FROM public.companies c
LEFT JOIN public.user_role_assignments ura
  ON ura.company_id = c.id
 AND ura.scope_type = 'system'
 AND ura.scope_department_id IS NULL
 AND ura.scope_team_id IS NULL
 AND ura.revoked_at IS NULL
LEFT JOIN public.roles r
  ON r.id = ura.role_id AND r.code = 'SYSTEM_ADMIN' AND r.is_active
LEFT JOIN public.users u
  ON u.id = ura.user_id AND u.company_id = ura.company_id
 AND u.status = 'active' AND u.deleted_at IS NULL
WHERE c.is_platform = TRUE
GROUP BY c.id, c.name;

-- Trigger inventory.
SELECT
    tbl.relname AS table_name,
    tg.tgname AS trigger_name,
    pg_get_triggerdef(tg.oid) AS trigger_definition
FROM pg_trigger tg
JOIN pg_class tbl ON tbl.oid = tg.tgrelid
JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
WHERE ns.nspname = 'public'
  AND NOT tg.tgisinternal
  AND tg.tgname LIKE 'trg_notify_system_admin_%'
ORDER BY tbl.relname, tg.tgname;

-- Token-protected health-ingestion function is executable by the API database role.
SELECT
    has_function_privilege(
        'ticket_app',
        'public.emit_system_admin_health_notification(text,text,text,text,uuid)',
        'EXECUTE'
    ) AS ticket_app_can_call_health_ingestion;
