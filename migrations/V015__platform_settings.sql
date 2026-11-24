-- V015: Persist platform-wide System Admin settings.
-- Requires PostgreSQL 15+ and the existing ticket_security_owner/ticket_app roles.
-- Applies only to the single row associated with the platform company.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_security_owner') THEN
        RAISE EXCEPTION 'V015 prerequisite missing: role ticket_security_owner';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION 'V015 prerequisite missing: role ticket_app';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.companies WHERE is_platform = TRUE) THEN
        RAISE EXCEPTION 'V015 prerequisite missing: platform company';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.permissions WHERE code = 'system.config.manage' AND is_active = TRUE) THEN
        RAISE EXCEPTION 'V015 prerequisite missing: active permission system.config.manage';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM public.roles r
        JOIN public.role_permissions rp ON rp.role_id = r.id
        JOIN public.permissions p ON p.id = rp.permission_id
        WHERE r.code = 'SYSTEM_ADMIN' AND r.is_active = TRUE
          AND p.code = 'system.config.manage' AND p.is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'V015 prerequisite missing: SYSTEM_ADMIN must have system.config.manage';
    END IF;
END;
$$;

CREATE TABLE public.platform_settings (
    company_id              uuid PRIMARY KEY,
    app_name                varchar(150) NOT NULL,
    default_sla_hours       integer NOT NULL DEFAULT 48,
    session_timeout_minutes integer NOT NULL DEFAULT 60,
    max_login_attempts      integer NOT NULL DEFAULT 5,
    lockout_minutes         integer NOT NULL DEFAULT 30,
    password_policy         varchar(20) NOT NULL DEFAULT 'Strong',
    maintenance_mode        boolean NOT NULL DEFAULT FALSE,
    email_notifications     boolean NOT NULL DEFAULT TRUE,
    system_alerts           boolean NOT NULL DEFAULT TRUE,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_platform_settings_platform_company
        FOREIGN KEY (company_id) REFERENCES public.companies(id) ON DELETE RESTRICT,
    CONSTRAINT chk_platform_settings_app_name
        CHECK (char_length(btrim(app_name)) BETWEEN 1 AND 150),
    CONSTRAINT chk_platform_settings_sla
        CHECK (default_sla_hours BETWEEN 1 AND 8760),
    CONSTRAINT chk_platform_settings_session_timeout
        CHECK (session_timeout_minutes BETWEEN 5 AND 1440),
    CONSTRAINT chk_platform_settings_max_login_attempts
        CHECK (max_login_attempts BETWEEN 3 AND 20),
    CONSTRAINT chk_platform_settings_lockout_minutes
        CHECK (lockout_minutes BETWEEN 1 AND 1440),
    CONSTRAINT chk_platform_settings_password_policy
        CHECK (password_policy IN ('Standard', 'Strong', 'Strict'))
);

COMMENT ON TABLE public.platform_settings IS
    'Single platform-wide configuration row, scoped to the platform company.';

INSERT INTO public.platform_settings (
    company_id, app_name, default_sla_hours, session_timeout_minutes,
    max_login_attempts, lockout_minutes, password_policy,
    maintenance_mode, email_notifications, system_alerts
)
SELECT
    c.id, 'Ticket Management System', 48, 60, 5, 30, 'Strong', FALSE, TRUE, TRUE
FROM public.companies c
WHERE c.is_platform = TRUE;

ALTER TABLE public.platform_settings OWNER TO ticket_security_owner;
ALTER TABLE public.platform_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY platform_settings_select_policy
ON public.platform_settings
FOR SELECT
USING (public.app_has_permission_in_company(company_id, 'system.config.manage'));

CREATE POLICY platform_settings_update_policy
ON public.platform_settings
FOR UPDATE
USING (public.app_has_permission_in_company(company_id, 'system.config.manage'))
WITH CHECK (public.app_has_permission_in_company(company_id, 'system.config.manage'));

REVOKE ALL ON TABLE public.platform_settings FROM PUBLIC;
GRANT SELECT ON TABLE public.platform_settings TO ticket_app;
GRANT UPDATE (
    app_name,
    default_sla_hours,
    session_timeout_minutes,
    max_login_attempts,
    lockout_minutes,
    password_policy,
    maintenance_mode,
    email_notifications,
    system_alerts,
    updated_at
) ON TABLE public.platform_settings TO ticket_app;

COMMIT;
