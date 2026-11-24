from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models.notification import Notification



def refresh_operational_notifications(
    db: Session,
    *,
    recipient_user_id: UUID,
) -> int:
    """Materialize role-scoped SLA/backlog alerts for the authenticated caller.

    The database functions derive identity and scope from app.user_id. They do
    not accept tenant, department, or recipient identifiers from the client.
    Each function returns zero for users without its corresponding role scope.
    A single transaction commits both evaluators, then restores the transaction-
    local user context for the RLS-protected notification SELECT that follows.
    """
    try:
        department_manager_count = db.execute(
            text("SELECT public.emit_department_manager_sla_notifications()")
        ).scalar_one()
        company_admin_count = db.execute(
            text("SELECT public.emit_company_admin_operational_notifications()")
        ).scalar_one()
        db.commit()
        db.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(recipient_user_id)},
        )
        return int(department_manager_count or 0) + int(company_admin_count or 0)
    except Exception:
        db.rollback()
        raise


def list_notifications(
    db: Session,
    *,
    company_id: UUID,
    recipient_user_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[Notification]:
    """Return only notifications addressed to this user in this tenant.

    The explicit predicates provide application-level tenant isolation. PostgreSQL
    RLS remains the final authorization boundary for every returned row.
    """
    statement = (
        select(Notification)
        .where(
            Notification.company_id == company_id,
            Notification.recipient_user_id == recipient_user_id,
        )
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all())


def mark_notification_read(
    db: Session,
    *,
    company_id: UUID,
    recipient_user_id: UUID,
    notification_id: UUID,
) -> None:
    """Mark one of the caller's notifications as read without exposing other IDs."""
    try:
        result = db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.company_id == company_id,
                Notification.recipient_user_id == recipient_user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
            .returning(Notification.id)
        )
        updated_id = result.scalar_one_or_none()

        if updated_id is None:
            # An already-read notification is still a valid successful operation.
            # Check ownership with a SELECT scoped to the same tenant and recipient.
            existing_id = db.scalar(
                select(Notification.id).where(
                    Notification.id == notification_id,
                    Notification.company_id == company_id,
                    Notification.recipient_user_id == recipient_user_id,
                )
            )
            if existing_id is None:
                db.rollback()
                raise HTTPException(status_code=404, detail="Notification not found.")

        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


def mark_all_notifications_read(
    db: Session,
    *,
    company_id: UUID,
    recipient_user_id: UUID,
) -> int:
    """Mark all unread notifications for the authenticated user as read."""
    try:
        result = db.execute(
            update(Notification)
            .where(
                Notification.company_id == company_id,
                Notification.recipient_user_id == recipient_user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True)
        )
        updated_count = max(result.rowcount or 0, 0)
        db.commit()
        return updated_count
    except Exception:
        db.rollback()
        raise
