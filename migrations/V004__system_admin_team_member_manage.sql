-- Migration: V004__system_admin_team_member_manage
-- Purpose : Grant team.member.manage to SYSTEM_ADMIN so that system
--           administrators can insert rows into team_members when creating
--           or updating users cross-tenant.
--
--           The team_members_insert_policy enforces:
--             app_user_has_permission_in_scope(company_id, 'team.member.manage', ...)
--           Without this grant the system-admin bypass inside that function
--           cannot fire (the permission JOIN finds no row), causing every
--           user-create request that includes a team to raise an RLS
--           violation caught as HTTP 500 "Failed to create user."
--
-- Safe to re-run: INSERT ... ON CONFLICT DO NOTHING is idempotent.

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
CROSS JOIN public.permissions p
WHERE r.code = 'SYSTEM_ADMIN'
  AND p.code = 'team.member.manage'
  AND r.is_active = TRUE
  AND p.is_active = TRUE
ON CONFLICT DO NOTHING;

-- Verification
DO $$
DECLARE
    granted_count int;
BEGIN
    SELECT COUNT(*)
    INTO   granted_count
    FROM   public.roles r
    JOIN   public.role_permissions rp ON rp.role_id = r.id
    JOIN   public.permissions p       ON p.id = rp.permission_id
    WHERE  r.code = 'SYSTEM_ADMIN'
      AND  p.code = 'team.member.manage';

    IF granted_count = 0 THEN
        RAISE EXCEPTION
            'V004 check failed: team.member.manage was not granted to SYSTEM_ADMIN';
    END IF;

    RAISE NOTICE 'V004 applied: SYSTEM_ADMIN now has team.member.manage.';
END;
$$;
