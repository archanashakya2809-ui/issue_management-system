-- V016: Tenant-scoped Company Settings.
-- Platform-wide settings remain in public.platform_settings (V015).
-- This migration creates one settings row per tenant company.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_security_owner') THEN
        RAISE EXCEPTION 'V016 prerequisite missing: role ticket_security_owner';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION 'V016 prerequisite missing: role ticket_app';
    END IF;
    IF to_regprocedure('public.app_has_permission_in_company(uuid,character varying)') IS NULL THEN
        RAISE EXCEPTION 'V016 prerequisite missing: app_has_permission_in_company(uuid, varchar)';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.permissions
        WHERE code = 'company.settings.manage' AND is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'V016 prerequisite missing: active permission company.settings.manage';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.roles
        WHERE code = 'COMPANY_ADMIN' AND is_active = TRUE
    ) THEN
        RAISE EXCEPTION 'V016 prerequisite missing: active role COMPANY_ADMIN';
    END IF;
END;
$$;

CREATE TABLE public.company_settings (
    company_id              uuid PRIMARY KEY,
    display_name            varchar(150) NOT NULL,
    timezone                varchar(64) NOT NULL DEFAULT 'Asia/Kolkata',
    default_sla_hours       integer NOT NULL DEFAULT 48,
    reply_to                varchar(254) NOT NULL DEFAULT '',
    notify_company_admins   boolean NOT NULL DEFAULT TRUE,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_company_settings_company
        FOREIGN KEY (company_id)
        REFERENCES public.companies(id)
        ON DELETE RESTRICT,
    CONSTRAINT chk_company_settings_display_name
        CHECK (char_length(btrim(display_name)) BETWEEN 1 AND 150),
    CONSTRAINT chk_company_settings_sla
        CHECK (default_sla_hours BETWEEN 1 AND 8760),
    CONSTRAINT chk_company_settings_timezone
        CHECK (char_length(btrim(timezone)) BETWEEN 1 AND 64),
    CONSTRAINT chk_company_settings_reply_to_length
        CHECK (char_length(reply_to) <= 254)
);

COMMENT ON TABLE public.company_settings IS
    'Tenant-level configuration maintained by authorized Company Admins. Platform settings are stored separately.';

-- Provision defaults for all existing tenant companies. A newly created
-- tenant receives equivalent defaults from the GET endpoint until first save.
INSERT INTO public.company_settings (
    company_id, display_name, timezone, default_sla_hours,
    reply_to, notify_company_admins
)
SELECT
    c.id, c.name, 'Asia/Kolkata', 48, '', TRUE
FROM public.companies c
WHERE c.is_platform = FALSE
ON CONFLICT (company_id) DO NOTHING;

-- Company Admin receives this permission. No other role is granted it here.
INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
CROSS JOIN public.permissions p
WHERE r.code = 'COMPANY_ADMIN'
  AND p.code = 'company.settings.manage'
  AND r.is_active = TRUE
  AND p.is_active = TRUE
ON CONFLICT DO NOTHING;

ALTER TABLE public.company_settings OWNER TO ticket_security_owner;
ALTER TABLE public.company_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY company_settings_select_policy
ON public.company_settings
FOR SELECT
USING (
    public.app_has_permission_in_company(
        company_id, 'company.settings.manage'::varchar
    )
);

CREATE POLICY company_settings_insert_policy
ON public.company_settings
FOR INSERT
WITH CHECK (
    public.app_has_permission_in_company(
        company_id, 'company.settings.manage'::varchar
    )
);

CREATE POLICY company_settings_update_policy
ON public.company_settings
FOR UPDATE
USING (
    public.app_has_permission_in_company(
        company_id, 'company.settings.manage'::varchar
    )
)
WITH CHECK (
    public.app_has_permission_in_company(
        company_id, 'company.settings.manage'::varchar
    )
);

REVOKE ALL ON TABLE public.company_settings FROM PUBLIC;
GRANT SELECT, INSERT ON TABLE public.company_settings TO ticket_app;
GRANT UPDATE (
    display_name,
    timezone,
    default_sla_hours,
    reply_to,
    notify_company_admins,
    updated_at
) ON TABLE public.company_settings TO ticket_app;

COMMIT;
