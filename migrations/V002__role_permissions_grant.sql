-- Migration: V002__role_permissions_grant
-- Purpose : Grant INSERT and DELETE on role_permissions to ticket_app so that
--           the FastAPI admin endpoints can manage role-permission assignments.
--           Authorization is enforced in the application layer:
--             - require_permission("role.manage") guards every endpoint
--             - admin_service.py prevents COMPANY_ADMIN from modifying
--               SYSTEM_ADMIN role or granting system-level permissions
-- Safe to re-run: GRANT is idempotent.

GRANT INSERT, DELETE ON public.role_permissions TO ticket_app;

-- Verification
DO $$
DECLARE
    has_insert boolean;
    has_delete boolean;
BEGIN
    SELECT
        bool_or(privilege_type = 'INSERT'),
        bool_or(privilege_type = 'DELETE')
    INTO has_insert, has_delete
    FROM information_schema.role_table_grants
    WHERE grantee = 'ticket_app'
      AND table_schema = 'public'
      AND table_name = 'role_permissions';

    IF NOT has_insert OR NOT has_delete THEN
        RAISE EXCEPTION 'V002 check failed: ticket_app missing INSERT or DELETE on role_permissions';
    END IF;

    RAISE NOTICE 'V002 applied: ticket_app has INSERT and DELETE on role_permissions.';
END;
$$;
