from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.admin import PermissionItem, RoleItem
from app.services.permission_service import user_has_permission

# Permissions that only SYSTEM_ADMIN can grant (escalation guard)
_SYSTEM_ONLY_PERMISSIONS = frozenset({
    "company.manage",
    "system.config.manage",
    "security.manage",
    "integration.manage",
    "system.health.view",
    "audit.view",
    "notification.manage",
    "dashboard.system.view",
    "workflow.manage",
    "workflow.view",
})


def _is_system_admin(db: Session, user: User) -> bool:
    return user_has_permission(
        db,
        user_id=user.id,
        company_id=user.company_id,
        permission_code="company.manage",
    )


def list_roles(db: Session) -> list[RoleItem]:
    rows = db.execute(
        text("""
            SELECT r.id, r.code, r.name, r.is_active,
                   ARRAY_REMOVE(ARRAY_AGG(p.code ORDER BY p.code), NULL) AS permissions
            FROM public.roles r
            LEFT JOIN public.role_permissions rp ON rp.role_id = r.id
            LEFT JOIN public.permissions p ON p.id = rp.permission_id AND p.is_active = TRUE
            WHERE r.is_active = TRUE
            GROUP BY r.id, r.code, r.name, r.is_active
            ORDER BY r.code
        """),
    ).mappings().all()

    return [
        RoleItem(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            is_active=bool(row["is_active"]),
            permissions=list(row["permissions"] or []),
        )
        for row in rows
    ]


def list_permissions(db: Session) -> list[PermissionItem]:
    rows = db.execute(
        text("""
            SELECT id, code, name, resource, description
            FROM public.permissions
            WHERE is_active = TRUE
            ORDER BY code
        """),
    ).mappings().all()

    return [
        PermissionItem(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            resource=row["resource"],
            description=row["description"],
        )
        for row in rows
    ]


def grant_permission(
    db: Session,
    *,
    current_user: User,
    role_id: UUID,
    permission_id: UUID,
) -> None:
    """Grant a permission to a role.

    COMPANY_ADMIN constraints:
    - Cannot modify the SYSTEM_ADMIN role.
    - Cannot grant system-only permissions.
    """
    is_sysadmin = _is_system_admin(db, current_user)

    target = db.execute(
        text("SELECT code FROM public.roles WHERE id = :id AND is_active = TRUE"),
        {"id": role_id},
    ).mappings().one_or_none()
    if target is None:
        raise HTTPException(404, "Role not found.")

    if not is_sysadmin and target["code"] == "SYSTEM_ADMIN":
        raise HTTPException(403, "Cannot modify the SYSTEM_ADMIN role.")

    perm = db.execute(
        text("SELECT code FROM public.permissions WHERE id = :id AND is_active = TRUE"),
        {"id": permission_id},
    ).mappings().one_or_none()
    if perm is None:
        raise HTTPException(404, "Permission not found.")

    if not is_sysadmin and perm["code"] in _SYSTEM_ONLY_PERMISSIONS:
        raise HTTPException(403, "Cannot grant a system-level permission.")

    try:
        db.execute(
            text("""
                INSERT INTO public.role_permissions (role_id, permission_id)
                VALUES (:rid, :pid)
                ON CONFLICT (role_id, permission_id) DO NOTHING
            """),
            {"rid": role_id, "pid": permission_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to grant permission.")


def revoke_permission(
    db: Session,
    *,
    current_user: User,
    role_id: UUID,
    permission_id: UUID,
) -> None:
    """Revoke a permission from a role."""
    is_sysadmin = _is_system_admin(db, current_user)

    target = db.execute(
        text("SELECT code FROM public.roles WHERE id = :id"),
        {"id": role_id},
    ).mappings().one_or_none()
    if target is None:
        raise HTTPException(404, "Role not found.")

    if not is_sysadmin and target["code"] == "SYSTEM_ADMIN":
        raise HTTPException(403, "Cannot modify the SYSTEM_ADMIN role.")

    try:
        db.execute(
            text("DELETE FROM public.role_permissions WHERE role_id = :rid AND permission_id = :pid"),
            {"rid": role_id, "pid": permission_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to revoke permission.")
