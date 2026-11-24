# Backend development roadmap

Build one vertical slice at a time. Every slice includes route, schema, service, authorization, tests, and documentation.

## Phase 0 — database contract
- Run `python scripts/check_schema.py`.
- Run the current SQL verification script.
- Confirm `ticket_app` grants, RLS, authentication lookup, default status, and routing behavior.

## Phase 1 — health and authentication
- Liveness/readiness, login, current user, logout.
- Test session expiry/revocation and RLS context.

## Phase 2 — Employee ticket flow
- Catalog reads; create/list/view tickets; public comments.
- Add secure attachment storage and download.
- Implement reopen/confirm-resolution only after workflow and permissions are explicitly configured.

## Phase 3 — Support Agent
- My queue/team queue; assignment; internal notes; workflow transitions; escalation; SLA visibility.

## Phase 4 — Team Lead and Department Manager
- Scoped dashboards, workload reports, SLA risk, escalation oversight.
- Test aggregate/report scope, not just individual ticket scope.

## Phase 5 — Company Admin and System Admin
- Organization/user/team management, role and permission management, workflow/SLA/notification configuration.

## Phase 6 — production hardening
- Idempotency, optimistic concurrency (`tickets.version`), request IDs, observability, rate limits.
- Background workers, notification delivery, SLA scheduler, integration/load tests.
- CI, deployment runbook, backup/restore drills.
