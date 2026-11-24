from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.ticket import Ticket
from app.models.ticket_assignment import TicketAssignment
from app.models.ticket_claim import TicketClaim
from app.models.ticket_responsibility import TicketResponsibility
from app.models.user import User
from app.schemas.ticket_routing import (
    TicketAssignmentRoutingRead,
    TicketClaimRoutingRead,
    TicketResponsibilityRoutingRead,
    TicketRoutingRead,
)


def get_ticket_routing(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
) -> TicketRoutingRead:
    """Load the ticket's three linked lifecycle records through ORM relationships.

    RLS remains authoritative: the initial Ticket SELECT and every child SELECT
    are executed as the authenticated application role with the request's local
    app.user_id context. This service does not grant additional visibility.
    """
    query = (
        select(Ticket)
        .where(
            Ticket.id == ticket_id,
            Ticket.company_id == current_user.company_id,
        )
        .options(
            selectinload(Ticket.assignments).joinedload(TicketAssignment.assigned_team),
            selectinload(Ticket.assignments).joinedload(TicketAssignment.assigned_user),
            selectinload(Ticket.assignments).joinedload(TicketAssignment.assigning_user),
            selectinload(Ticket.responsibilities).joinedload(TicketResponsibility.user),
            selectinload(Ticket.responsibilities).joinedload(TicketResponsibility.assigning_user),
            selectinload(Ticket.claims).joinedload(TicketClaim.claimant),
        )
    )
    ticket = db.scalar(query)
    if ticket is None:
        # Avoid revealing tickets hidden by tenant isolation or RLS.
        raise HTTPException(status_code=404, detail="Ticket not found.")

    assignment_history = [
        TicketAssignmentRoutingRead.model_validate(row)
        for row in ticket.assignments
    ]
    responsibility_history = [
        TicketResponsibilityRoutingRead.model_validate(row)
        for row in ticket.responsibilities
    ]
    claim_history = [TicketClaimRoutingRead.model_validate(row) for row in ticket.claims]

    active_assignments = [
        row for row in assignment_history if row.unassigned_at is None
    ]
    # The database's partial unique index guarantees at most one active row.
    active_assignment = active_assignments[0] if active_assignments else None

    active_responsibilities = [
        row for row in responsibility_history if row.unassigned_at is None
    ]
    active_claims = [row for row in claim_history if row.released_at is None]
    # V014's partial unique index guarantees at most one active claim.
    current_claim = active_claims[0] if active_claims else None

    return TicketRoutingRead(
        ticket_id=ticket.id,
        destination_department_id=ticket.destination_department_id,
        active_assignment=active_assignment,
        active_responsibilities=active_responsibilities,
        current_claim=current_claim,
        assignment_history=assignment_history,
        responsibility_history=responsibility_history,
        claim_history=claim_history,
    )
