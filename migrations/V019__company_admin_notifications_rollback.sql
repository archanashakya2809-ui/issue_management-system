-- ============================================================
-- V019 ROLLBACK: Company Admin notification extensions only.
-- Run only if V019 must be removed. Back up first.
-- Does NOT remove V018 event_key, V018 notifications, or any ticket data.
-- Drops V019 notification triggers and helper/evaluator functions, plus
-- the V019-only entity reference columns and lookup index.
-- Existing Company Admin notifications remain, but entity references are
-- discarded when entity_type/entity_id are dropped.
-- ============================================================

BEGIN;

DROP TRIGGER IF EXISTS trg_notify_company_admin_priority_insert ON public.tickets;
DROP TRIGGER IF EXISTS trg_notify_company_admin_priority_update ON public.tickets;
DROP TRIGGER IF EXISTS trg_notify_company_admin_ticket_status ON public.tickets;
DROP TRIGGER IF EXISTS trg_notify_company_admin_no_eligible_agent_insert ON public.tickets;
DROP TRIGGER IF EXISTS trg_notify_company_admin_no_eligible_agent_update ON public.tickets;
DROP TRIGGER IF EXISTS trg_notify_company_admin_user_created ON public.users;
DROP TRIGGER IF EXISTS trg_notify_company_admin_user_status ON public.users;
DROP TRIGGER IF EXISTS trg_notify_company_admin_role_assignment_insert ON public.user_role_assignments;
DROP TRIGGER IF EXISTS trg_notify_company_admin_role_assignment_update ON public.user_role_assignments;
DROP TRIGGER IF EXISTS trg_notify_company_admin_department_event ON public.departments;
DROP TRIGGER IF EXISTS trg_notify_company_admin_team_event ON public.teams;
DROP TRIGGER IF EXISTS trg_notify_company_admin_domain_event ON public.domains;
DROP TRIGGER IF EXISTS trg_notify_company_admin_settings_update ON public.company_settings;
DROP TRIGGER IF EXISTS trg_notify_company_admin_security_audit ON public.audit_logs;

DROP FUNCTION IF EXISTS public.notify_company_admin_priority_event();
DROP FUNCTION IF EXISTS public.notify_company_admin_ticket_status_event();
DROP FUNCTION IF EXISTS public.notify_company_admin_no_eligible_agent();
DROP FUNCTION IF EXISTS public.notify_company_admin_user_event();
DROP FUNCTION IF EXISTS public.notify_company_admin_role_assignment_event();
DROP FUNCTION IF EXISTS public.notify_company_admin_organization_event();
DROP FUNCTION IF EXISTS public.notify_company_admin_settings_event();
DROP FUNCTION IF EXISTS public.notify_company_admin_security_audit_event();
DROP FUNCTION IF EXISTS public.emit_company_admin_operational_notifications();
DROP FUNCTION IF EXISTS public.company_admin_notification_actor(uuid);
DROP FUNCTION IF EXISTS public.company_admin_notification_recipients(uuid, uuid);

DROP INDEX IF EXISTS public.idx_notifications_company_admin_entity;
DROP INDEX IF EXISTS public.idx_ticket_department_queue_company_open_created;
ALTER TABLE public.notifications DROP COLUMN IF EXISTS entity_type;
ALTER TABLE public.notifications DROP COLUMN IF EXISTS entity_id;

COMMIT;
