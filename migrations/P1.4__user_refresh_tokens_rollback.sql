-- P1.4 rollback. Destructive to refresh-session continuity; this does NOT
-- delete or modify existing public.user_sessions. All users must sign in again.
-- Do not run while refresh tokens need to remain valid.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.user_refresh_tokens') IS NULL THEN
        RAISE NOTICE 'public.user_refresh_tokens does not exist; nothing to roll back';
        RETURN;
    END IF;

    IF EXISTS (SELECT 1 FROM public.user_refresh_tokens) THEN
        RAISE EXCEPTION
            'Rollback blocked: user_refresh_tokens contains rows. Confirm all users may be forced to sign in again, then archive and remove rows deliberately.';
    END IF;
END;
$$;

DROP TABLE IF EXISTS public.user_refresh_tokens;

COMMIT;
