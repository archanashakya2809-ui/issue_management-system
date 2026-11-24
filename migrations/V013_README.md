# V013 — Cross-department authorization, lookup visibility and ticket visibility

## Why V013 is required

V011 separates requester home department from destination department. The original database rules still restricted Employee domain access and defaulted department-scoped visibility to the requester department. V013 updates those rules for the explicitly agreed same-company cross-department workflow.

## Changes

- Adds `app_user_can_select_ticket_destination(company_id)`, a narrowly scoped SECURITY DEFINER helper for active Employee accounts with `ticket.create` permission in their own company.
- Updates `app_user_can_create_ticket(...)`: the requester identity and `requester_department_id = users.department_id` check remain unchanged, but an Employee with `ticket.create` can create for any active company domain. Other roles retain their existing domain access restrictions.
- Adds `app_ticket_destination_is_valid(...)` and requires it in the ticket INSERT RLS policy. It validates the active destination department, domain/dept relationship, category/domain relationship and company priority.
- Allows authorized Employees to read active destination departments, domains and categories in their own company for cascading ticket lookups.
- Updates `app_ticket_access(...)` and `app_user_role_scope_matches_ticket(...)` to use `destination_department_id` (rather than requester home department) when no active team assignment exists. Employees can still view/comment on their own tickets when their Employee role is not assigned to every destination domain. Existing SYSTEM_ADMIN-only system-scope checks, active-team checks, and company isolation checks are preserved.

## Order

1. V011 — destination column and responsibility table.
2. V012 — automatic team/agent/lead/manager routing.
3. V013 — destination validation, Employee lookup access and ticket visibility.

## Safety notes

- The authenticated company/user are derived from existing transaction-local application context; no user/company ID is accepted from the frontend for authorization.
- `requester_department_id` still must match the authenticated user's home department, as enforced by the original creation helper.
- No cross-company destination is permitted.
- This migration is a draft and has not been executed by PostgreSQL in this workspace. Test with `ticket_app` and real RLS before production.


## Compatibility correction for supplied deployment-security v2.8

The supplied deployment-security script defines and grants `public.app_user_has_permission_in_scope(uuid, varchar, uuid, uuid)`. It does not define `public.app_has_permission_in_scope(...)`. The V013 draft was corrected to use the actual helper name in its preflight and department/domain lookup policies. The rollback script uses the same actual helper name. Do not create an alias or weaken the permission checks.
