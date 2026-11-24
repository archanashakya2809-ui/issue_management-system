# Ticket assignment and resolver relationship design (Option A)

This implementation keeps the three database tables because they model different lifecycle concepts. It does not create a fourth assignment table.

## Ownership of each record

| Table | Record responsibility | Active uniqueness |
|---|---|---|
| `ticket_assignments` | Responsible team and Support Agent assignment/reassignment history | One active row per ticket (`uq_one_active_ticket_assignment`) |
| `ticket_responsibilities` | Team Lead and Department Manager assignment history only | One active row per ticket and responsibility type (`uq_ticket_responsibility_active_type`) |
| `ticket_claims` | First-claim resolver ownership and controlled handover history | One active claim per ticket (`uq_ticket_claims_one_active_per_ticket`) |

`team_members` and `user_role_assignments` remain organizational source-of-truth tables. They are used by V012 to find eligible people; they are not copied into `ticket_responsibilities`. The responsibility rows are ticket-specific snapshots of who was made responsible when that ticket was routed.

## SQLAlchemy relationships

- `Ticket.assignments` -> `TicketAssignment.ticket`
- `Ticket.responsibilities` -> `TicketResponsibility.ticket`
- `Ticket.claims` -> `TicketClaim.ticket`
- `Ticket.requester` -> `User`
- `Ticket.requester_department` and `Ticket.destination_department` -> `Department`
- `TicketAssignment.assigned_team` -> `Team`
- `TicketAssignment.assigned_user` and `TicketAssignment.assigning_user` -> `User`
- `TicketResponsibility.user` and `TicketResponsibility.assigning_user` -> `User`
- `TicketClaim.claimant` -> `User`

The composite company-scoped foreign keys are represented in the ORM mappings. User/team lookup relationships are read-only ORM relationships; the ticket and assignment identifiers remain the write-authoritative columns.

## Write path and security boundary

1. FastAPI creates the ticket inside one transaction.
2. FastAPI calls `app_auto_assign_ticket_with_responsibilities(ticket_id)`.
3. V012, running as `ticket_security_owner`, calls the existing V008 helper, writes the active `ticket_assignments` row, and writes Team Lead/Department Manager rows to `ticket_responsibilities`.
4. FastAPI reads the resulting records through `TicketAssignment` and `TicketResponsibility` ORM mappings and checks that the persisted IDs match the routing function result before committing.
5. V014's `app_claim_ticket` and `app_release_ticket_claim` remain SECURITY DEFINER write paths. `ticket_app` deliberately has no direct INSERT/UPDATE/DELETE grants on `ticket_claims`; do not replace those function calls with ORM writes.
6. The `GET /api/v1/tickets/{ticket_id}/routing` endpoint reads assignment, responsibility, and claim history through ORM relationships. PostgreSQL RLS remains authoritative for ticket and child-row visibility.

## Important non-duplication rule

Do not add Support Agent rows to `ticket_responsibilities`. The Support Agent remains in `ticket_assignments.assigned_user_id`. Do not store Team Lead/Department Manager in `ticket_assignments.assigned_user_id`. Do not store the current resolver in either assignment table; current resolver is the unreleased row in `ticket_claims`.

## Team Lead selection note

The installed V012 function defines the Team Lead by an active team membership plus an active `TEAM_LEAD` role assignment scoped to that team. It explicitly does not require `team_members.membership_type = 'lead'`, because older application versions may have created the membership row with `membership_type = 'member'`. Do not change a membership type solely to satisfy V012.
