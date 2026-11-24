-- Verify V018 Department Manager notifications.

SELECT
    table_schema,
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'notifications'
  AND column_name = 'event_key';

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('notifications', 'ticket_sla')
  AND indexname IN (
      'uq_notifications_company_recipient_event_key',
      'idx_ticket_sla_resolution_due_at'
  )
ORDER BY indexname;

SELECT tgname AS trigger_name, pg_get_triggerdef(oid) AS trigger_definition
FROM pg_trigger
WHERE NOT tgisinternal
  AND tgname IN (
      'trg_notify_department_manager_priority_insert',
      'trg_notify_department_manager_priority_update',
      'trg_notify_department_manager_ticket_status'
  )
ORDER BY tgname;

SELECT
    p.oid::regprocedure AS function_name,
    p.prosecdef AS security_definer,
    pg_get_userbyid(p.proowner) AS function_owner
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
      'notify_department_manager_priority_event',
      'notify_department_manager_ticket_status_event',
      'emit_department_manager_sla_notifications'
  )
ORDER BY p.proname;
