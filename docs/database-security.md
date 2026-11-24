# Database security checklist

- [ ] Confirm database is `ticket` and schema version matches the deployed scripts.
- [ ] Verify `ticket_app` is not superuser and has no `BYPASSRLS`.
- [ ] Verify app login does not own protected tables and cannot CREATE in `public`.
- [ ] Review `SECURITY DEFINER` function owner, fixed `search_path`, grants and RLS behavior.
- [ ] Confirm the login lookup path works under the restricted role.
- [ ] Verify RLS policies for tickets, comments, assignments, attachments, notifications and audit.
- [ ] Test Employee cannot see internal notes.
- [ ] Test cross-company, cross-team and unauthorized-ticket requests.
- [ ] Test using `ticket_app`, not just a DBA login.
- [ ] Confirm `app.user_id` is set transaction-locally on the same connection as protected queries.
- [ ] Never log passwords, tokens, or database connection strings.
