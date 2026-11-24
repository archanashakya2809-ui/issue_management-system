from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TicketResponsibility(Base):
    """Team Lead and Department Manager responsibility history for a ticket.

    Support Agent assignment remains authoritative in `ticket_assignments`.
    This table stores only the two additional supervisory responsibility types.
    """

    __tablename__ = "ticket_responsibilities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "ticket_id"],
            ["tickets.company_id", "tickets.id"],
            name="fk_ticket_responsibilities_ticket_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "user_id"],
            ["users.company_id", "users.id"],
            name="fk_ticket_responsibilities_user_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "assigned_by"],
            ["users.company_id", "users.id"],
            name="fk_ticket_responsibilities_actor_company",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "responsibility_type IN ('TEAM_LEAD', 'DEPARTMENT_MANAGER')",
            name="chk_ticket_responsibility_type",
        ),
        CheckConstraint(
            "unassigned_at IS NULL OR unassigned_at >= assigned_at",
            name="chk_ticket_responsibility_dates",
        ),
        Index(
            "uq_ticket_responsibility_active_type",
            "ticket_id",
            "responsibility_type",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "idx_ticket_responsibilities_company_user_active",
            "company_id",
            "user_id",
            text("assigned_at DESC"),
            postgresql_where=text("unassigned_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ticket_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    responsibility_type: Mapped[str] = mapped_column(String(40), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    assigned_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)

    ticket = relationship("Ticket", back_populates="responsibilities")
    user = relationship("User", foreign_keys=[company_id, user_id], viewonly=True)
    assigning_user = relationship(
        "User", foreign_keys=[company_id, assigned_by], viewonly=True
    )
