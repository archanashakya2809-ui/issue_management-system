from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PlatformSettings(Base):
    """Single platform-wide settings row, owned by the platform company."""

    __tablename__ = "platform_settings"
    __table_args__ = (
        CheckConstraint("char_length(btrim(app_name)) BETWEEN 1 AND 150", name="chk_platform_settings_app_name"),
        CheckConstraint("default_sla_hours BETWEEN 1 AND 8760", name="chk_platform_settings_sla"),
        CheckConstraint("session_timeout_minutes BETWEEN 5 AND 1440", name="chk_platform_settings_session_timeout"),
        CheckConstraint("max_login_attempts BETWEEN 3 AND 20", name="chk_platform_settings_max_login_attempts"),
        CheckConstraint("lockout_minutes BETWEEN 1 AND 1440", name="chk_platform_settings_lockout_minutes"),
        CheckConstraint("password_policy IN ('Standard', 'Strong', 'Strict')", name="chk_platform_settings_password_policy"),
    )

    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    app_name: Mapped[str] = mapped_column(String(150), nullable=False)
    default_sla_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=48)
    session_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    lockout_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    password_policy: Mapped[str] = mapped_column(String(20), nullable=False, default="Strong")
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    system_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
