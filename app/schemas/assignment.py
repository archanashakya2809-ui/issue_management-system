from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TicketAssignmentCreate(BaseModel):
    """Assign a ticket to a team, optionally to an active member of that team."""

    assigned_team_id: UUID
    assigned_user_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_a_target(self) -> "TicketAssignmentCreate":
        # A team is required by this endpoint. The database also enforces
        # the rule that a user cannot be assigned without a team.
        if self.reason is not None:
            self.reason = self.reason.strip() or None
        return self


class TicketAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    ticket_id: UUID
    assigned_team_id: UUID | None
    assigned_user_id: UUID | None
    assigned_by: UUID
    assigned_at: datetime
    unassigned_at: datetime | None
    reason: str | None
