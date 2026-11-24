SELECT
    t.tgname AS trigger_name,
    p.proname AS function_name,
    pg_get_functiondef(p.oid) AS function_definition
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_proc p ON p.oid = t.tgfoid
WHERE n.nspname = 'public'
  AND c.relname = 'tickets'
  AND NOT t.tgisinternal
  AND (
      t.tgname = 'trg_require_active_category_route'
      OR p.proname = 'validate_ticket_update_permissions'
  )
ORDER BY t.tgname;
