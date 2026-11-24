from uuid import UUID

from pydantic import BaseModel


class LookupItem(BaseModel):
    id: UUID
    name: str
    code: str | None = None
