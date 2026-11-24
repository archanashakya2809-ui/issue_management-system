from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TicketAttachmentRead(BaseModel):
    """Safe attachment metadata; private storage paths are never returned."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    ticket_id: UUID
    original_filename: str
    mime_type: str | None
    file_size: int | None
    checksum: str | None
    uploaded_by: UUID
    created_at: datetime
