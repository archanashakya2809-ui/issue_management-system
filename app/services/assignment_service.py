import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.ticket import Ticket
from app.models.ticket_assignment import TicketAssignment
from app.models.user import User
from app.schemas.assignment import TicketAssignmentCreate
from app.services.audit_service import write_audit_event

logger = logging.getLogger(__name__)


_EXPECTED_DATABASE_REJECTIONS = {"P0001", "23503", "23505", "23514", "23502"}


def _db_sqlstate(exc: DBAPIError) -> str | None:
    original = getattr(exc, "orig", None)
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


def _has_ticket_permission(
    db: Session, *, ticket_id: UUID, permission_code: str
) -> bool:
    # app_ticket_access is the database's authoritative check for active
    # grants, scope, and domain access on this exact ticket.
    value = db.execute(
        text("SELECT public.app_ticket_access(:ticket_id, :permission_code)"),
        {"ticket_id": ticket_id, "permission_code": permission_code},
    ).scalar_one()
    return bool(value)


def assign_ticket(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    payload: TicketAssignmentCreate,
) -> TicketAssignment:
    """Create an assignment; database triggers enforce target integrity and close the prior assignment."""
    try:
        if not _has_ticket_permission(
            db, ticket_id=ticket_id, permission_code="ticket.assign"
        ):
            # Avoid revealing whether an inaccessible ticket exists.
            if _has_ticket_permission(
                db, ticket_id=ticket_id, permission_code="ticket.view"
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Permission required: ticket.assign",
                )
            raise HTTPException(status_code=404, detail="Ticket not found")

        # The trigger also locks the ticket. Locking it here makes the
        # endpoint's state checks and insertion a single serialized action.
        ticket = db.scalar(
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .with_for_update()
        )
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")

        status_row = db.execute(
            text(
                """
                SELECT code, is_terminal
                FROM public.ticket_statuses
                WHERE company_id = :company_id
                  AND id = :status_id
                """
            ),
            {"company_id": ticket.company_id, "status_id": ticket.status_id},
        ).mappings().first()
        if status_row is None:
            raise HTTPException(
                status_code=409,
                detail="The ticket's current status is unavailable.",
            )
        if status_row["is_terminal"] or status_row["code"] in {"DRAFT", "RESOLVED"}:
            raise HTTPException(
                status_code=409,
                detail="A draft or completed ticket cannot be assigned.",
            )

        assignment = TicketAssignment(
            company_id=ticket.company_id,
            ticket_id=ticket.id,
            assigned_team_id=payload.assigned_team_id,
            assigned_user_id=payload.assigned_user_id,
            assigned_by=current_user.id,
            reason=payload.reason,
        )
        db.add(assignment)

        # Expected FK/check/trigger rejections are reported as conflicts.
        # The transaction is rolled back so no partial assignment remains.
        try:
            db.flush()
        except DBAPIError as exc:
            db.rollback()
            if _db_sqlstate(exc) in _EXPECTED_DATABASE_REJECTIONS:
                logger.info(
                    "Assignment rejected by database validation (SQLSTATE %s)",
                    _db_sqlstate(exc),
                )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Assignment rejected by database validation. Check that the "
                        "team and user are active, in the correct company/department, "
                        "and that the user belongs to the team."
                    ),
                ) from exc
            raise

        write_audit_event(
            db,
            company_id=ticket.company_id,
            action="ticket.assign",
            entity_type="ticket_assignment",
            entity_id=assignment.id,
            metadata={
                "ticket_id": str(ticket.id),
                "assigned_team_id": str(payload.assigned_team_id),
                "assigned_user_id": (
                    str(payload.assigned_user_id)
                    if payload.assigned_user_id is not None
                    else None
                ),
            },
        )
        db.commit()
        # INSERT ... RETURNING populates the server-generated id/time. Avoid
        # refreshing after commit: the transaction-local RLS identity has reset.
        return assignment
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
