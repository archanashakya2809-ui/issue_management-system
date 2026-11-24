from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationRead(BaseModel):
    """Notification response matching the frontend's camelCase contract."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    title: str
    message: str
    created_at: datetime = Field(serialization_alias="createdAt")
    read: bool = Field(validation_alias="is_read", serialization_alias="read")
    ticket_id: UUID | None = Field(
        default=None,
        validation_alias="ticket_id",
        serialization_alias="ticketId",
    )
    entity_type: str | None = Field(
        default=None,
        validation_alias="entity_type",
        serialization_alias="entityType",
    )
    entity_id: UUID | None = Field(
        default=None,
        validation_alias="entity_id",
        serialization_alias="entityId",
    )
    source_company_id: UUID | None = Field(
        default=None,
        validation_alias="source_company_id",
        serialization_alias="sourceCompanyId",
    )


class MarkAllNotificationsReadResponse(BaseModel):
    updated: int = Field(ge=0)
