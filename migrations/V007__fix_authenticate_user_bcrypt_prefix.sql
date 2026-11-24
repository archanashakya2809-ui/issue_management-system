-- Migration: V007__fix_authenticate_user_bcrypt_prefix
--
-- Root cause
-- ----------
-- Python's bcrypt library (>=4.x) produces $2b$ prefix hashes by default.
-- PostgreSQL pgcrypto.crypt() only recognises $2a$ prefix bcrypt hashes and
-- silently returns a non-matching result for $2b$ hashes, so every user whose
-- password was hashed by the FastAPI application via bcrypt.gensalt() could
-- not authenticate.  Seed / SQL-created users already stored $2a$ hashes and
-- were unaffected.
--
-- The $2a$ and $2b$ bcrypt variants are algorithmically identical; they differ
-- only in the prefix character.  Normalising $2b$ -> $2a$ inside the WHERE
-- clause (without modifying the stored hash) lets pgcrypto verify the password
-- correctly without touching any stored data.
--
-- Two-part fix
-- ------------
-- 1. This migration: update app_authenticate_user so it normalises the prefix
--    before calling crypt().  This repairs all existing accounts that were
--    created via the API and have a $2b$ hash stored.
--
-- 2. security.py: hash_password() is changed to use bcrypt.gensalt(prefix=b"2a")
--    so that new accounts receive a pgcrypto-compatible hash at creation time.
--
-- Safe to re-run: CREATE OR REPLACE replaces the function body idempotently.

CREATE OR REPLACE FUNCTION public.app_authenticate_user(
    p_company_code text,
    p_email        text,
    p_password     text
)
RETURNS TABLE(authenticated_user_id uuid, authenticated_company_id uuid)
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO 'pg_catalog', 'public'
AS $function$
    SELECT u.id, u.company_id
    FROM public.users    AS u
    JOIN public.companies AS c ON c.id = u.company_id
    -- Normalise $2b$ -> $2a$ so pgcrypto.crypt() can verify the hash.
    -- The two bcrypt variants are algorithmically identical; only the prefix
    -- differs.  We never rewrite the stored value.
    CROSS JOIN LATERAL (
        SELECT CASE WHEN u.password_hash LIKE '$2b$%'
                    THEN '$2a$' || substring(u.password_hash FROM 5)
                    ELSE u.password_hash
               END AS normalized_hash
    ) ph
    WHERE lower(c.code)    = lower(btrim(p_company_code))
      AND lower(u.email)   = lower(btrim(p_email))
      AND u.status         = 'active'
      AND u.deleted_at     IS NULL
      AND c.status         = 'active'
      AND public.crypt(p_password, ph.normalized_hash) = ph.normalized_hash
    LIMIT 1;
$function$;

-- Verification
DO $$
DECLARE
    v_exists boolean;
    v_wrong  text;
BEGIN
    -- 1. Function must exist after replacement
    SELECT EXISTS (
        SELECT 1
        FROM   pg_proc p
        JOIN   pg_namespace n ON n.oid = p.pronamespace
        WHERE  p.proname  = 'app_authenticate_user'
          AND  n.nspname  = 'public'
    ) INTO v_exists;

    IF NOT v_exists THEN
        RAISE EXCEPTION 'V007 failed: app_authenticate_user not found after CREATE OR REPLACE';
    END IF;

    -- 2. The normalization SQL must produce $2a$ from a $2b$ input
    SELECT CASE WHEN '$2b$12$ExampleSaltAndHash' LIKE '$2b$%'
                THEN '$2a$' || substring('$2b$12$ExampleSaltAndHash' FROM 5)
                ELSE '$2b$12$ExampleSaltAndHash'
           END INTO v_wrong;

    IF v_wrong != '$2a$12$ExampleSaltAndHash' THEN
        RAISE EXCEPTION 'V007 failed: prefix normalisation logic returned unexpected value: %', v_wrong;
    END IF;

    RAISE NOTICE 'V007 applied: app_authenticate_user updated to handle $2b$ bcrypt prefix.';
END;
$$;
