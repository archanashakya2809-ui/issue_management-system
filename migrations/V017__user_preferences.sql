-- V017: Persist personal notification and appearance preferences.
-- One row per tenant user. Users can read/update only their own row.
-- This migration is separate from company_settings and platform_settings.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_security_owner') THEN
        RAISE EXCEPTION 'V017 prerequisite missing: role ticket_security_owner';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION 'V017 prerequisite missing: role ticket_app';
    END IF;
    IF to_regclass('public.users') IS NULL THEN
        RAISE EXCEPTION 'V017 prerequisite missing: public.users';
    END IF;
    IF to_regprocedure('public.app_current_user_id()') IS NULL THEN
        RAISE EXCEPTION 'V017 prerequisite missing: public.app_current_user_id()';
    END IF;
END;
$$;

CREATE TABLE public.user_preferences (
    company_id       uuid        NOT NULL,
    user_id          uuid        NOT NULL,
    in_app           boolean     NOT NULL DEFAULT TRUE,
    email            boolean     NOT NULL DEFAULT TRUE,
    weekly_summary   boolean     NOT NULL DEFAULT FALSE,
    compact_tables   boolean     NOT NULL DEFAULT FALSE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT pk_user_preferences
        PRIMARY KEY (company_id, user_id),
    CONSTRAINT fk_user_preferences_user
        FOREIGN KEY (company_id, user_id)
        REFERENCES public.users (company_id, id)
        ON DELETE CASCADE
);

COMMENT ON TABLE public.user_preferences IS
    'Personal notification and appearance preferences; one row per tenant user.';

ALTER TABLE public.user_preferences OWNER TO ticket_security_owner;
ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_preferences FORCE ROW LEVEL SECURITY;

CREATE POLICY user_preferences_select_own_policy
ON public.user_preferences
FOR SELECT
USING (user_id = public.app_current_user_id());

CREATE POLICY user_preferences_insert_own_policy
ON public.user_preferences
FOR INSERT
WITH CHECK (user_id = public.app_current_user_id());

CREATE POLICY user_preferences_update_own_policy
ON public.user_preferences
FOR UPDATE
USING (user_id = public.app_current_user_id())
WITH CHECK (user_id = public.app_current_user_id());

REVOKE ALL ON TABLE public.user_preferences FROM PUBLIC;
GRANT SELECT, INSERT ON TABLE public.user_preferences TO ticket_app;
GRANT UPDATE (in_app, email, weekly_summary, compact_tables, updated_at)
ON TABLE public.user_preferences TO ticket_app;
GRANT EXECUTE ON FUNCTION public.app_current_user_id() TO ticket_app;

COMMIT;
