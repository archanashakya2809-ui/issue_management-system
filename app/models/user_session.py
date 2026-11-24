
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserSession(Base):
    """
    Represents an authenticated user session.

    Session lifecycle:
    - Idle timeout: 30 minutes.
    - Absolute lifetime: 14 days.
    - Refresh-token rotation does not extend absolute expiry.
    """

    __tablename__ = "user_sessions"

    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "user_id",
            "id",
            name="uq_user_sessions_company_user_id",
        ),
    )

    # Unique session identifier.
    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # Tenant and user ownership.
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )

    # Legacy session token hash retained for compatibility.
    session_token_hash: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
    )

    # Original session creation time.
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Legacy expiry field retained for compatibility.
    # It must not be used as the absolute session expiry.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # V024: Last successfully authenticated user activity.
    # Used to enforce the 30-minute idle timeout.
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # V024: Fixed absolute session expiration time.
    # Set at login and never extended during refresh.
    absolute_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Session revocation timestamp.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Optional client metadata.
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
