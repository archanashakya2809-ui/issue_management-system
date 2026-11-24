# V011 — Corrected backfill ordering (v2)

This correction addresses two existing database trigger requirements found during pgAdmin testing:

1. `guard_ticket_version()` requires every updated ticket's `version` to increment by exactly one. The historical backfill does so.
2. `set_ticket_destination_department_from_domain()` rejects changing a destination after ticket creation. Therefore, the migration performs the one-time historical backfill BEFORE installing that trigger.

## Apply only after checking the target database

- Run against a disposable development clone, not production.
- Use a database owner/DBA connection.
- Confirm the prior failed V011 transaction was rolled back.
- Confirm `destination_department_id` and `ticket_responsibilities` were not left behind by a partially executed script.
- Execute the corrected `V011__cross_department_ticket_foundation.sql` as one complete script.
- Run `V011__cross_department_ticket_foundation_verify.sql` and inspect every result.
- Do not proceed to V012 unless all verification checks pass.

Do not manually disable the ticket version or destination triggers. This migration order allows the historical backfill before the immutable-destination trigger is installed.
