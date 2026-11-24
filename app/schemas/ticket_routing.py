from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TicketParticipantSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str | None
    status: str


class TicketTeamSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    code: str
    department_id: UUID
    status: str


class TicketAssignmentRoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    assigned_team_id: UUID | None
    assigned_user_id: UUID | None
    assigned_by: UUID
    assigned_at: datetime
    unassigned_at: datetime | None
    reason: str | None
    assigned_team: TicketTeamSummary | None = None
    assigned_user: TicketParticipantSummary | None = None
    assigning_user: TicketParticipantSummary | None = None


class TicketResponsibilityRoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    responsibility_type: str
    user_id: UUID
    assigned_by: UUID
    assigned_at: datetime
    unassigned_at: datetime | None
    reason: str | None
    user: TicketParticipantSummary | None = None
    assigning_user: TicketParticipantSummary | None = None


class TicketClaimRoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticket_id: UUID
    claimed_by: UUID
    claim_number: int
    claimed_at: datetime
    released_at: datetime | None
    release_reason: str | None
    claimant: TicketParticipantSummary | None = None


class TicketRoutingRead(BaseModel):
    """Normalized ticket routing snapshot with all three assignment tracks."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    destination_department_id: UUID
    active_assignment: TicketAssignmentRoutingRead | None
    active_responsibilities: list[TicketResponsibilityRoutingRead]
    current_claim: TicketClaimRoutingRead | None
    assignment_history: list[TicketAssignmentRoutingRead]
    responsibility_history: list[TicketResponsibilityRoutingRead]
    claim_history: list[TicketClaimRoutingRead]
