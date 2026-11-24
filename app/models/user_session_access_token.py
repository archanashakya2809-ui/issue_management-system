from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserSessionAccessToken(Base):
    """One hashed, short-lived access JWT issued within a login session."""

    __tablename__ = "user_session_access_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "user_id", "session_id"],
            ["user_sessions.company_id", "user_sessions.user_id", "user_sessions.id"],
            name="fk_session_access_tokens_session_identity",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_session_access_tokens_hash_format",
        ),
        CheckConstraint(
            "expires_at > issued_at",
            name="ck_session_access_tokens_expiry",
        ),
        Index(
            "ix_session_access_tokens_session_expiry",
            "session_id",
            "expires_at",
        ),
        Index(
            "ix_session_access_tokens_active_session",
            "session_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
