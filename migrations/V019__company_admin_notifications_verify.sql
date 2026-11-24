-- ============================================================
-- V019 VERIFICATION: Company Admin notifications
-- Run against the same `ticket` database after V019 commits.
-- Read-only.
-- ============================================================

-- 1. Columns and dedupe index added by V019.
SELECT
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'notifications'
          AND column_name = 'entity_type'
    ) AS entity_type_column_exists,
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'notifications'
          AND column_name = 'entity_id'
    ) AS entity_id_column_exists,
    EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'notifications'
          AND indexname = 'idx_notifications_company_admin_entity'
    ) AS entity_lookup_index_exists,
    EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename = 'notifications'
          AND indexname = 'uq_notifications_company_recipient_event_key'
    ) AS v018_event_dedupe_index_exists;

-- 2. All V019 trigger registrations.
SELECT
    t.tgname AS trigger_name,
    c.relname AS table_name,
    p.proname AS function_name,
    t.tgenabled = 'O' AS enabled
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_proc p ON p.oid = t.tgfoid
WHERE n.nspname = 'public'
  AND NOT t.tgisinternal
  AND t.tgname LIKE 'trg_notify_company_admin_%'
ORDER BY c.relname, t.tgname;

-- 3. Verify security-definer ownership and runtime execute permissions.
SELECT
    p.proname AS function_name,
    p.prosecdef AS security_definer,
    p.provolatile AS volatility,
    pg_get_userbyid(p.proowner) AS function_owner,
    has_function_privilege('ticket_app', p.oid, 'EXECUTE') AS ticket_app_can_execute
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
      'company_admin_notification_recipients',
      'company_admin_notification_actor',
      'notify_company_admin_priority_event',
      'notify_company_admin_ticket_status_event',
      'notify_company_admin_no_eligible_agent',
      'notify_company_admin_user_event',
      'notify_company_admin_role_assignment_event',
      'notify_company_admin_organization_event',
      'notify_company_admin_settings_event',
      'notify_company_admin_security_audit_event',
      'emit_company_admin_operational_notifications'
  )
ORDER BY p.proname;

-- 4. Validate tenant-scoped recipient setup.
-- Replace the UUID with a real tenant company ID when running this check.
-- This returns only eligible Company Admin user IDs; do not publish the output.
-- SELECT recipient_user_id
-- FROM public.company_admin_notification_recipients(
--     '00000000-0000-0000-0000-000000000000'::uuid,
--     NULL
-- );

-- 5. Verify that the evaluator is callable by the API role.
SELECT
    has_function_privilege(
        'ticket_app',
        'public.emit_company_admin_operational_notifications()',
        'EXECUTE'
    ) AS ticket_app_can_run_company_admin_evaluator;
