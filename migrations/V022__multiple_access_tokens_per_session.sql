-- V022: Support multiple short-lived access tokens within one login session.
-- Existing user_sessions rows and refresh-token families are preserved.
-- The legacy session_token_hash column remains temporarily for rollback compatibility.

BEGIN;

LOCK TABLE public.user_sessions IN SHARE ROW EXCLUSIVE MODE;

-- Fail closed if the required P1.4 session identity key or security roles are absent.
DO $migration$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_constraintdef(c.oid)
      INTO v_definition
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.user_sessions'::regclass
      AND c.conname = 'uq_user_sessions_company_user_id';

    IF replace(coalesce(v_definition, ''), ' ', '') NOT IN (
        'UNIQUE(company_id,user_id,id)',
        'UNIQUE(company_id,user_id,id)NULLSNOTDISTINCT'
    ) THEN
        RAISE EXCEPTION
            'V022 stopped: expected P1.4 constraint uq_user_sessions_company_user_id is missing or has an unexpected definition';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_security_owner') THEN
        RAISE EXCEPTION 'V022 stopped: role ticket_security_owner does not exist';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ticket_app') THEN
        RAISE EXCEPTION 'V022 stopped: role ticket_app does not exist';
    END IF;
    IF to_regprocedure('public.app_current_user_id()') IS NULL THEN
        RAISE EXCEPTION 'V022 stopped: public.app_current_user_id() does not exist';
    END IF;
    IF to_regclass('public.user_session_access_tokens') IS NOT NULL THEN
        RAISE EXCEPTION 'V022 stopped: public.user_session_access_tokens already exists; inspect it before retrying';
    END IF;
END
$migration$;

-- Backfill depends on the existing access-token digest being a SHA-256 hex digest.
DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.user_sessions
        WHERE session_token_hash !~ '^[0-9a-f]{64}$'
    ) THEN
        RAISE EXCEPTION
            'V022 stopped: user_sessions contains a session_token_hash that is not a 64-character lowercase SHA-256 digest';
    END IF;
END
$migration$;

CREATE TABLE public.user_session_access_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid NOT NULL,
    user_id uuid NOT NULL,
    session_id uuid NOT NULL,
    token_hash character varying(64) NOT NULL,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    CONSTRAINT user_session_access_tokens_pkey PRIMARY KEY (id),
    CONSTRAINT uq_session_access_tokens_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_session_access_tokens_hash_format
        CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_session_access_tokens_expiry
        CHECK (expires_at > issued_at),
    CONSTRAINT fk_session_access_tokens_session_identity
        FOREIGN KEY (company_id, user_id, session_id)
        REFERENCES public.user_sessions (company_id, user_id, id)
        ON DELETE RESTRICT
);

ALTER TABLE public.user_session_access_tokens OWNER TO ticket_security_owner;

COMMENT ON TABLE public.user_session_access_tokens IS
    'Hashed short-lived access JWTs. Multiple access tokens may belong to one revocable user session.';
COMMENT ON COLUMN public.user_session_access_tokens.token_hash IS
    'SHA-256 digest of the complete signed access JWT; raw JWTs are never stored.';

CREATE INDEX ix_session_access_tokens_session_expiry
    ON public.user_session_access_tokens (session_id, expires_at);
CREATE INDEX ix_session_access_tokens_active_session
    ON public.user_session_access_tokens (session_id)
    WHERE revoked_at IS NULL;
CREATE INDEX ix_session_access_tokens_user_company
    ON public.user_session_access_tokens (company_id, user_id);

-- Preserve all existing session credentials so a deployment does not force
-- every currently logged-in user to authenticate again.
INSERT INTO public.user_session_access_tokens (
    company_id, user_id, session_id, token_hash, issued_at, expires_at, revoked_at
)
SELECT
    company_id, user_id, id, session_token_hash, issued_at, expires_at, revoked_at
FROM public.user_sessions;

ALTER TABLE public.user_session_access_tokens ENABLE ROW LEVEL SECURITY;

CREATE POLICY session_access_tokens_select_policy
    ON public.user_session_access_tokens
    FOR SELECT TO ticket_app
    USING (user_id = public.app_current_user_id());

CREATE POLICY session_access_tokens_insert_policy
    ON public.user_session_access_tokens
    FOR INSERT TO ticket_app
    WITH CHECK (user_id = public.app_current_user_id());

CREATE POLICY session_access_tokens_update_policy
    ON public.user_session_access_tokens
    FOR UPDATE TO ticket_app
    USING (user_id = public.app_current_user_id())
    WITH CHECK (user_id = public.app_current_user_id());

REVOKE ALL ON TABLE public.user_session_access_tokens FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON TABLE public.user_session_access_tokens TO ticket_app;

COMMIT;
