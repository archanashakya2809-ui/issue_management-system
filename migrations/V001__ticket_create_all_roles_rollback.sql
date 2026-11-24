-- Rollback: V001__ticket_create_all_roles_rollback.sql
-- Reverts every change made by V001__ticket_create_all_roles.sql.
-- Run ONLY if the migration must be undone.  Safe to run when the migration
-- has already been partially applied.

BEGIN;

-- ── 1. Remove ticket.create from the five roles that previously lacked it ──────
-- EMPLOYEE keeps its existing ticket.create grant (it existed before V001).
DELETE FROM public.role_permissions
WHERE  permission_id = (
           SELECT id FROM public.permissions WHERE code = 'ticket.create'
       )
  AND  role_id IN (
           SELECT id FROM public.roles
           WHERE  code IN ('SUPPORT_AGENT', 'TEAM_LEAD', 'DEPARTMENT_MANAGER',
                           'COMPANY_ADMIN',  'SYSTEM_ADMIN')
       );

-- ── 2. Revoke the domain grants added for Bandana and Disu ───────────────────
-- Use revoked_at rather than DELETE to preserve history.
UPDATE public.user_domains
SET    revoked_at = now()
WHERE  user_id   IN ('71111111-1111-1111-1111-111111111112',   -- Bandana
                      '71111111-1111-1111-1111-111111111113')  -- Disu
  AND  domain_id  = '51111111-1111-1111-1111-111111111111'     -- IT Infrastructure
  AND  revoked_at IS NULL
  AND  company_id = '22222222-2222-2222-2222-222222222222';    -- ACME guard

-- ── 3. Clear Rahul's department assignment ────────────────────────────────────
-- Only clears the assignment if it was made by V001 (was previously NULL).
-- If the department was set by another migration or manually, this is a no-op.
UPDATE public.users
SET    department_id = NULL
WHERE  id         = '71111111-1111-1111-1111-111111111115'
  AND  company_id = '22222222-2222-2222-2222-222222222222'
  AND  department_id = '21111111-1111-1111-1111-111111111111';

COMMIT;
