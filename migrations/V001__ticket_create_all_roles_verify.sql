-- Verification: V001__ticket_create_all_roles_verify.sql
-- Run after the migration to confirm the state is correct.
-- Read-only; safe to run multiple times with no side effects.

-- 1. All six roles should have ticket.create
SELECT r.code         AS role,
       p.code         AS permission,
       r.is_active    AS role_active,
       p.is_active    AS perm_active
FROM   public.roles r
JOIN   public.role_permissions rp ON rp.role_id = r.id
JOIN   public.permissions p       ON p.id = rp.permission_id
WHERE  p.code = 'ticket.create'
ORDER BY r.code;
-- Expected: 6 rows (COMPANY_ADMIN, DEPARTMENT_MANAGER, EMPLOYEE,
--                   SUPPORT_AGENT, SYSTEM_ADMIN, TEAM_LEAD)

-- 2. Roles still MISSING ticket.create (should return 0 rows)
SELECT r.code AS role_missing_ticket_create
FROM   public.roles r
WHERE  r.is_active = TRUE
  AND  NOT EXISTS (
           SELECT 1
           FROM   public.role_permissions rp
           JOIN   public.permissions p ON p.id = rp.permission_id
           WHERE  rp.role_id = r.id AND p.code = 'ticket.create'
       );
-- Expected: 0 rows

-- 3. Domain access for IT team members
SELECT u.first_name, u.email, d.name AS domain_name, ud.revoked_at
FROM   public.user_domains ud
JOIN   public.users   u ON u.id  = ud.user_id
JOIN   public.domains d ON d.id  = ud.domain_id
WHERE  ud.user_id IN ('71111111-1111-1111-1111-111111111112',   -- Bandana
                       '71111111-1111-1111-1111-111111111113')  -- Disu
  AND  ud.revoked_at IS NULL;
-- Expected: 2 rows, both for IT Infrastructure

-- 4. Rahul's department assignment
SELECT first_name, email, department_id
FROM   public.users
WHERE  id = '71111111-1111-1111-1111-111111111115';
-- Expected: department_id = 21111111-1111-1111-1111-111111111111

-- 5. Snapshot: ticket.create + scope matrix across test users
--    (run as ticket_app with set_config('app.user_id', ...) already set)
SELECT u.first_name,
       r.code          AS role_code,
       ura.scope_type,
       u.department_id,
       p.code          AS has_perm
FROM   public.users u
JOIN   public.user_role_assignments ura ON ura.user_id = u.id AND ura.revoked_at IS NULL
JOIN   public.roles r                   ON r.id = ura.role_id
JOIN   public.role_permissions rp       ON rp.role_id = r.id
JOIN   public.permissions p             ON p.id = rp.permission_id AND p.code = 'ticket.create'
WHERE  u.email IN (
           'archana.employee@example.com',
           'bandana.support@example.com',
           'disu.lead@example.com',
           'gautam.manager@example.com',
           'rahul.admin@example.com',
           'indu.system@example.com'
       )
ORDER BY u.first_name;
-- Expected: 6 rows (Disu may appear twice – SUPPORT_AGENT + TEAM_LEAD both match)
