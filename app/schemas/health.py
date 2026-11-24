from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthIncidentIngest(BaseModel):
    """Event posted by a trusted external health monitor."""

    model_config = ConfigDict(extra="forbid")

    service: str = Field(min_length=1, max_length=80)
    state: Literal["degraded", "recovered"]
    incident_key: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=500)
    source_company_id: UUID | None = None

    @field_validator("service", "incident_key", "summary")
    @classmethod
    def strip_and_reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class HealthIncidentIngestResponse(BaseModel):
    notifications_created: int = Field(ge=0)
    service: str
    state: Literal["degraded", "recovered"]
    incident_key: str
