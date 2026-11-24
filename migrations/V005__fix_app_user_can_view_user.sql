-- Migration: V005__fix_app_user_can_view_user
-- Purpose : Fix app_user_can_view_user so the SYSTEM_ADMIN and self-view
--           bypasses do NOT require finding the target row in the users heap.
--
--           Root cause: when a system admin inserts a new user with RETURNING,
--           PostgreSQL evaluates the users_select_policy (USING expression) as
--           a WITH CHECK option against the RETURNING row BEFORE the AFTER
--           INSERT triggers fire. At that execution point the newly written row
--           is not yet visible to the function's own SELECT from users, so
--           `FROM users target WHERE target.id = p_target_user_id` finds nothing,
--           EXISTS returns FALSE, and the insert is rejected with
--           "new row violates row-level security policy for table users".
--
--           Fix: split the check into three UNION ALL branches so that the
--           system-admin and self-view paths never need to look up the target
--           row, and only the company/dept/team-scoped path requires it.
--
-- Safe to re-run: CREATE OR REPLACE replaces the existing function body.

CREATE OR REPLACE FUNCTION public.app_user_can_view_user(
    p_target_user_id uuid
)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = public
AS $$
    SELECT EXISTS (
        -- Branch 1: system-admin bypass — no target row needed
        SELECT 1
        FROM   public.users              actor
        JOIN   public.user_role_assignments ura
                 ON  ura.user_id      = actor.id
                 AND ura.revoked_at   IS NULL
                 AND ura.scope_type   = 'system'
        JOIN   public.roles              r
                 ON  r.id             = ura.role_id
                 AND r.code           = 'SYSTEM_ADMIN'
                 AND r.is_active      = TRUE
        JOIN   public.role_permissions   rp  ON rp.role_id    = r.id
        JOIN   public.permissions        p
                 ON  p.id             = rp.permission_id
                 AND p.code           = 'user.view'
                 AND p.is_active      = TRUE
        WHERE  actor.id     = app_current_user_id()
          AND  actor.status = 'active'

        UNION ALL

        -- Branch 2: self-view bypass — no target row needed
        SELECT 1
        WHERE  p_target_user_id = app_current_user_id()

        UNION ALL

        -- Branch 3: company / department / team scoped access — requires target row
        SELECT 1
        FROM   public.users              target
        JOIN   public.users              actor
                 ON  actor.id      = app_current_user_id()
                 AND actor.status  = 'active'
        JOIN   public.user_role_assignments ura
                 ON  ura.user_id     = actor.id
                 AND ura.revoked_at  IS NULL
                 AND ura.scope_type  <> 'system'
        JOIN   public.roles              r
                 ON  r.id            = ura.role_id
                 AND r.is_active     = TRUE
        JOIN   public.role_permissions   rp  ON rp.role_id    = r.id
        JOIN   public.permissions        p
                 ON  p.id            = rp.permission_id
                 AND p.code          = 'user.view'
                 AND p.is_active     = TRUE
        LEFT JOIN public.team_members    target_tm
                 ON  target_tm.company_id = target.company_id
                 AND target_tm.user_id    = target.id
                 AND target_tm.left_at    IS NULL
        WHERE  target.id            = p_target_user_id
          AND  ura.company_id       = target.company_id
          AND  (
                   ura.scope_type   = 'company'
                OR (
                       ura.scope_type         = 'department'
                   AND target.department_id   = ura.scope_department_id
                   )
                OR (
                       ura.scope_type         = 'team'
                   AND target_tm.team_id      = ura.scope_team_id
                   )
               )
    );
$$;

-- Verify the function was replaced
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.proname = 'app_user_can_view_user'
          AND n.nspname = 'public'
    ) THEN
        RAISE EXCEPTION 'V005 check failed: app_user_can_view_user not found';
    END IF;
    RAISE NOTICE 'V005 applied: app_user_can_view_user updated with split-branch system-admin bypass.';
END;
$$;
