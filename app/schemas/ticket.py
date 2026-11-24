
from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
)


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_department_id: UUID | None = None
    domain_id: UUID
    category_id: UUID
    priority_id: UUID
    subject: str = Field(min_length=3, max_length=500)
    description: str | None = Field(default=None, max_length=20000)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError(
                "Subject must contain at least 3 non-whitespace characters."
            )
        return value


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    ticket_number: int
    requester_id: UUID
    requester_department_id: UUID
    destination_department_id: UUID
    domain_id: UUID
    category_id: UUID
    priority_id: UUID
    status_id: UUID
    subject: str
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None


class TicketListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TicketRead]
    limit: int
    offset: int


class TicketClaimedRead(TicketRead):
    """Ticket data plus the current voluntary claim owner and claim history."""

    claimed_by: UUID
    claimed_by_name: str
    claim_number: int
    claimed_at: datetime


class TicketClaimedListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TicketClaimedRead]
    limit: int
    offset: int


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str | None = Field(default=None, min_length=3, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    domain_id: UUID | None = None
    category_id: UUID | None = None
    priority_id: UUID | None = None
    expected_version: StrictInt = Field(ge=1)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip()
        if len(value) < 3:
            raise ValueError(
                "Subject must contain at least 3 non-whitespace characters."
            )
        return value

    @field_validator(
        "domain_id",
        "category_id",
        "priority_id",
        mode="before",
    )
    @classmethod
    def reject_null_related_ids(cls, value):
        if value is None:
            raise ValueError("This field cannot be null when provided.")
        return value
