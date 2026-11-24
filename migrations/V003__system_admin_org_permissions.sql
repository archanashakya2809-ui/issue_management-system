-- Migration: V003__system_admin_org_permissions
-- Purpose : Grant organisation-management permissions (department.manage,
--           team.manage, domain.manage) to the SYSTEM_ADMIN role so that
--           platform administrators can create, edit and deactivate
--           departments, teams, domains and categories for any tenant
--           through the /system/org/* API endpoints.
--
--           These permissions already exist in the permissions table and
--           are governed by the same RLS helper
--           app_user_has_permission_in_scope(), which already contains
--           an explicit system-scope (ura.scope_type = 'system' AND
--           r.code = 'SYSTEM_ADMIN') bypass — no new DB logic is required.
--
-- Safe to re-run: INSERT ... ON CONFLICT DO NOTHING is idempotent.

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM public.roles r
CROSS JOIN public.permissions p
WHERE r.code = 'SYSTEM_ADMIN'
  AND p.code IN ('department.manage', 'team.manage', 'domain.manage')
ON CONFLICT DO NOTHING;

-- Verification
DO $$
DECLARE
    expected_count int := 3;
    actual_count   int;
BEGIN
    SELECT COUNT(*)
    INTO   actual_count
    FROM   public.roles r
    JOIN   public.role_permissions rp ON rp.role_id = r.id
    JOIN   public.permissions p       ON p.id = rp.permission_id
    WHERE  r.code = 'SYSTEM_ADMIN'
      AND  p.code IN ('department.manage', 'team.manage', 'domain.manage');

    IF actual_count < expected_count THEN
        RAISE EXCEPTION
            'V003 check failed: expected % org permissions for SYSTEM_ADMIN, found %',
            expected_count, actual_count;
    END IF;

    RAISE NOTICE 'V003 applied: SYSTEM_ADMIN now has department.manage, team.manage, domain.manage.';
END;
$$;
