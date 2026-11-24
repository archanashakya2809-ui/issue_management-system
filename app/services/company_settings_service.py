import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.company_settings import CompanySettingsRead, CompanySettingsUpdate
from app.services.audit_service import write_audit_event

logger = logging.getLogger(__name__)

_SETTINGS_COLUMNS = (
    "display_name",
    "timezone",
    "default_sla_hours",
    "reply_to",
    "notify_company_admins",
)


def _to_response(row) -> CompanySettingsRead:
    return CompanySettingsRead(
        display_name=row["display_name"],
        timezone=row["timezone"],
        default_sla_hours=int(row["default_sla_hours"]),
        reply_to=row["reply_to"],
        notify_company_admins=bool(row["notify_company_admins"]),
    )


def _default_settings(company_name: str) -> CompanySettingsRead:
    """Return safe defaults for a newly created tenant before its first save."""
    return CompanySettingsRead(
        display_name=company_name,
        timezone="Asia/Kolkata",
        default_sla_hours=48,
        reply_to="",
        notify_company_admins=True,
    )


def get_company_settings(db: Session, *, company_id: UUID) -> CompanySettingsRead:
    row = db.execute(
        text("""
            SELECT display_name, timezone, default_sla_hours,
                   reply_to, notify_company_admins
            FROM public.company_settings
            WHERE company_id = :company_id
        """),
        {"company_id": company_id},
    ).mappings().one_or_none()

    if row is not None:
        return _to_response(row)

    # New tenants may not yet have a settings row. Do not return a 404 and do
    # not insert during GET. The first authorized PUT persists these defaults.
    company = db.execute(
        text("SELECT name FROM public.companies WHERE id = :company_id"),
        {"company_id": company_id},
    ).mappings().one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found.")

    return _default_settings(company["name"])


def update_company_settings(
    db: Session,
    *,
    current_user: User,
    payload: CompanySettingsUpdate,
) -> CompanySettingsRead:
    company_id = current_user.company_id
    try:
        before = db.execute(
            text("""
                SELECT display_name, timezone, default_sla_hours,
                       reply_to, notify_company_admins
                FROM public.company_settings
                WHERE company_id = :company_id
                FOR UPDATE
            """),
            {"company_id": company_id},
        ).mappings().one_or_none()

        if before is None:
            company = db.execute(
                text("SELECT name FROM public.companies WHERE id = :company_id"),
                {"company_id": company_id},
            ).mappings().one_or_none()
            if company is None:
                raise HTTPException(status_code=404, detail="Company not found.")
            before_values = _default_settings(company["name"]).model_dump()
        else:
            before_values = {key: before[key] for key in _SETTINGS_COLUMNS}

        values = payload.model_dump()
        values["display_name"] = values["display_name"].strip()
        values["timezone"] = values["timezone"].strip()
        values["reply_to"] = values["reply_to"].strip()

        saved = db.execute(
            text("""
                INSERT INTO public.company_settings (
                    company_id, display_name, timezone, default_sla_hours,
                    reply_to, notify_company_admins, created_at, updated_at
                )
                VALUES (
                    :company_id, :display_name, :timezone, :default_sla_hours,
                    :reply_to, :notify_company_admins, now(), now()
                )
                ON CONFLICT (company_id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    timezone = EXCLUDED.timezone,
                    default_sla_hours = EXCLUDED.default_sla_hours,
                    reply_to = EXCLUDED.reply_to,
                    notify_company_admins = EXCLUDED.notify_company_admins,
                    updated_at = now()
                RETURNING display_name, timezone, default_sla_hours,
                          reply_to, notify_company_admins
            """),
            {**values, "company_id": company_id},
        ).mappings().one()

        after_values = {key: saved[key] for key in _SETTINGS_COLUMNS}
        write_audit_event(
            db,
            company_id=company_id,
            action="company.settings.update",
            entity_type="company_settings",
            entity_id=company_id,
            metadata={"before": before_values, "after": after_values},
        )
        db.commit()
        return _to_response(saved)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update company settings")
        raise HTTPException(
            status_code=500,
            detail="Failed to save company settings. Check backend logs.",
        ) from exc
