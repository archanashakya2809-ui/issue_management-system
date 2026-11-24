import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.transition import TicketTransitionCreate
from app.services.audit_service import write_audit_event

logger = logging.getLogger(__name__)


def _sqlstate(exc: DBAPIError) -> str | None:
    original = getattr(exc, "orig", None)
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


def _ticket_permission(db: Session, *, ticket_id: UUID, permission_code: str) -> bool:
    result = db.execute(
        text("SELECT public.app_ticket_access(:ticket_id, :permission_code)"),
        {"ticket_id": ticket_id, "permission_code": permission_code},
    ).scalar_one()
    return bool(result)


def transition_ticket(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    payload: TicketTransitionCreate,
) -> Ticket:
    """Transition a ticket using configured workflow rules and optimistic locking.

    The installed PostgreSQL triggers remain authoritative: they validate the
    configured from/to transition and append a ticket_status_history row.
    """
    try:
        # Hide ticket existence when the caller cannot view it.
        if not _ticket_permission(db, ticket_id=ticket_id, permission_code="ticket.view"):
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket = db.scalar(
            select(Ticket)
            .where(
                Ticket.id == ticket_id,
                Ticket.company_id == current_user.company_id,
            )
            .with_for_update()
        )
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")

        if ticket.version != payload.expected_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Ticket version is {ticket.version}, but expected_version was "
                    f"{payload.expected_version}. Reload the ticket and retry."
                ),
            )

        if ticket.status_id == payload.to_status_id:
            raise HTTPException(
                status_code=409,
                detail="Ticket already has the requested status.",
            )

        target = db.execute(
            text(
                """
                SELECT code, is_active
                FROM public.ticket_statuses
                WHERE company_id = :company_id
                  AND id = :status_id
                """
            ),
            {"company_id": ticket.company_id, "status_id": payload.to_status_id},
        ).mappings().first()
        if target is None or not target["is_active"]:
            raise HTTPException(
                status_code=422,
                detail="Target status does not exist or is inactive for this company.",
            )

        old_status_id = ticket.status_id
        old_version = ticket.version
        # The installed guard_ticket_version trigger requires exactly +1.
        # Workflow validation and history insertion are handled by DB triggers.
        ticket.status_id = payload.to_status_id
        ticket.version = old_version + 1

        try:
            db.flush()
        except StaleDataError as exc:
            db.rollback()
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to change this ticket's status.",
            ) from exc
        except DBAPIError as exc:
            db.rollback()
            state = _sqlstate(exc)
            message = str(getattr(exc, "orig", exc)).lower()

            if state == "42501" or "does not have permission to change ticket status" in message:
                raise HTTPException(
                    status_code=403,
                    detail="You are not authorized to change this ticket's status.",
                ) from exc

            if "closing a ticket requires ticket.close permission" in message:
                raise HTTPException(
                    status_code=403,
                    detail="Permission required: ticket.close",
                ) from exc

            if "status transition is not allowed" in message:
                raise HTTPException(
                    status_code=409,
                    detail="The configured workflow does not allow this status transition.",
                ) from exc

            if "optimistic locking conflict" in message:
                raise HTTPException(
                    status_code=409,
                    detail="The ticket was updated concurrently. Reload it and retry.",
                ) from exc

            if state in {"P0001", "23503", "23514", "23502"}:
                logger.info("Ticket transition rejected by database validation (SQLSTATE %s)", state)
                raise HTTPException(
                    status_code=409,
                    detail="Ticket transition was rejected by database validation.",
                ) from exc
            raise

        # Reload trigger-managed fields (version, updated_at, lifecycle timestamps)
        # before audit/response; app.user_id remains set for this transaction.
        db.refresh(ticket)
        write_audit_event(
            db,
            company_id=ticket.company_id,
            action="ticket.status.transition",
            entity_type="ticket",
            entity_id=ticket.id,
            metadata={
                "from_status_id": str(old_status_id),
                "to_status_id": str(ticket.status_id),
                "from_version": old_version,
                "to_version": ticket.version,
                "to_status_code": target["code"],
            },
        )
        db.commit()
        return ticket
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
