# API conventions

- Prefix: `/api/v1`
- Authentication: `Authorization: Bearer <access_token>`
- Login identity: company code + email + password.
- UUIDs identify records; ticket numbers are human-readable.
- `401`: missing/invalid session; `403`: permission denied; `404`: not found or not visible; `422`: request validation failed.
- List endpoints use bounded `limit` and `offset`.
- Never trust browser-supplied user, company, role, or scope IDs as proof of authorization.
- Internal notes require both `ticket.comment` and `ticket.internal_note`; PostgreSQL RLS must enforce visibility too.

## In-app notifications

- `GET /api/v1/notifications?limit=50&offset=0` — list notifications addressed to the authenticated user.
- `PATCH /api/v1/notifications/{notification_id}/read` — mark one owned notification as read (`204`).
- `PATCH /api/v1/notifications/read-all` — mark all owned notifications as read (`{ "updated": n }`).

See [notifications.md](notifications.md) for the response contract, tenant/RLS behavior, and required V009 and V010 database migrations.
