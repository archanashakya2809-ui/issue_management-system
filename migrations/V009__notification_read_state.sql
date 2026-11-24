-- ============================================================
-- V009: Allow users to update only their own notification read state
-- Apply with a database owner / DBA role after the base schema.
-- PostgreSQL 15+
-- ============================================================

BEGIN;

ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS notifications_update_own_read_state
    ON public.notifications;

CREATE POLICY notifications_update_own_read_state
ON public.notifications
FOR UPDATE
USING (
    company_id = public.app_current_user_company_id()
    AND recipient_user_id = public.app_current_user_id()
)
WITH CHECK (
    company_id = public.app_current_user_company_id()
    AND recipient_user_id = public.app_current_user_id()
);

-- Limit the application role to changing only is_read. The RLS policy above
-- independently limits which notification rows can be updated.
REVOKE UPDATE ON TABLE public.notifications FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_app') THEN
        EXECUTE 'REVOKE UPDATE ON TABLE public.notifications FROM ticket_app';
        EXECUTE 'GRANT UPDATE (is_read) ON TABLE public.notifications TO ticket_app';
    END IF;
END;
$$;

COMMIT;
