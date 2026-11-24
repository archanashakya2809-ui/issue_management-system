-- V022 read-only preflight. Run this against the LIVE database before applying V022.
-- It does not modify any database objects or data.

-- 1. Required tables and session identity constraint.
SELECT to_regclass('public.user_sessions') AS user_sessions_table,
       to_regclass('public.user_refresh_tokens') AS user_refresh_tokens_table,
       to_regclass('public.user_session_access_tokens') AS access_token_table_if_already_present;

SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
FROM pg_constraint c
WHERE c.conrelid = 'public.user_sessions'::regclass
  AND c.conname IN ('user_sessions_pkey', 'uq_user_sessions_company_user_id', 'uq_user_sessions_token')
ORDER BY c.conname;

-- 2. Existing session-token hashes must be SHA-256 lowercase hex digests.
SELECT count(*) AS session_count,
       count(*) FILTER (WHERE session_token_hash ~ '^[0-9a-f]{64}$') AS valid_sha256_hashes,
       count(*) FILTER (WHERE session_token_hash !~ '^[0-9a-f]{64}$') AS invalid_hashes
FROM public.user_sessions;

-- 3. Verify current refresh-token/session identity consistency.
SELECT count(*) AS refresh_token_rows,
       count(*) FILTER (WHERE s.id IS NULL) AS missing_sessions,
       count(*) FILTER (
           WHERE s.id IS NOT NULL
             AND (s.company_id IS DISTINCT FROM r.company_id
                  OR s.user_id IS DISTINCT FROM r.user_id)
       ) AS mismatched_session_identity
FROM public.user_refresh_tokens r
LEFT JOIN public.user_sessions s ON s.id = r.session_id;

-- 4. Existing RLS state and policies.
SELECT c.relname, c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS force_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('user_sessions', 'user_refresh_tokens')
ORDER BY c.relname;

SELECT tablename, policyname, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('user_sessions', 'user_refresh_tokens')
ORDER BY tablename, policyname;

-- 5. Required database roles and helper function.
SELECT rolname
FROM pg_roles
WHERE rolname IN ('ticket_security_owner', 'ticket_app')
ORDER BY rolname;

SELECT to_regprocedure('public.app_current_user_id()') AS current_user_helper;

-- 6. Current session rows to be backfilled. The V022 migration copies one row
-- per user_sessions record, preserving issued_at, expires_at and revoked_at.
SELECT count(*) AS expected_backfill_rows,
       count(*) FILTER (WHERE revoked_at IS NULL AND expires_at > now()) AS active_session_rows,
       count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked_session_rows,
       count(*) FILTER (WHERE expires_at <= now()) AS expired_session_rows
FROM public.user_sessions;
