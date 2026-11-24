from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    company_code: str = Field(min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_id: UUID
    first_name: str
    last_name: str | None
    email: EmailStr
    department_id: UUID | None
    status: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    csrf_token: str
    user: UserRead


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    csrf_token: str


class RoleAssignmentRead(BaseModel):
    """A single active role assignment returned to the frontend."""
    role_code: str           # e.g. "employee", "system_admin" — derived from roles.name
    scope: str               # value from user_role_assignments.scope_type
    company_id: UUID
    department_id: UUID | None = None
    team_id: UUID | None = None


class UserMeResponse(BaseModel):
    """Extended /auth/me response that includes the caller's active role assignments."""
    id: UUID
    company_id: UUID
    first_name: str
    last_name: str | None
    email: EmailStr
    department_id: UUID | None
    status: str
    roles: list[RoleAssignmentRead]
