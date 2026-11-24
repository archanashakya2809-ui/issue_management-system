from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    employee_code: str | None
    first_name: str
    last_name: str | None
    email: str
    department_id: UUID | None
    status: str
    primary_role: str | None = None


class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: int
    limit: int
    offset: int


class ProfileUpdate(BaseModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, max_length=100)
