-- P1.4 read-only verification. Run after applying P1.4__user_refresh_tokens.sql.

SELECT
    c.relname AS table_name,
    pg_get_userbyid(c.relowner) AS table_owner,
    c.relrowsecurity AS rls_enabled,
    c.relforcerowsecurity AS force_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname = 'user_refresh_tokens';

SELECT
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'user_refresh_tokens'
ORDER BY ordinal_position;

SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'user_refresh_tokens'
ORDER BY indexname;

SELECT
    policyname,
    roles,
    cmd,
    qual,
    with_check
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename = 'user_refresh_tokens'
ORDER BY policyname;

SELECT
    grantee,
    privilege_type,
    is_grantable
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name = 'user_refresh_tokens'
ORDER BY grantee, privilege_type;

SELECT
    count(*) AS refresh_token_rows,
    count(*) FILTER (WHERE used_at IS NULL AND revoked_at IS NULL AND expires_at > now()) AS active_refresh_tokens,
    count(*) FILTER (WHERE used_at IS NOT NULL) AS rotated_tokens,
    count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked_tokens,
    count(*) FILTER (WHERE expires_at <= now()) AS expired_tokens
FROM public.user_refresh_tokens;
