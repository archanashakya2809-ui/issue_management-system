# Integration test plan

Use a dedicated disposable test database with the same schema and restricted DB role as development. Never run destructive tests against the developer's `ticket` database.

Add tests for:
1. Login and logout with a test account.
2. Expired/revoked sessions.
3. Employee ticket visibility.
4. Internal-note visibility.
5. Cross-company and cross-team access denial.
6. Invalid catalog IDs and ticket creation.
7. `app.user_id` not leaking across pooled connections.
8. Permission-denied behavior for every role.
