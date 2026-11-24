import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.user_preferences import UserPreferencesRead, UserPreferencesUpdate

logger = logging.getLogger(__name__)

_DEFAULTS = UserPreferencesRead()


def get_user_preferences(db: Session, *, company_id: UUID, user_id: UUID) -> UserPreferencesRead:
    """Return the authenticated user's preferences, using UI defaults if not saved yet."""
    row = db.execute(
        text("""
            SELECT in_app, email, weekly_summary, compact_tables
            FROM public.user_preferences
            WHERE company_id = :company_id
              AND user_id = :user_id
        """),
        {"company_id": company_id, "user_id": user_id},
    ).mappings().one_or_none()

    if row is None:
        return _DEFAULTS

    return UserPreferencesRead(**dict(row))


def update_user_preferences(
    db: Session,
    *,
    company_id: UUID,
    user_id: UUID,
    payload: UserPreferencesUpdate,
) -> UserPreferencesRead:
    """Persist preferences only for the current authenticated user."""
    values = payload.model_dump()
    try:
        row = db.execute(
            text("""
                INSERT INTO public.user_preferences (
                    company_id,
                    user_id,
                    in_app,
                    email,
                    weekly_summary,
                    compact_tables
                )
                VALUES (
                    :company_id,
                    :user_id,
                    :in_app,
                    :email,
                    :weekly_summary,
                    :compact_tables
                )
                ON CONFLICT (company_id, user_id)
                DO UPDATE SET
                    in_app = EXCLUDED.in_app,
                    email = EXCLUDED.email,
                    weekly_summary = EXCLUDED.weekly_summary,
                    compact_tables = EXCLUDED.compact_tables,
                    updated_at = now()
                RETURNING in_app, email, weekly_summary, compact_tables
            """),
            {"company_id": company_id, "user_id": user_id, **values},
        ).mappings().one()

        db.commit()
        return UserPreferencesRead(**dict(row))
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update user preferences")
        raise exc
