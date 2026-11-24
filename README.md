# Ticket Management System — Backend

A clean, layered FastAPI backend starter for the existing PostgreSQL Ticket Management System.

## Included
- Liveness and database-readiness endpoints
- Login, current-user, and logout endpoints
- Revocable bearer sessions stored in `user_sessions`
- Permission dependency using the existing role/permission tables
- Ticket list, detail, create, and comment endpoints
- PostgreSQL transaction-local RLS identity (`app.user_id`)
- Read-only schema preflight script, tests, and documentation

This is a **starter implementation**, not a claim that every workflow, SLA worker, notification provider, report, or admin endpoint is finished. Add features in tested vertical slices.

## Database safety
- Your existing PostgreSQL database remains the source of truth. **Do not run a new schema or seed script from this backend.**
- The supplied file named `production_schema_v2.6.sql` has a header saying schema v2.3. Confirm the actual installed schema version before developing against it.
- The deployment-security script supplied earlier calls itself a staging candidate, not production approval. Review grants, RLS, `SECURITY DEFINER` ownership and function behavior.
- Use a restricted application login such as `ticket_app`; never use a PostgreSQL superuser or `BYPASSRLS` role.
- The approved six roles remain Employee, Support Agent, Team Lead, Department Manager, Company Admin, and System Admin. No Auditor or Super Admin role is added here.
- PostgreSQL RLS and constraints remain part of authorization. Do not remove RLS to make an endpoint work.
- This repo intentionally does not call `Base.metadata.create_all()` and does not apply migrations at startup.

## Stack
Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.x, Psycopg 3, PostgreSQL, JWT, bcrypt, pytest, Ruff.

## Structure
```text
app/
  api/deps.py                 # DB, authentication, permission dependencies
  api/v1/routes/              # HTTP endpoints
  core/                       # settings, logging, token security
  db/                         # SQLAlchemy engine and request-scoped sessions
  models/                     # mappings for first implemented feature slice
  schemas/                    # request/response validation
  services/                   # business logic and audit calls
  repositories/               # future shared query layer
scripts/check_schema.py       # read-only database preflight
tests/                        # unit tests and integration-test plan
docs/                         # architecture, security, roadmap
database/README.md            # database-first policy
```

## Windows setup (PowerShell)
Run from the repository folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env`. For local PostgreSQL, use a URL like:
`postgresql+psycopg://ticket_app:YOUR_PASSWORD@localhost:5433/ticket`
URL-encode special characters in passwords. Never commit `.env` or put secrets in source code.

Then run the read-only schema preflight:
```powershell
python scripts/check_schema.py
```

Start the API:
```powershell
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`, `http://127.0.0.1:8000/health`, and `http://127.0.0.1:8000/health/ready`.

Run tests and lint:
```powershell
pytest
ruff check .
```

## Initial endpoints
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Process liveness |
| GET | `/health/ready` | Database connectivity |
| POST | `/api/v1/auth/login` | Login with company code, email, password |
| GET | `/api/v1/auth/me` | Current authenticated user |
| POST | `/api/v1/auth/logout` | Revoke current session |
| GET | `/api/v1/tickets` | List tickets visible to current user |
| GET | `/api/v1/tickets/{ticket_id}` | Read one authorized ticket |
| POST | `/api/v1/tickets` | Create ticket |
| GET | `/api/v1/tickets/{ticket_id}/comments` | Read comments visible under RLS |
| POST | `/api/v1/tickets/{ticket_id}/comments` | Add a public comment or authorized internal note |

## RLS and authentication
After authentication, the backend sets PostgreSQL's transaction-local `app.user_id` with `set_config(..., true)` **on the same connection and transaction** used for protected queries. The supplied database helper functions depend on this context.

The login path queries an active user by company code and email, verifies the stored bcrypt-compatible hash, sets the user context, and stores a one-way hash of the access token in `user_sessions`. Confirm that your restricted application role can perform the login lookup. If unauthenticated reads from `users` are intentionally blocked by RLS, use a narrowly scoped, reviewed authentication lookup function rather than granting broad table access.

## Important implementation notes
1. The create-ticket service currently chooses the first active status by `display_order`, because the described schema has no explicit `is_default` status column. Confirm this matches your actual workflow.
2. Confirm whether default team routing is handled by a DB trigger or needs a backend service.
3. Attachments require secure storage, size/type checks, malware scanning, and authorized download endpoints.
4. Notifications and SLA processing need actual workers/schedulers; tables alone do not deliver or calculate anything.
5. Test all endpoints with `ticket_app`, not only with a DBA account.
6. Integration tests must use a dedicated test database, never your development database.
7. This repository is not a production security certification.

## Migrations
This is a database-first project. Do not run Alembic autogeneration against partial ORM mappings: it may propose dropping or changing tables it does not know about. Establish a reviewed baseline only after the complete installed schema has been mapped and verified.



# Phase 4.1 — Ticket assignment endpoint patch

This patch adds `POST /api/v1/tickets/{ticket_id}/assign` and maps the existing `public.ticket_assignments` table. It does not add or migrate database tables.

## Files to copy

Copy the `app/` folder in this patch into the root of your existing `ticket-management-backend` project, merging directories. The `tickets.py` file is a replacement for the existing file and retains the existing list/detail/create routes while adding assignment.

Make a backup of your current `app/api/v1/routes/tickets.py` before replacing it.

## Endpoint

`POST /api/v1/tickets/{ticket_id}/assign`

Example body:

```json
{
  "assigned_team_id": "<existing-team-uuid>",
  "assigned_user_id": "<existing-active-team-member-uuid>",
  "reason": "Assigned to the support queue"
}
```

`assigned_user_id` is optional; this endpoint requires `assigned_team_id`.

## Security notes

- The acting user, company, and `assigned_by` are derived from the authenticated session, not accepted from request JSON.
- The endpoint calls `public.app_ticket_access(ticket_id, 'ticket.assign')`, which is the database's ticket-specific permission/scope check. It returns `403` when the ticket is visible but the caller cannot assign, and `404` when the ticket is not visible.
- PostgreSQL's existing RLS policies and assignment triggers remain in force. Trigger validation rejects inactive/cross-company users, inactive teams, wrong departments, and users who are not active team members.
- A new assignment should close the previous active assignment via the existing trigger.
- Terminal, draft, and resolved tickets are rejected by this first assignment endpoint.
- Assignment is audited through the existing `write_audit_event` service.
- This endpoint records assignment only; it does not automatically change ticket status. The separate status-transition endpoint should enforce `workflow_transitions` and database status triggers.
- No `Base.metadata.create_all()` or schema SQL is included or needed.

## Before testing

1. Ensure the two assignment triggers verified in Phase 4 remain enabled.
2. Ensure `ticket_app` can execute `public.app_ticket_access` and `public.app_write_audit` as already required by the installed RLS/audit policies.
3. Run the backend test suite and `python -m compileall app`.
4. Test as Support Agent with a real ticket, valid team, and active team member. Then test invalid team/user, employee without `ticket.assign`, and a ticket from another company.

The provided code was syntax-checked, but must still be integration-tested against the live PostgreSQL database and the actual `ticket_app` role.







# Phase 3 — System Admin Notifications

This package adds platform-scoped in-app notifications for System Administrators.
It extends the existing notification table, read/unread API, and UI from Phases 1
and 2. It does not create another notification table and does not change ticket
assignment, queue, claim, or resolution behavior.

## Scope

System Admin notifications are stored in `public.notifications` using the single
platform company's `company_id`. The new nullable `source_company_id` identifies
the tenant that originated an event, where applicable. The recipient is resolved
in PostgreSQL from active, non-deleted users with a non-revoked, system-scoped
`SYSTEM_ADMIN` role assignment on the platform company.

The phase covers:

- Tenant created, status changed, and name/code updated.
- User account suspended, locked, deleted, or restored.
- Security / privileged actions written to `audit_logs` with `auth.*`,
  `session.*`, `permission.*`, `security.*`, `system.*`, `workflow.*`, or
  `platform.*` action prefixes (routine successful `auth.login` and `auth.logout`
  are excluded).
- `COMPANY_ADMIN` / `SYSTEM_ADMIN` assignment grants, revocations, restorations,
  and scope/role changes.
- Permissions added to or removed from the `SYSTEM_ADMIN` role.
- Platform settings changes without copying sensitive policy values into the
  notification text.
- External monitor degraded/recovered incidents.

### Security event-source limitation

The uploaded application's current login service writes successful login/logout
audit events, but it does not currently persist failed-login attempts. V020 can
notify for failed-login / session / security audit actions when a producer writes
those actions to `audit_logs`; it does not fabricate failed-login records. The
user status trigger covers account lock/deactivation/reactivation events.

## Deployment prerequisites

Apply the existing migrations in their established order. V020 directly requires:

- V015 — platform settings
- V016 — company settings (also required by V019)
- V018 — Department Manager notifications
- V019 — Company Admin notifications

If V017 (user preferences) is part of your local migration sequence, keep it in
that sequence; V020 does not directly depend on it. V020 must run after V019. It checks for the required tables, columns,
platform company, role, System Admin assignment, notification event key and
security roles. Do not renumber or reactivate V012/V014; they remain rolled back.

## Files in this package

Copy the included files into the matching paths under your project root:

- `ticket-management-backend/migrations/V020__system_admin_notifications.sql`
- `ticket-management-backend/migrations/V020__system_admin_notifications_verify.sql`
- `ticket-management-backend/migrations/V020__system_admin_notifications_rollback.sql`
- `ticket-management-backend/app/core/config.py`
- `ticket-management-backend/app/models/notification.py`
- `ticket-management-backend/app/schemas/notification.py`
- `ticket-management-backend/app/schemas/health.py`
- `ticket-management-backend/app/api/v1/routes/health.py`
- `ticket-management-backend/app/api/v1/routes/notifications.py`
- `ticket-management-backend/app/services/notification_service.py`
- `ticket-management-backend/tests/unit/test_health_ingest_schema.py`
- `ticketsys-react-frontend/src/types/index.ts`
- `ticketsys-react-frontend/src/services/notifications.service.ts`
- `ticketsys-react-frontend/src/features/notifications/NotificationsPage.tsx`
- `ticketsys-react-frontend/src/components/layout/Topbar.tsx`

The root `app/main.py` in the supplied project already includes the health router
at the root path (`app.include_router(health_router)`). Therefore, the new monitor
endpoint is `POST /health/incidents` (not `/api/v1/health/incidents`). If your
local `main.py` no longer includes `health_router`, restore that existing include.

## Installation

1. Back up the `ticket` database and the backend/frontend folders.
2. Confirm V019 is applied and its verification succeeds.
3. Add a unique high-entropy `HEALTH_INGEST_TOKEN` to the backend `.env`. Generate
   a token with:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Example environment entry (use your generated value, not this placeholder):

   ```env
   HEALTH_INGEST_TOKEN=replace-with-a-generated-random-token-of-at-least-32-characters
   ```

   Keep this token only in the external monitoring system and backend secret
   configuration. Do not put it in React/Vite environment variables or source
   control. If it is unset or shorter than 32 characters, health ingestion returns
   HTTP 503 and remains disabled.

4. In pgAdmin, select the `ticket` database and run the complete
   `V020__system_admin_notifications.sql` as the authorized database administrator.
5. Run `V020__system_admin_notifications_verify.sql`. Confirm it completes without
   an exception, lists all nine System Admin triggers, and shows their functions
   owned by `ticket_security_owner` with `SECURITY DEFINER = true`.
6. Copy the backend/frontend files from this package, preserving the directory
   structure.
7. Restart FastAPI and build the frontend:

   ```powershell
   # backend
   uvicorn app.main:app --reload

   # frontend
   npm run build
   npm run dev
   ```

## Health monitor integration

A separate monitoring service should POST only on a state transition. The endpoint
requires `X-Health-Monitor-Token` and validates it using constant-time comparison.

Example PowerShell degraded event:

```powershell
$body = @{
  service = "PostgreSQL"
  state = "degraded"
  incident_key = "db-primary-incident-2026-001"
  summary = "The database health check failed repeatedly."
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/health/incidents" `
  -Headers @{ "X-Health-Monitor-Token" = $env:HEALTH_INGEST_TOKEN } `
  -ContentType "application/json" `
  -Body $body
```

Send the matching recovery event with the same `incident_key` and `state =
"recovered"`. The database event key includes the incident, service and state, so
retries of the same transition are idempotent. Use a new incident key for a later,
separate outage.

**Availability caveat:** an in-app notification cannot be written while the API or
database is unavailable. The monitor must run outside this application and should
retry/defer the event until the notification API and database recover. For alerting
while the application is down, configure an independent channel such as email,
SMS, paging, or a monitoring provider alert; that is outside this in-app phase.
Use HTTPS and network restrictions for the monitor endpoint in production.

## Verification checklist

- [ ] V020 migration commits successfully.
- [ ] Verification SQL finds exactly one platform company and at least one active
      system-scoped System Admin recipient.
- [ ] Trigger functions are `SECURITY DEFINER`, owned by `ticket_security_owner`.
- [ ] System Admin notification rows use the platform company's `company_id`.
- [ ] A tenant event stores the tenant's UUID in `source_company_id`.
- [ ] Tenant create/status update generates a platform notification.
- [ ] A System Admin role assignment or role permission change generates a
      platform security notification.
- [ ] Platform settings changes generate a configuration notification.
- [ ] Health degraded and recovered requests require the token and generate
      idempotent notifications.
- [ ] System Admin sees All, Unread, Tenants, Security, Health and Configuration
      filters; mark-one and mark-all read still work.
- [ ] Company Admin and Department Manager notification views remain unchanged.
- [ ] A non-System Admin user cannot see the platform feed through the existing
      user/company-scoped notification API and RLS.

## Rollback

If V020 must be rolled back, run the complete
`V020__system_admin_notifications_rollback.sql` as the database administrator.
It removes V020 triggers/functions and revokes the health function from `ticket_app`.
It deliberately keeps generated notifications, `source_company_id`, and its index
so existing platform notification history is not destroyed. No ticket data or
claim state is modified.

## Validation performed in this package build

Python syntax compilation and TypeScript/TSX transpilation are checked before
release. Live PostgreSQL execution, external-monitor connectivity, and the full
Vite production build must still be verified in your environment.



## Local Windows runtime and direct attachment uploads

Run this API directly with the Windows Python virtual environment as described in the Windows setup section above. The existing PostgreSQL database is accessed through `DATABASE_URL`; no container networking or container-specific database URL is used.

Ticket attachments are uploaded directly without ClamAV or antivirus scanning. The backend retains allowed-format, file-content, size, count, private-storage, authentication and authorization checks. Apply `migrations/V023__direct_ticket_attachment_upload.sql` once to the existing database before using the updated attachment API. See `docs/attachments.md` for details and the security implications of accepting unscanned files.
#   i s s u e _ m a n a g e m e n t - s y s t e m  
 