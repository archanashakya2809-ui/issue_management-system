-- Run after V011. Expected: all checks return true / zero invalid rows.
SELECT
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'tickets'
          AND column_name = 'destination_department_id' AND is_nullable = 'NO'
    ) AS destination_department_column_ready,
    to_regclass('public.ticket_responsibilities') IS NOT NULL AS responsibilities_table_exists,
    EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'uq_ticket_responsibility_active_type'
    ) AS active_responsibility_unique_index_exists,
    EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'ticket_responsibilities'
          AND policyname = 'ticket_responsibilities_select_policy'
    ) AS responsibility_select_policy_exists;

SELECT count(*) AS tickets_with_destination_department_mismatch
FROM public.tickets t
JOIN public.domains d
  ON d.company_id = t.company_id
 AND d.id = t.domain_id
WHERE t.destination_department_id IS DISTINCT FROM d.department_id;

SELECT count(*) AS active_duplicate_responsibility_types
FROM (
    SELECT ticket_id, responsibility_type
    FROM public.ticket_responsibilities
    WHERE unassigned_at IS NULL
    GROUP BY ticket_id, responsibility_type
    HAVING count(*) > 1
) duplicates;

SELECT
    c.relrowsecurity AS rls_enabled,
    pg_get_userbyid(c.relowner) AS table_owner
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname = 'ticket_responsibilities';


SELECT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgrelid = 'public.tickets'::regclass
      AND tgname = 'trg_ticket_destination_department_default'
      AND NOT tgisinternal
) AS destination_compatibility_trigger_exists;
