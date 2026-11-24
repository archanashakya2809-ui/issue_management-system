-- ============================================================
-- V020 rollback: System Admin platform notification event logic
--
-- Removes V020 triggers/functions and revokes monitor function access.
-- Existing notification rows and source_company_id are intentionally kept.
-- Keeping stored records preserves the System Admin notification history.
-- ============================================================

BEGIN;

DROP TRIGGER IF EXISTS trg_notify_system_admin_tenant_created ON public.companies;
DROP TRIGGER IF EXISTS trg_notify_system_admin_tenant_updated ON public.companies;
DROP TRIGGER IF EXISTS trg_notify_system_admin_platform_settings ON public.platform_settings;
DROP TRIGGER IF EXISTS trg_notify_system_admin_audit_event ON public.audit_logs;
DROP TRIGGER IF EXISTS trg_notify_system_admin_user_security_update ON public.users;
DROP TRIGGER IF EXISTS trg_notify_system_admin_privileged_role_insert ON public.user_role_assignments;
DROP TRIGGER IF EXISTS trg_notify_system_admin_privileged_role_update ON public.user_role_assignments;
DROP TRIGGER IF EXISTS trg_notify_system_admin_role_permission_insert ON public.role_permissions;
DROP TRIGGER IF EXISTS trg_notify_system_admin_role_permission_delete ON public.role_permissions;

REVOKE EXECUTE ON FUNCTION public.emit_system_admin_health_notification(text, text, text, text, uuid)
    FROM ticket_app;

DROP FUNCTION IF EXISTS public.notify_system_admin_role_permission_change();
DROP FUNCTION IF EXISTS public.notify_system_admin_privileged_role_change();
DROP FUNCTION IF EXISTS public.notify_system_admin_user_security_event();
DROP FUNCTION IF EXISTS public.notify_system_admin_audit_event();
DROP FUNCTION IF EXISTS public.notify_system_admin_platform_settings();
DROP FUNCTION IF EXISTS public.notify_system_admin_tenant_lifecycle();
DROP FUNCTION IF EXISTS public.emit_system_admin_health_notification(text, text, text, text, uuid);
DROP FUNCTION IF EXISTS public.emit_system_admin_notification(varchar, varchar, text, text, uuid, varchar, uuid, uuid);
DROP FUNCTION IF EXISTS public.system_admin_notification_recipients(uuid);

COMMIT;
