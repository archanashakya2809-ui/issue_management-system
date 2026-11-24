-- ============================================================
-- V021 verification (read-only)
-- Run after V021__support_agent_ticket_notifications.sql.
-- ============================================================

-- 1. Confirm all expected triggers exist and are enabled.
SELECT
    c.relname AS table_name,
    t.tgname AS trigger_name,
    CASE t.tgenabled WHEN 'O' THEN 'enabled' WHEN 'D' THEN 'disabled'
         WHEN 'R' THEN 'replica' WHEN 'A' THEN 'always' ELSE t.tgenabled::text END AS trigger_state,
    pg_catalog.pg_get_triggerdef(t.oid) AS trigger_definition
FROM pg_catalog.pg_trigger t
JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND NOT t.tgisinternal
  AND t.tgname IN (
      'trg_notify_support_agents_queue_insert',
      'trg_notify_support_agents_queue_reopened',
      'trg_notify_support_agent_role_grant',
      'trg_notify_support_agent_team_membership',
      'trg_notify_support_agent_user_domain_grant',
      'trg_notify_support_agent_role_domain_grant',
      'trg_notify_support_agent_team_activated',
      'trg_notify_support_agent_domain_activated',
      'trg_notify_support_agent_role_activated',
      'trg_notify_support_agent_user_activated'
  )
ORDER BY c.relname, t.tgname;

-- 2. Confirm trusted function owner and SECURITY DEFINER/search_path settings.
SELECT
    p.oid::regprocedure AS function_name,
    r.rolname AS function_owner,
    p.prosecdef AS security_definer,
    p.proconfig AS function_settings,
    pg_catalog.has_function_privilege('ticket_app', p.oid, 'EXECUTE') AS ticket_app_can_execute
FROM pg_catalog.pg_proc p
JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
JOIN pg_catalog.pg_roles r ON r.oid = p.proowner
WHERE n.nspname = 'public'
  AND p.proname IN (
      'emit_support_agent_ticket_available_notifications',
      'notify_support_agents_from_department_queue',
      'notify_newly_eligible_support_agents'
  )
ORDER BY p.proname;

-- 3. Count currently eligible OPEN queue ticket / Support Agent pairs.
--    This should be compared with the outstanding-notification count below.
WITH eligible_pairs AS (
    SELECT DISTINCT
        t.company_id,
        t.id AS ticket_id,
        u.id AS recipient_user_id,
        pg_catalog.format('support-agent-ticket-available:%s', t.id) AS event_key
    FROM public.ticket_department_queue q
    JOIN public.tickets t
      ON t.company_id = q.company_id AND t.id = q.ticket_id
    JOIN public.users u
      ON u.company_id = t.company_id
     AND u.status = 'active'
     AND u.deleted_at IS NULL
    JOIN public.user_role_assignments ura
      ON ura.company_id = t.company_id
     AND ura.user_id = u.id
     AND ura.scope_type = 'team'
     AND ura.scope_team_id IS NOT NULL
     AND ura.revoked_at IS NULL
    JOIN public.roles r
      ON r.id = ura.role_id
     AND r.code = 'SUPPORT_AGENT'
     AND r.is_active = TRUE
    WHERE q.queue_status = 'OPEN'
      AND public.app_is_eligible_department_agent(t.id, u.id) IS TRUE
)
SELECT
    count(*) AS currently_eligible_open_ticket_agent_pairs,
    count(*) FILTER (
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.notifications n
            WHERE n.company_id = eligible_pairs.company_id
              AND n.ticket_id = eligible_pairs.ticket_id
              AND n.recipient_user_id = eligible_pairs.recipient_user_id
              AND n.event_key = eligible_pairs.event_key
        )
    ) AS pairs_without_availability_notification
FROM eligible_pairs;

-- 4. Review the most recent Support Agent queue notifications.
SELECT
    n.id,
    n.company_id,
    n.ticket_id,
    n.recipient_user_id,
    n.type,
    n.title,
    n.is_read,
    n.event_key,
    n.created_at
FROM public.notifications n
WHERE n.type = 'ticket_available_for_support_agent'
ORDER BY n.created_at DESC
LIMIT 100;

-- 5. Confirm there are no duplicate event rows for a recipient.
SELECT company_id, recipient_user_id, event_key, count(*) AS duplicate_count
FROM public.notifications
WHERE type = 'ticket_available_for_support_agent'
GROUP BY company_id, recipient_user_id, event_key
HAVING count(*) > 1;
