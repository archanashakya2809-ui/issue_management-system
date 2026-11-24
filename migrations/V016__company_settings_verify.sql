-- Run after V016__company_settings.sql.

SELECT
    c.name AS company_name,
    cs.company_id,
    cs.display_name,
    cs.timezone,
    cs.default_sla_hours,
    cs.reply_to,
    cs.notify_company_admins,
    cs.updated_at
FROM public.companies c
LEFT JOIN public.company_settings cs ON cs.company_id = c.id
WHERE c.is_platform = FALSE
ORDER BY c.name;

SELECT
    pg_get_userbyid(c.relowner) AS table_owner,
    c.relrowsecurity AS rls_enabled,
    c.relforcerowsecurity AS force_rls,
    has_table_privilege('ticket_app', 'public.company_settings', 'SELECT') AS ticket_app_can_select,
    has_table_privilege('ticket_app', 'public.company_settings', 'INSERT') AS ticket_app_can_insert,
    has_column_privilege('ticket_app', 'public.company_settings', 'display_name', 'UPDATE') AS ticket_app_can_update
FROM pg_class c
WHERE c.oid = 'public.company_settings'::regclass;

SELECT policyname, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public' AND tablename = 'company_settings'
ORDER BY policyname;

SELECT
    r.code AS role_code,
    p.code AS permission_code
FROM public.roles r
JOIN public.role_permissions rp ON rp.role_id = r.id
JOIN public.permissions p ON p.id = rp.permission_id
WHERE p.code = 'company.settings.manage'
ORDER BY r.code;
