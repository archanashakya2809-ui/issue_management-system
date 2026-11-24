-- V015 verification: run after V015__platform_settings.sql.

SELECT
    to_regclass('public.platform_settings') IS NOT NULL AS table_exists,
    (SELECT relrowsecurity FROM pg_class WHERE oid = 'public.platform_settings'::regclass) AS rls_enabled,
    (SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'public.platform_settings'::regclass) AS table_owner;

SELECT
    company_id,
    app_name,
    default_sla_hours,
    session_timeout_minutes,
    max_login_attempts,
    lockout_minutes,
    password_policy,
    maintenance_mode,
    email_notifications,
    system_alerts
FROM public.platform_settings;

SELECT
    has_table_privilege('ticket_app', 'public.platform_settings', 'SELECT') AS ticket_app_can_select,
    has_column_privilege('ticket_app', 'public.platform_settings', 'app_name', 'UPDATE') AS ticket_app_can_update_settings,
    has_table_privilege('ticket_app', 'public.platform_settings', 'INSERT') AS ticket_app_can_insert,
    has_table_privilege('ticket_app', 'public.platform_settings', 'DELETE') AS ticket_app_can_delete;

SELECT schemaname, tablename, policyname, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public' AND tablename = 'platform_settings'
ORDER BY policyname;
