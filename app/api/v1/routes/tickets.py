
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession, require_permission
from app.models.user import User
from app.schemas.assignment import TicketAssignmentCreate, TicketAssignmentRead
from app.schemas.claim import (
    TicketClaimRead,
    TicketClaimReleaseRead,
    TicketClaimStateRead,
    TicketReleaseRequest,
)
from app.schemas.transition import TicketTransitionCreate
from app.schemas.ticket_routing import TicketRoutingRead
from app.schemas.ticket import (
    TicketClaimedListResponse,
    TicketClaimedRead,
    TicketCreate,
    TicketListResponse,
    TicketRead,
    TicketUpdate,
)
from app.services.assignment_service import assign_ticket
from app.services.ticket_claim_service import (
    claim_ticket,
    get_ticket_claim_state,
    list_my_claimed_tickets,
    list_unassigned_queue_tickets,
    release_ticket_claim,
)
from app.services.ticket_routing_service import get_ticket_routing
from app.services.permission_service import user_has_permission
from app.services.ticket_service import (
    create_ticket,
    get_ticket,
    list_tickets,
    update_ticket,
)
from app.services.transition_service import transition_ticket

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=TicketListResponse)
def read_tickets(
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TicketListResponse:
    items = list_tickets(
        db,
        company_id=current_user.company_id,
        limit=limit,
        offset=offset,
    )
    return TicketListResponse(
        items=[TicketRead.model_validate(item) for item in items],
        limit=limit,
        offset=offset,
    )


@router.get("/assigned-to-me", response_model=TicketClaimedListResponse)
def read_my_claimed_tickets(
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TicketClaimedListResponse:
    """List the current user's unreleased voluntary claims.

    The database query scopes by the authenticated user and company, then
    applies the ticket-view authorization function. It does not use legacy
    assignment ownership or team-lead/manager supervisory visibility.
    """
    items = list_my_claimed_tickets(
        db,
        current_user=current_user,
        limit=limit,
        offset=offset,
    )
    return TicketClaimedListResponse(
        items=[TicketClaimedRead.model_validate(item) for item in items],
        limit=limit,
        offset=offset,
    )


@router.get("/unassigned-queue", response_model=TicketListResponse)
def read_unassigned_queue(
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TicketListResponse:
    """Read the shared department queue without assigning or claiming tickets.

    Queue visibility is available to Support Agents, Team Leads,
    Department Managers and Company Admins when they hold the corresponding
    application permission. Per-ticket visibility and tenant isolation remain
    enforced by app_ticket_access and PostgreSQL RLS.
    """
    if current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="The unassigned queue is available only within a company.",
        )

    allowed_permissions = (
        "ticket.assign",
        "dashboard.agent.view",
        "dashboard.team.view",
        "dashboard.department.view",
    )
    can_view_queue = any(
        user_has_permission(
            db,
            user_id=current_user.id,
            company_id=current_user.company_id,
            permission_code=permission_code,
        )
        for permission_code in allowed_permissions
    )
    if not can_view_queue:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to view the unassigned queue.",
        )

    items = list_unassigned_queue_tickets(
        db,
        current_user=current_user,
        limit=limit,
        offset=offset,
    )
    return TicketListResponse(
        items=[TicketRead.model_validate(item) for item in items],
        limit=limit,
        offset=offset,
    )


@router.get("/{ticket_id}", response_model=TicketRead)
def read_ticket(
    ticket_id: UUID,
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
) -> TicketRead:
    ticket = get_ticket(
        db,
        company_id=current_user.company_id,
        ticket_id=ticket_id,
    )
    return TicketRead.model_validate(ticket)


@router.patch("/{ticket_id}", response_model=TicketRead)
def update_ticket_route(
    ticket_id: UUID,
    payload: TicketUpdate,
    current_user: Annotated[
        User, Depends(require_permission("ticket.update"))
    ],
    db: DbSession,
) -> TicketRead:
    """Update permitted ticket fields without changing workflow status."""
    ticket = update_ticket(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        payload=payload,
    )
    return TicketRead.model_validate(ticket)


@router.post("", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
def create_ticket_route(
    payload: TicketCreate,
    current_user: Annotated[User, Depends(require_permission("ticket.create"))],
    db: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TicketRead:
    ticket = create_ticket(
        db,
        current_user=current_user,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return TicketRead.model_validate(ticket)


@router.get("/{ticket_id}/routing", response_model=TicketRoutingRead)
def read_ticket_routing(
    ticket_id: UUID,
    current_user: Annotated[User, Depends(require_permission("ticket.view"))],
    db: DbSession,
) -> TicketRoutingRead:
    """Read active assignment, supervisory responsibilities and claim history."""
    return get_ticket_routing(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
    )


@router.get("/{ticket_id}/claim-state", response_model=TicketClaimStateRead)
def read_ticket_claim_state(
    ticket_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> TicketClaimStateRead:
    return get_ticket_claim_state(
        db, current_user=current_user, ticket_id=ticket_id
    )


@router.post(
    "/{ticket_id}/claim",
    response_model=TicketClaimRead,
    status_code=status.HTTP_200_OK,
)
def claim_ticket_route(
    ticket_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> TicketClaimRead:
    """First successful assigned participant becomes the active resolver."""
    return claim_ticket(
        db, current_user=current_user, ticket_id=ticket_id
    )


@router.post(
    "/{ticket_id}/release",
    response_model=TicketClaimReleaseRead,
    status_code=status.HTTP_200_OK,
)
def release_ticket_claim_route(
    ticket_id: UUID,
    payload: TicketReleaseRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> TicketClaimReleaseRead:
    """Current resolver may release with a reason for the other two to claim."""
    return release_ticket_claim(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        reason=payload.reason,
    )


@router.post(
    "/{ticket_id}/assign",
    response_model=TicketAssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
def assign_ticket_route(
    ticket_id: UUID,
    payload: TicketAssignmentCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> TicketAssignmentRead:
    """Assign to a team and optionally a member; permission is checked for this ticket in DB."""
    assignment = assign_ticket(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        payload=payload,
    )
    return TicketAssignmentRead.model_validate(assignment)


@router.post(
    "/{ticket_id}/transition",
    response_model=TicketRead,
    status_code=status.HTTP_200_OK,
)
def transition_ticket_route(
    ticket_id: UUID,
    payload: TicketTransitionCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> TicketRead:
    """Change a ticket status using the configured database workflow."""
    ticket = transition_ticket(
        db,
        current_user=current_user,
        ticket_id=ticket_id,
        payload=payload,
    )
    return TicketRead.model_validate(ticket)
