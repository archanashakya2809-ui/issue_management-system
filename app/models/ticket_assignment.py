from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TicketAssignment(Base):
    """Active team/Support Agent assignment plus its reassignment history.

    This table intentionally does not store Team Lead or Department Manager
    responsibility rows; those live in `ticket_responsibilities`.
    """

    __tablename__ = "ticket_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["company_id", "ticket_id"],
            ["tickets.company_id", "tickets.id"],
            name="fk_assignments_ticket_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "assigned_team_id"],
            ["teams.company_id", "teams.id"],
            name="fk_assignments_team_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "assigned_user_id"],
            ["users.company_id", "users.id"],
            name="fk_assignments_user_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["company_id", "assigned_by"],
            ["users.company_id", "users.id"],
            name="fk_assignments_actor_company",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "assigned_team_id IS NOT NULL OR assigned_user_id IS NOT NULL",
            name="chk_assignment_target",
        ),
        CheckConstraint(
            "assigned_user_id IS NULL OR assigned_team_id IS NOT NULL",
            name="chk_assignment_assignee_requires_team",
        ),
        CheckConstraint(
            "unassigned_at IS NULL OR unassigned_at >= assigned_at",
            name="chk_assignment_dates",
        ),
        Index(
            "uq_one_active_ticket_assignment",
            "ticket_id",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "idx_ticket_assignments_active_team",
            "company_id",
            "assigned_team_id",
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "idx_ticket_assignments_active_user",
            "company_id",
            "assigned_user_id",
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "idx_ticket_assignments_ticket_history",
            "company_id",
            "ticket_id",
            text("assigned_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ticket_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    assigned_team_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    assigned_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    assigned_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)

    ticket = relationship("Ticket", back_populates="assignments")
    assigned_team = relationship(
        "Team",
        foreign_keys=[company_id, assigned_team_id],
        viewonly=True,
    )
    assigned_user = relationship(
        "User",
        foreign_keys=[company_id, assigned_user_id],
        viewonly=True,
    )
    assigning_user = relationship(
        "User",
        foreign_keys=[company_id, assigned_by],
        viewonly=True,
    )
