-- P1.4: Enforce refresh-token/session company and user identity consistency.
-- PostgreSQL 18 compatible. Review on staging before production.
-- This migration does NOT modify user/session rows or ticket data.
-- It does NOT change app_current_user_id(), RLS policies, or V012/V014 objects.

BEGIN;

LOCK TABLE public.user_sessions IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.user_refresh_tokens IN SHARE ROW EXCLUSIVE MODE;

-- Fail safely if existing refresh-token records do not match their sessions.
DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.user_refresh_tokens AS r
        LEFT JOIN public.user_sessions AS s
          ON s.id = r.session_id
        WHERE s.id IS NULL
           OR s.user_id IS DISTINCT FROM r.user_id
           OR s.company_id IS DISTINCT FROM r.company_id
    ) THEN
        RAISE EXCEPTION
            'P1.4 migration stopped: one or more refresh-token rows reference a missing or mismatched user/company session';
    END IF;
END
$migration$;

-- PostgreSQL requires a unique/primary-key constraint on the exact referenced
-- column set for a composite foreign key. id is already a PK, but this
-- explicit composite key is required to bind the session to its tenant/user.
DO $migration$
DECLARE
    v_definition text;
BEGIN
    SELECT pg_get_constraintdef(c.oid)
      INTO v_definition
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.user_sessions'::regclass
      AND c.conname = 'uq_user_sessions_company_user_id';

    IF v_definition IS NULL THEN
        ALTER TABLE public.user_sessions
            ADD CONSTRAINT uq_user_sessions_company_user_id
            UNIQUE (company_id, user_id, id);
    ELSIF replace(v_definition, ' ', '') NOT IN (
        'UNIQUE(company_id,user_id,id)',
        'UNIQUE(company_id,user_id,id)NULLSNOTDISTINCT'
    ) THEN
        RAISE EXCEPTION
            'Constraint uq_user_sessions_company_user_id already exists with unexpected definition: %',
            v_definition;
    END IF;
END
$migration$;

-- Replace the old single-column FK with the composite identity FK.
DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS c
        WHERE c.conrelid = 'public.user_refresh_tokens'::regclass
          AND c.conname = 'fk_refresh_tokens_session_identity'
    ) THEN
        IF EXISTS (
            SELECT 1
            FROM pg_constraint AS c
            WHERE c.conrelid = 'public.user_refresh_tokens'::regclass
              AND c.conname = 'user_refresh_tokens_session_id_fkey'
        ) THEN
            ALTER TABLE public.user_refresh_tokens
                DROP CONSTRAINT user_refresh_tokens_session_id_fkey;
        END IF;

        ALTER TABLE public.user_refresh_tokens
            ADD CONSTRAINT fk_refresh_tokens_session_identity
            FOREIGN KEY (company_id, user_id, session_id)
            REFERENCES public.user_sessions (company_id, user_id, id)
            ON DELETE RESTRICT;
    ELSE
        -- Remove the redundant old FK if a prior run left both constraints.
        IF EXISTS (
            SELECT 1
            FROM pg_constraint AS c
            WHERE c.conrelid = 'public.user_refresh_tokens'::regclass
              AND c.conname = 'user_refresh_tokens_session_id_fkey'
        ) THEN
            ALTER TABLE public.user_refresh_tokens
                DROP CONSTRAINT user_refresh_tokens_session_id_fkey;
        END IF;
    END IF;
END
$migration$;

COMMIT;
