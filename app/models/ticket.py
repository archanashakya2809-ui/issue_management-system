from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Ticket(Base):
    """Ticket aggregate and the parent of its assignment/claim histories.

    `requester_department_id` is the requester's home department.
    `destination_department_id` is the department responsible for handling it.
    These are deliberately separate relationships.
    """

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("company_id", "id", name="uq_tickets_company_id_id"),
        ForeignKeyConstraint(
            ["company_id", "requester_id"],
            ["users.company_id", "users.id"],
            name="fk_tickets_requester_company",
        ),
        ForeignKeyConstraint(
            ["company_id", "requester_department_id"],
            ["departments.company_id", "departments.id"],
            name="fk_tickets_requester_department_company",
        ),
        ForeignKeyConstraint(
            ["company_id", "destination_department_id"],
            ["departments.company_id", "departments.id"],
            name="fk_tickets_destination_department_company",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False
    )
    ticket_number: Mapped[int] = mapped_column(
        BigInteger, server_default=text("nextval('ticket_number_seq')")
    )
    requester_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    requester_department_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    destination_department_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    domain_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    category_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    priority_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requester = relationship("User", foreign_keys=[company_id, requester_id], viewonly=True)
    requester_department = relationship(
        "Department",
        foreign_keys=[company_id, requester_department_id],
        viewonly=True,
    )
    destination_department = relationship(
        "Department",
        foreign_keys=[company_id, destination_department_id],
        viewonly=True,
    )

    assignments = relationship(
        "TicketAssignment",
        back_populates="ticket",
        order_by="TicketAssignment.assigned_at",
        passive_deletes=True,
    )
    responsibilities = relationship(
        "TicketResponsibility",
        back_populates="ticket",
        order_by="TicketResponsibility.assigned_at",
        passive_deletes=True,
    )
    claims = relationship(
        "TicketClaim",
        back_populates="ticket",
        order_by="TicketClaim.claim_number",
        passive_deletes=True,
    )
