-- ============================================================
-- V021 rollback
-- Removes only V021 triggers and functions.
-- Existing notification rows are intentionally preserved.
-- No ticket, queue, claim, assignment, or V012/V014 objects are changed.
-- ============================================================

BEGIN;

DROP TRIGGER IF EXISTS trg_notify_support_agents_queue_insert
    ON public.ticket_department_queue;
DROP TRIGGER IF EXISTS trg_notify_support_agents_queue_reopened
    ON public.ticket_department_queue;

DROP TRIGGER IF EXISTS trg_notify_support_agent_role_grant
    ON public.user_role_assignments;
DROP TRIGGER IF EXISTS trg_notify_support_agent_team_membership
    ON public.team_members;
DROP TRIGGER IF EXISTS trg_notify_support_agent_user_domain_grant
    ON public.user_domains;
DROP TRIGGER IF EXISTS trg_notify_support_agent_role_domain_grant
    ON public.role_domains;
DROP TRIGGER IF EXISTS trg_notify_support_agent_team_activated
    ON public.teams;
DROP TRIGGER IF EXISTS trg_notify_support_agent_domain_activated
    ON public.domains;
DROP TRIGGER IF EXISTS trg_notify_support_agent_role_activated
    ON public.roles;
DROP TRIGGER IF EXISTS trg_notify_support_agent_user_activated
    ON public.users;

DROP FUNCTION IF EXISTS public.notify_newly_eligible_support_agents();
DROP FUNCTION IF EXISTS public.notify_support_agents_from_department_queue();
DROP FUNCTION IF EXISTS public.emit_support_agent_ticket_available_notifications(
    uuid, uuid, uuid, uuid, uuid, uuid
);

COMMIT;
