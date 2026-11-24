from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TicketClaim(Base):
    """Resolver claim and controlled-handover history for a ticket."""

    __tablename__ = "ticket_claims"
    __table_args__ = (
        UniqueConstraint("ticket_id", "claim_number", name="uq_ticket_claim_number"),
        Index(
            "uq_ticket_claims_one_active_per_ticket",
            "ticket_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index(
            "idx_ticket_claims_history",
            "company_id",
            "ticket_id",
            text("claim_number DESC"),
        ),
        Index(
            "idx_ticket_claims_user_history",
            "company_id",
            "claimed_by",
            text("claimed_at DESC"),
        ),
        ForeignKeyConstraint(
            ["company_id", "ticket_id"],
            ["tickets.company_id", "tickets.id"],
            name="fk_ticket_claims_ticket_company",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["company_id", "claimed_by"],
            ["users.company_id", "users.id"],
            name="fk_ticket_claims_user_company",
            ondelete="RESTRICT",
        ),
        CheckConstraint("claim_number > 0", name="chk_ticket_claim_number"),
        CheckConstraint(
            "released_at IS NULL OR released_at >= claimed_at",
            name="chk_ticket_claim_release_time",
        ),
        CheckConstraint(
            "(released_at IS NULL AND release_reason IS NULL) OR "
            "(released_at IS NOT NULL AND length(btrim(release_reason)) >= 5)",
            name="chk_ticket_claim_release_reason",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ticket_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    claimed_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    claim_number: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)

    ticket = relationship("Ticket", back_populates="claims")
    claimant = relationship("User", foreign_keys=[company_id, claimed_by], viewonly=True)
