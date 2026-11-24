# V015 — Platform Settings

## Purpose
Adds the persistence layer and authenticated API routes used by the existing React `PlatformSettingsPage`.

## Fields
`app_name`, `default_sla_hours`, `session_timeout_minutes`, `max_login_attempts`, `lockout_minutes`, `password_policy`, `maintenance_mode`, `email_notifications`, and `system_alerts`.

## Security
- The row is scoped to the existing single platform company.
- `ticket_app` receives SELECT and column-limited UPDATE only; it cannot INSERT or DELETE.
- RLS allows reads and updates only when `app_has_permission_in_company(company_id, 'system.config.manage')` passes.
- FastAPI additionally requires `system.config.manage` on both routes.
- Updates use the existing `app_write_audit` audit helper in the same transaction.
- The migration verifies that SYSTEM_ADMIN already has the existing permission; it does not invent or broaden role grants.

## Apply
1. Back up the test database.
2. Run `V015__platform_settings.sql` using the database owner/DBA connection.
3. Run `V015__platform_settings_verify.sql`.
4. Expected: one row, RLS enabled, owner `ticket_security_owner`, app SELECT/UPDATE true, INSERT/DELETE false.

## API
- `GET /api/v1/system/settings`
- `PUT /api/v1/system/settings`

Both routes use the existing bearer authentication and `system.config.manage` permission.

## Important behavior boundary
V015 persists the settings currently displayed by React. It does not, by itself, wire every value into runtime behavior. Session expiration, login lockout/password enforcement, maintenance-mode request blocking, SLA defaults, and notification delivery must each be integrated with their respective runtime services before those toggles can be treated as enforced platform controls.

## Rollback
`V015__platform_settings_rollback.sql` removes the table and all stored settings. Back up first; do not use rollback in production unless data loss is intended.
