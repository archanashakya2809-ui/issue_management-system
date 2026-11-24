-- V022 rollback.
-- IMPORTANT: first deploy the pre-V022 application code. The V022 application
-- expects this table and cannot run after it is dropped.
-- Existing user_sessions.session_token_hash values were retained for rollback.

BEGIN;
DROP TABLE IF EXISTS public.user_session_access_tokens;
COMMIT;
