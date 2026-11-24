# In-app notifications

The notifications API reads and updates the `public.notifications` table. Ticket assignment, ticket status changes and comments create notification events through
the trusted database triggers introduced by V010. Internal-note notifications are sent
only to current assignees and team members, never to requesters or general watchers.

## Endpoints

All endpoints require the normal bearer access token.

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/v1/notifications` | Lists notifications addressed to the current user; supports `limit` (1-100, default 50) and `offset` (default 0). |
| `PATCH` | `/api/v1/notifications/{notification_id}/read` | Marks one notification addressed to the current user as read. Returns `204`; returns `404` for IDs not addressed to that user. |
| `PATCH` | `/api/v1/notifications/read-all` | Marks all unread notifications addressed to the current user as read. Returns `{ "updated": n }`. |

The response uses the frontend contract: `id`, `type`, `title`, `message`,
`createdAt`, `read`, and `ticketId`.

## Database migration

Apply `migrations/V009__notification_read_state.sql` and then
`migrations/V010__ticket_notification_events.sql` using the database owner/DBA
connection after the base schema and V008. V010 requires the documented
`ticket_security_owner` NOLOGIN role. On the usual local PostgreSQL port, run these
from `ticket-management-backend` in PowerShell (psql will prompt for the admin password):

```powershell
psql -h localhost -p 5433 -U postgres -d ticket -v ON_ERROR_STOP=1 -f .\migrations\V009__notification_read_state.sql
psql -h localhost -p 5433 -U postgres -d ticket -v ON_ERROR_STOP=1 -f .\migrations\V010__ticket_notification_events.sql
```

Do not run these migrations using the `ticket_app` application login. V009 creates a
user-and-company-scoped RLS UPDATE policy and restricts `ticket_app` to updating only
the `is_read` column.

The FastAPI application sets `app.user_id` transaction-locally during authentication.
The service also filters by both `company_id` and `recipient_user_id`; RLS remains the
final database authorization boundary.

## Event generation scope

V010 writes in-app notification rows for team assignments, ticket status changes,
and comments. Internal-note events are visible only to active assignees and team
members; the actual note body is never copied into a notification. Email, SMS,
push and webhook delivery through `notification_deliveries`, plus escalation-specific
events, remain separate future work.
