
-- Confirm the helper name/signature actually used by deployment-security v2.8.
SELECT
    to_regprocedure('public.app_user_has_permission_in_scope(uuid,character varying,uuid,uuid)')
        IS NOT NULL AS expected_scope_permission_helper_exists,
    has_function_privilege(
        'ticket_app',
        'public.app_user_has_permission_in_scope(uuid,character varying,uuid,uuid)',
        'EXECUTE'
    ) AS ticket_app_can_execute_scope_permission_helper;

-- V013 verification. Expected: owner/execute flags correct and mismatch counts zero.
SELECT
    p.oid::regprocedure AS function_signature,
    pg_get_userbyid(p.proowner) AS function_owner,
    p.prosecdef AS security_definer,
    has_function_privilege('ticket_app', p.oid, 'EXECUTE') AS ticket_app_can_execute
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN (
      'app_user_can_select_ticket_destination',
      'app_ticket_destination_is_valid',
      'app_user_can_create_ticket'
  )
ORDER BY p.proname;

SELECT policyname, with_check
FROM pg_policies
WHERE schemaname = 'public' AND tablename = 'tickets'
  AND policyname = 'tickets_insert_policy';

SELECT tablename, policyname, qual
FROM pg_policies
WHERE schemaname = 'public'
  AND (
      (tablename = 'departments' AND policyname = 'departments_select_policy')
      OR (tablename = 'domains' AND policyname = 'domains_select_policy')
      OR (tablename = 'ticket_categories' AND policyname = 'ticket_categories_select_policy')
  )
ORDER BY tablename;

SELECT count(*) AS tickets_with_destination_domain_mismatch
FROM public.tickets t
JOIN public.domains d ON d.company_id = t.company_id AND d.id = t.domain_id
WHERE t.destination_department_id IS DISTINCT FROM d.department_id;

SELECT count(*) AS tickets_with_missing_or_cross_company_destination
FROM public.tickets t
LEFT JOIN public.departments dep
  ON dep.company_id = t.company_id AND dep.id = t.destination_department_id
WHERE dep.id IS NULL;

-- Informational only: old tickets may legitimately retain an inactive department
-- after an organizational deactivation. New-ticket validation rejects inactive depts.
SELECT count(*) AS historical_tickets_with_inactive_destination_department
FROM public.tickets t
JOIN public.departments dep
  ON dep.company_id = t.company_id AND dep.id = t.destination_department_id
WHERE dep.status <> 'active';

SELECT p.proname,
       position('destination_department_id' in pg_get_functiondef(p.oid)) > 0
           AS uses_destination_department
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN ('app_ticket_access', 'app_user_role_scope_matches_ticket');


SELECT p.proname,
       position('SYSTEM_ADMIN' in pg_get_functiondef(p.oid)) > 0
           AS preserves_system_admin_only_system_scope,
       position('destination_department_id' in pg_get_functiondef(p.oid)) > 0
           AS uses_destination_department
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname IN ('app_ticket_access', 'app_user_role_scope_matches_ticket')
ORDER BY p.proname;


-- Expected: the SECURITY DEFINER owner can invoke trusted identity/domain helpers.
SELECT
    has_function_privilege('ticket_security_owner', 'public.app_current_user_id()', 'EXECUTE')
        AS can_read_current_user_id,
    has_function_privilege('ticket_security_owner', 'public.app_current_user_company_id()', 'EXECUTE')
        AS can_read_current_company_id,
    has_function_privilege('ticket_security_owner', 'public.app_default_team_for_category(uuid,uuid)', 'EXECUTE')
        AS can_resolve_default_team,
    has_function_privilege('ticket_security_owner', 'public.app_user_domain_access(uuid,uuid,uuid,uuid)', 'EXECUTE')
        AS can_check_domain_access;
