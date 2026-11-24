from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TicketTransitionCreate(BaseModel):
    """Request to move a ticket to an existing workflow status."""

    model_config = ConfigDict(extra="forbid")

    to_status_id: UUID
    expected_version: int = Field(ge=1)
