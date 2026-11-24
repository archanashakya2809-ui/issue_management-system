from uuid import UUID

from pydantic import BaseModel


class PermissionItem(BaseModel):
    id: UUID
    code: str
    name: str
    resource: str
    description: str | None


class RoleItem(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    permissions: list[str]


class RolePermissionGrant(BaseModel):
    role_id: UUID
    permission_id: UUID
