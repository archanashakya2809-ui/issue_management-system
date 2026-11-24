-- Migration: V001__ticket_create_all_roles
-- Purpose : Grant ticket.create to all six authorized roles so that any
--           authenticated, active user with a role and the required domain
--           access can file a ticket for their own issue.
--           Also fixes seed-data gaps (domain access for IT team members,
--           department assignment for the company admin test user) that
--           are required for the full test suite to pass.
--
-- Safe to re-run: every INSERT is guarded by NOT EXISTS.
-- Requires: psql (or any SQL client) connected as a user with INSERT
--           privileges on role_permissions, user_domains, and UPDATE on
--           users.  The ticket_app role does NOT have these privileges;
--           run as the DBA or postgres superuser.
--
-- Rollback: run V001__ticket_create_all_roles_rollback.sql

BEGIN;

-- ── 1. Permission mapping ─────────────────────────────────────────────────────
-- Add ticket.create to the five roles that are currently missing it.
-- EMPLOYEE already has it; the INSERT guard prevents duplication.

INSERT INTO public.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM   public.roles       r
CROSS JOIN public.permissions p
WHERE  r.code  IN ('SUPPORT_AGENT', 'TEAM_LEAD', 'DEPARTMENT_MANAGER',
                   'COMPANY_ADMIN',  'SYSTEM_ADMIN')
  AND  p.code  =  'ticket.create'
  AND  r.is_active = TRUE
  AND  p.is_active = TRUE
  AND  NOT EXISTS (
          SELECT 1
          FROM   public.role_permissions rp2
          WHERE  rp2.role_id      = r.id
            AND  rp2.permission_id = p.id
       );

-- ── 2. Domain access for IT team members ─────────────────────────────────────
-- SUPPORT_AGENT and TEAM_LEAD use domain_access_mode = 'assigned', so they
-- need an explicit user_domains row.  Bandana and Disu work in the IT Support
-- team (team 41111111-…) which handles the IT Infrastructure domain.
-- Without this grant app_user_can_create_ticket returns FALSE for them.

INSERT INTO public.user_domains (company_id, user_id, domain_id)
SELECT vals.company_id::uuid, vals.user_id::uuid, vals.domain_id::uuid
FROM (VALUES
    -- Bandana – Support Agent in IT Support team
    ('22222222-2222-2222-2222-222222222222',
     '71111111-1111-1111-1111-111111111112',
     '51111111-1111-1111-1111-111111111111'),
    -- Disu – Team Lead in IT Support team
    ('22222222-2222-2222-2222-222222222222',
     '71111111-1111-1111-1111-111111111113',
     '51111111-1111-1111-1111-111111111111')
) AS vals(company_id, user_id, domain_id)
WHERE NOT EXISTS (
    SELECT 1
    FROM   public.user_domains ud
    WHERE  ud.company_id = vals.company_id::uuid
      AND  ud.user_id    = vals.user_id::uuid
      AND  ud.domain_id  = vals.domain_id::uuid
      AND  ud.revoked_at IS NULL
);

-- ── 3. Department assignment for company-admin test user ─────────────────────
-- Rahul (COMPANY_ADMIN) has department_id = NULL.  tickets.requester_department_id
-- is NOT NULL, and create_ticket() guards against a NULL department before
-- attempting the INSERT.  Assign him to the Information Technology department
-- so the COMPANY_ADMIN test case can run.
--
-- Preconditions checked by guard_user_department_change trigger:
--   • Rahul has no active team memberships          (confirmed: none)
--   • Rahul has no active dept/team-scoped roles    (confirmed: scope = company)
--   • Target department belongs to same company     (ACME/IT confirmed)
--   • Target department is active                   (confirmed)

UPDATE public.users
SET    department_id = '21111111-1111-1111-1111-111111111111'  -- Information Technology, ACME
WHERE  id            = '71111111-1111-1111-1111-111111111115'  -- Rahul (COMPANY_ADMIN)
  AND  company_id    = '22222222-2222-2222-2222-222222222222'  -- ACME Corp guard
  AND  department_id IS NULL;                                  -- idempotent

-- ── Verification snapshot ─────────────────────────────────────────────────────
DO $$
DECLARE
    missing_count  int;
    domain_count   int;
    rahul_dept     uuid;
BEGIN
    -- 1. All six roles must now have ticket.create
    SELECT COUNT(*)
    INTO   missing_count
    FROM   public.roles r
    WHERE  r.is_active = TRUE
      AND  NOT EXISTS (
               SELECT 1
               FROM   public.role_permissions rp
               JOIN   public.permissions p ON p.id = rp.permission_id
               WHERE  rp.role_id = r.id AND p.code = 'ticket.create'
           );

    IF missing_count > 0 THEN
        RAISE EXCEPTION 'Migration sanity check failed: % role(s) still missing ticket.create',
                        missing_count;
    END IF;

    -- 2. Bandana and Disu must have active IT Infrastructure domain access
    SELECT COUNT(*)
    INTO   domain_count
    FROM   public.user_domains ud
    WHERE  ud.user_id   IN ('71111111-1111-1111-1111-111111111112',
                             '71111111-1111-1111-1111-111111111113')
      AND  ud.domain_id  = '51111111-1111-1111-1111-111111111111'
      AND  ud.revoked_at IS NULL;

    IF domain_count < 2 THEN
        RAISE EXCEPTION 'Migration sanity check failed: expected 2 domain grants, got %',
                        domain_count;
    END IF;

    -- 3. Rahul must have a department
    SELECT department_id
    INTO   rahul_dept
    FROM   public.users
    WHERE  id = '71111111-1111-1111-1111-111111111115';

    IF rahul_dept IS NULL THEN
        RAISE EXCEPTION 'Migration sanity check failed: Rahul still has no department';
    END IF;

    RAISE NOTICE 'V001 sanity checks passed.';
END;
$$;

COMMIT;
