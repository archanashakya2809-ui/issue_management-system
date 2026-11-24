from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def user_has_permission(
    db: Session, *, user_id: UUID, company_id: UUID, permission_code: str
) -> bool:
    """Check active role grants. PostgreSQL RLS must still enforce record and scope access."""
    result = db.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM public.user_role_assignments ura
                JOIN public.roles r ON r.id = ura.role_id
                JOIN public.role_permissions rp ON rp.role_id = r.id
                JOIN public.permissions p ON p.id = rp.permission_id
                WHERE ura.user_id = :user_id
                  AND ura.company_id = :company_id
                  AND ura.revoked_at IS NULL
                  AND r.is_active = TRUE
                  AND p.is_active = TRUE
                  AND p.code = :permission_code
            )
            """
        ),
        {"user_id": user_id, "company_id": company_id, "permission_code": permission_code},
    ).scalar_one()
    return bool(result)
