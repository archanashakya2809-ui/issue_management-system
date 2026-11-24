from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CommentCreate(BaseModel):
    comment_text: str = Field(min_length=1, max_length=10000)
    is_internal: bool = False


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    ticket_id: UUID
    user_id: UUID
    comment_text: str
    is_internal: bool
    created_at: datetime
