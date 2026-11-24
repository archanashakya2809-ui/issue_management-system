import logging
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.platform_settings import PlatformSettingsRead, PlatformSettingsUpdate
from app.services.audit_service import write_audit_event

logger = logging.getLogger(__name__)

_COLUMNS = (
    "app_name",
    "default_sla_hours",
    "session_timeout_minutes",
    "max_login_attempts",
    "lockout_minutes",
    "password_policy",
    "maintenance_mode",
    "email_notifications",
    "system_alerts",
)


def _to_response(row: Any) -> PlatformSettingsRead:
    return PlatformSettingsRead(
        app_name=row["app_name"],
        default_sla_hours=row["default_sla_hours"],
        session_timeout_minutes=row["session_timeout_minutes"],
        max_login_attempts=row["max_login_attempts"],
        lockout_minutes=row["lockout_minutes"],
        password_policy=row["password_policy"],
        maintenance_mode=row["maintenance_mode"],
        email_notifications=row["email_notifications"],
        system_alerts=row["system_alerts"],
    )


def get_platform_settings(db: Session, *, company_id: UUID) -> PlatformSettingsRead:
    """Read the one settings row for the authenticated platform company."""
    row = db.execute(
        text("""
            SELECT app_name, default_sla_hours, session_timeout_minutes,
                   max_login_attempts, lockout_minutes, password_policy,
                   maintenance_mode, email_notifications, system_alerts
            FROM public.platform_settings
            WHERE company_id = :company_id
        """),
        {"company_id": company_id},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(
            status_code=503,
            detail="Platform settings are not initialized. Apply migration V015.",
        )
    return _to_response(row)


def update_platform_settings(
    db: Session,
    *,
    current_user: User,
    payload: PlatformSettingsUpdate,
) -> PlatformSettingsRead:
    """Update settings and append an audit event in the same transaction."""
    company_id = current_user.company_id
    try:
        before = db.execute(
            text("""
                SELECT app_name, default_sla_hours, session_timeout_minutes,
                       max_login_attempts, lockout_minutes, password_policy,
                       maintenance_mode, email_notifications, system_alerts
                FROM public.platform_settings
                WHERE company_id = :company_id
                FOR UPDATE
            """),
            {"company_id": company_id},
        ).mappings().one_or_none()
        if before is None:
            raise HTTPException(
                status_code=503,
                detail="Platform settings are not initialized. Apply migration V015.",
            )

        values = payload.model_dump()
        values["app_name"] = values["app_name"].strip()
        if not values["app_name"]:
            raise HTTPException(status_code=422, detail="Application name cannot be blank.")

        updated = db.execute(
            text("""
                UPDATE public.platform_settings
                   SET app_name = :app_name,
                       default_sla_hours = :default_sla_hours,
                       session_timeout_minutes = :session_timeout_minutes,
                       max_login_attempts = :max_login_attempts,
                       lockout_minutes = :lockout_minutes,
                       password_policy = :password_policy,
                       maintenance_mode = :maintenance_mode,
                       email_notifications = :email_notifications,
                       system_alerts = :system_alerts,
                       updated_at = now()
                 WHERE company_id = :company_id
                RETURNING app_name, default_sla_hours, session_timeout_minutes,
                          max_login_attempts, lockout_minutes, password_policy,
                          maintenance_mode, email_notifications, system_alerts
            """),
            {**values, "company_id": company_id},
        ).mappings().one_or_none()
        if updated is None:
            raise HTTPException(status_code=404, detail="Platform settings not found.")

        before_dict = {key: before[key] for key in _COLUMNS}
        after_dict = {key: updated[key] for key in _COLUMNS}
        write_audit_event(
            db,
            company_id=company_id,
            action="platform.settings.update",
            entity_type="platform_settings",
            entity_id=company_id,
            metadata={"before": before_dict, "after": after_dict},
        )
        db.commit()
        return _to_response(updated)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update platform settings")
        raise HTTPException(status_code=500, detail="Failed to update platform settings.") from exc
