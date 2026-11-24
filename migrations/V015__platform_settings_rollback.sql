-- V015 rollback. WARNING: this deletes the persisted platform settings row.
BEGIN;
DROP POLICY IF EXISTS platform_settings_update_policy ON public.platform_settings;
DROP POLICY IF EXISTS platform_settings_select_policy ON public.platform_settings;
REVOKE ALL ON TABLE public.platform_settings FROM ticket_app;
DROP TABLE IF EXISTS public.platform_settings;
COMMIT;
