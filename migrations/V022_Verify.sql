-- V022 read-only verification. Run after applying V022.

SELECT
    c.relname AS table_name,
    pg_get_userbyid(c.relowner) AS table_owner,
    c.relrowsecurity AS rls_enabled,
    c.relforcerowsecurity AS force_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname = 'user_session_access_tokens';

SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'user_session_access_tokens'
ORDER BY ordinal_position;

SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.user_session_access_tokens'::regclass
ORDER BY conname;

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'user_session_access_tokens'
ORDER BY indexname;

SELECT policyname, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename = 'user_session_access_tokens'
ORDER BY policyname;

SELECT grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name = 'user_session_access_tokens'
ORDER BY grantee, privilege_type;

SELECT
    (SELECT count(*) FROM public.user_sessions) AS session_rows,
    (SELECT count(*) FROM public.user_session_access_tokens) AS access_token_rows,
    (SELECT count(*) FROM public.user_sessions s
      WHERE NOT EXISTS (
          SELECT 1 FROM public.user_session_access_tokens a
          WHERE a.session_id = s.id
            AND a.token_hash = s.session_token_hash
      )) AS sessions_missing_legacy_token_backfill,
    (SELECT count(*) FROM public.user_session_access_tokens
      WHERE revoked_at IS NULL AND expires_at > now()) AS currently_valid_access_token_rows;
