from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, require_permission
from app.models.user import User
from app.schemas.admin import PermissionItem, RoleItem, RolePermissionGrant
from app.services.admin_service import grant_permission, list_permissions, list_roles, revoke_permission

router = APIRouter(prefix="/admin", tags=["administration"])


@router.get("/roles", response_model=list[RoleItem])
def read_roles(
    _current_user: Annotated[User, Depends(require_permission("role.view"))],
    db: DbSession,
) -> list[RoleItem]:
    """List all active roles with their permission codes."""
    return list_roles(db)


@router.get("/permissions", response_model=list[PermissionItem])
def read_permissions(
    _current_user: Annotated[User, Depends(require_permission("permission.view"))],
    db: DbSession,
) -> list[PermissionItem]:
    """List all active permission codes."""
    return list_permissions(db)


@router.post("/role-permissions", status_code=204)
def add_role_permission(
    payload: RolePermissionGrant,
    current_user: CurrentUser,
    db: DbSession,
    _guard: Annotated[User, Depends(require_permission("role.manage"))],
) -> None:
    """Grant a permission to a role."""
    grant_permission(db, current_user=current_user, role_id=payload.role_id, permission_id=payload.permission_id)


@router.delete("/role-permissions/{role_id}/{permission_id}", status_code=204)
def remove_role_permission(
    role_id: UUID,
    permission_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    _guard: Annotated[User, Depends(require_permission("role.manage"))],
) -> None:
    """Revoke a permission from a role."""
    revoke_permission(db, current_user=current_user, role_id=role_id, permission_id=permission_id)
