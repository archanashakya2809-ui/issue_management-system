from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TicketClaimStateRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: UUID
    current_resolver_id: UUID | None
    current_claim_number: int | None
    claimed_at: datetime | None
    is_claimed: bool
    claim_available: bool
    can_current_user_claim: bool
    can_current_user_release: bool
    has_current_user_released: bool
    claim_count: int
    remaining_claimants_count: int


class TicketReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=4000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("Please provide a release reason of at least 5 characters.")
        return cleaned


class TicketClaimRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    ticket_id: UUID
    company_id: UUID
    claimed_by: UUID
    claim_number: int
    claimed_at: datetime


class TicketClaimReleaseRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: UUID
    ticket_id: UUID
    company_id: UUID
    released_by: UUID
    claim_number: int
    released_at: datetime
    release_reason: str
