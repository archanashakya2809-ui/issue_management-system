import hashlib
import json
import logging
import unicodedata
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.ticket import Ticket
from app.models.ticket_comment import TicketComment
from app.models.user import User
from app.schemas.comment import CommentCreate
from app.schemas.ticket import TicketCreate, TicketRead, TicketUpdate
from app.services.audit_service import write_audit_event

logger = logging.getLogger(__name__)


def list_tickets(
    db: Session,
    *,
    company_id: UUID,
    limit: int,
    offset: int,
) -> list[Ticket]:
    """Return tickets visible to the database user within one company."""
    query = (
        select(Ticket)
        .where(Ticket.company_id == company_id)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(query).all())


def get_ticket(
    db: Session,
    *,
    company_id: UUID,
    ticket_id: UUID,
) -> Ticket:
    """Return a ticket scoped to the specified company."""
    ticket = db.scalar(
        select(Ticket).where(
            Ticket.id == ticket_id,
            Ticket.company_id == company_id,
        )
    )

    if ticket is None:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found.",
        )

    return ticket


def _db_sqlstate(exc: DBAPIError) -> str | None:
    original = getattr(exc, "orig", None)
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


def _ticket_create_request_hash(
    payload: TicketCreate, *, destination_department_id: UUID
) -> str:
    """Hash the normalized create request without storing its raw idempotency key."""
    request_data = payload.model_dump(mode="json")
    request_data["destination_department_id"] = str(destination_department_id)

    # These values are normalized before persistence, so normalize them before
    # hashing as well. This makes equivalent retries produce the same hash.
    subject = request_data.get("subject")
    if isinstance(subject, str):
        request_data["subject"] = subject.strip()

    canonical = json.dumps(
        request_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reserve_idempotency_key(
    db: Session,
    *,
    current_user: User,
    idempotency_key: str,
    request_hash: str,
) -> tuple[UUID | None, UUID | None]:
    """Reserve a key or return the ticket id from a completed identical request.

    The idempotency row and ticket are committed in one transaction. A concurrent
    retry blocks on the unique key and then either replays the committed ticket or
    takes ownership after the competing transaction rolls back.
    """
    key = idempotency_key.strip()
    if (
        not key
        or len(key) > 128
        or any(unicodedata.category(char) == "Cc" for char in key)
    ):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 1 to 128 printable characters.",
        )

    key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    reserved_id = db.execute(
        text(
            """
            INSERT INTO public.idempotency_keys (
                company_id, user_id, operation, idempotency_key_hash,
                request_hash, status, expires_at
            )
            VALUES (
                :company_id, :user_id, 'ticket.create', :key_hash,
                :request_hash, 'processing', now() + interval '24 hours'
            )
            ON CONFLICT (company_id, user_id, operation, idempotency_key_hash)
            DO UPDATE SET
                request_hash = EXCLUDED.request_hash,
                status = 'processing',
                response_status_code = NULL,
                response_body = NULL,
                resource_type = NULL,
                resource_id = NULL,
                created_at = now(),
                completed_at = NULL,
                expires_at = EXCLUDED.expires_at
            WHERE public.idempotency_keys.expires_at <= now()
            RETURNING id
            """
        ),
        {
            "company_id": current_user.company_id,
            "user_id": current_user.id,
            "key_hash": key_hash,
            "request_hash": request_hash,
        },
    ).scalar_one_or_none()

    if reserved_id is not None:
        return reserved_id, None

    existing = db.execute(
        text(
            """
            SELECT id, request_hash, status, resource_type, resource_id
            FROM public.idempotency_keys
            WHERE company_id = :company_id
              AND user_id = :user_id
              AND operation = 'ticket.create'
              AND idempotency_key_hash = :key_hash
            """
        ),
        {
            "company_id": current_user.company_id,
            "user_id": current_user.id,
            "key_hash": key_hash,
        },
    ).mappings().one_or_none()

    if existing is None:
        raise HTTPException(
            status_code=409,
            detail="The idempotency key could not be reserved. Retry the request.",
        )
    if existing["request_hash"] != request_hash:
        raise HTTPException(
            status_code=409,
            detail="This Idempotency-Key was already used with a different ticket request.",
        )
    if (
        existing["status"] != "completed"
        or existing["resource_type"] != "ticket"
        or existing["resource_id"] is None
    ):
        raise HTTPException(
            status_code=409,
            detail="A request with this Idempotency-Key is still processing. Retry shortly.",
        )

    return None, existing["resource_id"]


def create_ticket(
    db: Session,
    *,
    current_user: User,
    payload: TicketCreate,
    idempotency_key: str | None = None,
) -> Ticket:
    """Create a ticket and enqueue it atomically using trusted user context."""
    if current_user.company_id is None:
        raise HTTPException(
            status_code=400,
            detail="Your account must belong to an active company before creating tickets.",
        )
    if current_user.department_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Your account must have a home department before creating tickets."
            ),
        )

    normalized_subject = payload.subject.strip()
    if len(normalized_subject) < 3:
        raise HTTPException(
            status_code=422,
            detail="Subject must contain at least 3 non-whitespace characters.",
        )

    destination_department_id = (
        payload.destination_department_id or current_user.department_id
    )
    if destination_department_id is None:
        raise HTTPException(
            status_code=422,
            detail="Select a destination department; your account has no home department to use as a default.",
        )

    try:
        idempotency_row_id: UUID | None = None
        if idempotency_key is not None:
            request_hash = _ticket_create_request_hash(
                payload, destination_department_id=destination_department_id
            )
            idempotency_row_id, replay_ticket_id = _reserve_idempotency_key(
                db,
                current_user=current_user,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay_ticket_id is not None:
                replay_ticket = db.scalar(
                    select(Ticket).where(
                        Ticket.id == replay_ticket_id,
                        Ticket.company_id == current_user.company_id,
                    )
                )
                if replay_ticket is None:
                    raise HTTPException(
                        status_code=404,
                        detail="The ticket from the original request is no longer available.",
                    )
                db.commit()
                return replay_ticket

        # Validate the whole organizational chain in the authenticated tenant.
        # requester_department_id remains the user's home department; the selected
        # destination is stored separately and must own the selected domain.
        valid_target = db.execute(
            text(
                """
                SELECT 1
                FROM public.departments AS dep
                JOIN public.domains AS dom
                  ON dom.company_id = dep.company_id
                 AND dom.department_id = dep.id
                JOIN public.ticket_categories AS cat
                  ON cat.company_id = dom.company_id
                 AND cat.domain_id = dom.id
                WHERE dep.company_id = :company_id
                  AND dep.id = :destination_department_id
                  AND dep.status = 'active'
                  AND dom.id = :domain_id
                  AND dom.status = 'active'
                  AND cat.id = :category_id
                  AND cat.is_active = TRUE
                """
            ),
            {
                "company_id": current_user.company_id,
                "destination_department_id": destination_department_id,
                "domain_id": payload.domain_id,
                "category_id": payload.category_id,
            },
        ).scalar_one_or_none()
        if valid_target is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Select an active destination department, a domain belonging "
                    "to that department, and a category belonging to that domain."
                ),
            )

        priority_is_valid = db.execute(
            text(
                """
                SELECT 1
                FROM public.ticket_priorities
                WHERE company_id = :company_id
                  AND id = :priority_id
                  AND is_active = TRUE
                """
            ),
            {"company_id": current_user.company_id, "priority_id": payload.priority_id},
        ).scalar_one_or_none()
        if priority_is_valid is None:
            raise HTTPException(
                status_code=422,
                detail="The selected priority is not active in your company.",
            )

        open_status_ids = db.execute(
            text(
                """
                SELECT id
                FROM public.ticket_statuses
                WHERE company_id = :company_id
                  AND code = 'OPEN'
                  AND is_active = TRUE
                """
            ),
            {"company_id": current_user.company_id},
        ).scalars().all()
        if len(open_status_ids) != 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The company must have exactly one active OPEN ticket status. "
                    "Ask an administrator to review ticket status configuration."
                ),
            )
        status_id = open_status_ids[0]

        ticket_id = uuid4()
        db.execute(
            text(
                """
                INSERT INTO public.tickets (
                    id, company_id, requester_id, requester_department_id,
                    destination_department_id, domain_id, category_id, priority_id,
                    status_id, subject, description
                )
                VALUES (
                    :id, :company_id, :requester_id, :requester_department_id,
                    :destination_department_id, :domain_id, :category_id, :priority_id,
                    :status_id, :subject, :description
                )
                """
            ),
            {
                "id": ticket_id,
                "company_id": current_user.company_id,
                "requester_id": current_user.id,
                "requester_department_id": current_user.department_id,
                "destination_department_id": destination_department_id,
                "domain_id": payload.domain_id,
                "category_id": payload.category_id,
                "priority_id": payload.priority_id,
                "status_id": status_id,
                "subject": normalized_subject,
                "description": payload.description,
            },
        )

        # Add the ticket to the shared department queue.
        # The enqueue function is SECURITY DEFINER because ticket_app does not
        # have direct INSERT permission on ticket_department_queue.
        # This call uses the same transaction as the ticket INSERT above.
        queue_id = db.execute(
            text(
                """
                SELECT public.app_enqueue_ticket_department(:ticket_id)
                """
            ),
            {"ticket_id": ticket_id},
        ).scalar_one()

        if queue_id is None:
            raise HTTPException(
                status_code=500,
                detail=(
                    "The ticket could not be added to the department queue. "
                    "Ticket creation was rolled back."
                ),
            )

        # Fetch before commit while the transaction-local app.user_id remains set.
        ticket = db.scalar(
            select(Ticket).where(
                Ticket.id == ticket_id,
                Ticket.company_id == current_user.company_id,
            )
        )
        if ticket is None:
            raise HTTPException(status_code=500, detail="Ticket creation failed.")

        write_audit_event(
            db,
            company_id=current_user.company_id,
            action="ticket.create",
            entity_type="ticket",
            entity_id=ticket.id,
            metadata={
                "ticket_number": ticket.ticket_number,
                "requester_department_id": str(current_user.department_id),
                "destination_department_id": str(destination_department_id),
                "domain_id": str(payload.domain_id),
                "category_id": str(payload.category_id),
                "queue_id": str(queue_id),
            },
        )
        if idempotency_row_id is not None:
            response_body = json.dumps(
                TicketRead.model_validate(ticket).model_dump(mode="json"),
                separators=(",", ":"),
                ensure_ascii=False,
            )
            completed = db.execute(
                text(
                    """
                    UPDATE public.idempotency_keys
                       SET status = 'completed',
                           response_status_code = 201,
                           response_body = CAST(:response_body AS jsonb),
                           resource_type = 'ticket',
                           resource_id = :ticket_id,
                           completed_at = now()
                     WHERE id = :id
                       AND company_id = :company_id
                       AND user_id = :user_id
                       AND operation = 'ticket.create'
                       AND status = 'processing'
                    """
                ),
                {
                    "response_body": response_body,
                    "ticket_id": ticket.id,
                    "id": idempotency_row_id,
                    "company_id": current_user.company_id,
                    "user_id": current_user.id,
                },
            )
            if completed.rowcount != 1:
                raise HTTPException(
                    status_code=409,
                    detail="Could not finalize the idempotent ticket creation request.",
                )

        db.commit()
        return ticket

    except HTTPException:
        db.rollback()
        raise
    except DBAPIError as exc:
        # Keep useful PostgreSQL diagnostics in server logs without exposing SQL,
        # bound values, ticket descriptions, credentials, or tokens to the client.
        original = getattr(exc, "orig", None)
        diagnostic = getattr(original, "diag", None)
        sqlstate = _db_sqlstate(exc)
        logger.error(
            "TICKET_CREATE_DATABASE_FAILURE sqlstate=%s primary=%s "
            "constraint=%s table=%s column=%s",
            sqlstate,
            getattr(diagnostic, "message_primary", None)
            or type(original).__name__,
            getattr(diagnostic, "constraint_name", None),
            getattr(diagnostic, "table_name", None),
            getattr(diagnostic, "column_name", None),
        )
        db.rollback()
        if sqlstate == "42501":
            raise HTTPException(
                status_code=403,
                detail="You are not authorized to create a ticket or add it to the department queue.",
            ) from exc
        if sqlstate in {"23503", "23505", "23514", "P0001", "40001"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Ticket creation conflicts with the selected destination or "
                    "department queue configuration. Check the department and domain."
                ),
            ) from exc
        if sqlstate == "42883":
            raise HTTPException(
                status_code=503,
                detail="The department queue database function is not installed. Contact the database administrator.",
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise


def list_comments(
    db: Session,
    *,
    company_id: UUID,
    ticket_id: UUID,
) -> list[TicketComment]:
    """Return non-deleted comments visible under the existing RLS policies."""
    query = (
        select(TicketComment)
        .where(
            TicketComment.company_id == company_id,
            TicketComment.ticket_id == ticket_id,
            TicketComment.deleted_at.is_(None),
        )
        .order_by(TicketComment.created_at.asc())
    )
    return list(db.scalars(query).all())


def create_comment(
    db: Session,
    *,
    current_user: User,
    ticket: Ticket,
    payload: CommentCreate,
) -> TicketComment:
    """Create a comment and record its audit event in the same transaction."""
    # Defense in depth: never trust a Ticket object supplied by a caller unless
    # it belongs to the authenticated tenant.
    if ticket.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    normalized_comment = payload.comment_text.strip()
    if not normalized_comment:
        raise HTTPException(
            status_code=422,
            detail="Comment must contain at least one non-whitespace character.",
        )

    comment = TicketComment(
        company_id=current_user.company_id,
        ticket_id=ticket.id,
        user_id=current_user.id,
        comment_text=normalized_comment,
        is_internal=payload.is_internal,
    )

    try:
        db.add(comment)
        db.flush()

        write_audit_event(
            db,
            company_id=current_user.company_id,
            action="ticket.comment.create",
            entity_type="ticket_comment",
            entity_id=comment.id,
            metadata={
                "ticket_id": str(ticket.id),
                "is_internal": payload.is_internal,
            },
        )

        db.commit()
        return comment

    except Exception:
        db.rollback()
        raise


def update_ticket(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    payload: TicketUpdate,
) -> Ticket:
    """Update permitted ticket fields with optimistic concurrency checking."""
    try:
        # Lock the ticket to avoid concurrent modifications.
        ticket = db.scalar(
            select(Ticket)
            .where(
                Ticket.id == ticket_id,
                Ticket.company_id == current_user.company_id,
            )
            .with_for_update()
        )

        if ticket is None:
            raise HTTPException(
                status_code=404,
                detail="Ticket not found.",
            )

        # Verify update permission using the existing database helper.
        can_update = db.execute(
            text(
                """
                SELECT public.app_ticket_access(
                    :ticket_id,
                    'ticket.update'
                )
                """
            ),
            {"ticket_id": ticket_id},
        ).scalar_one()

        if not can_update:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permission to update this ticket.",
            )

        # Prevent overwriting changes made by another user.
        if ticket.version != payload.expected_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Ticket was updated by someone else. "
                    "Reload it and retry."
                ),
            )

        changes = payload.model_dump(
            exclude_unset=True,
            exclude={"expected_version"},
        )

        if not changes:
            raise HTTPException(
                status_code=422,
                detail="Provide at least one field to update.",
            )

        # Normalize and validate the subject when provided.
        if "subject" in changes and changes["subject"] is not None:
            subject = changes["subject"].strip()

            if len(subject) < 3:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Subject must contain at least 3 "
                        "non-whitespace characters."
                    ),
                )

            changes["subject"] = subject

        # Keep the destination department and domain hierarchy consistent. Moving
        # a ticket to a different department is intentionally not part of generic PATCH.
        related_fields = {"domain_id", "category_id", "priority_id"}
        if related_fields.intersection(changes):
            effective_domain_id = changes.get("domain_id", ticket.domain_id)
            effective_category_id = changes.get("category_id", ticket.category_id)
            effective_priority_id = changes.get("priority_id", ticket.priority_id)
            hierarchy_valid = db.execute(
                text(
                    """
                    SELECT 1
                    FROM public.domains d
                    JOIN public.departments dep
                      ON dep.company_id = d.company_id
                     AND dep.id = d.department_id
                    JOIN public.ticket_categories c
                      ON c.company_id = d.company_id
                     AND c.domain_id = d.id
                    JOIN public.ticket_priorities p
                      ON p.company_id = d.company_id
                     AND p.id = :priority_id
                    WHERE d.company_id = :company_id
                      AND d.id = :domain_id
                      AND d.department_id = :destination_department_id
                      AND d.status = 'active'
                      AND dep.status = 'active'
                      AND c.id = :category_id
                      AND c.is_active = TRUE
                      AND p.id = :priority_id
                      AND p.is_active = TRUE
                    """
                ),
                {
                    "company_id": ticket.company_id,
                    "domain_id": effective_domain_id,
                    "destination_department_id": ticket.destination_department_id,
                    "category_id": effective_category_id,
                    "priority_id": effective_priority_id,
                },
            ).scalar_one_or_none()
            if hierarchy_valid is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Domain, category and priority must be active and remain "
                        "within the ticket's destination department."
                    ),
                )

        # Apply only fields whose values actually changed.
        actual_changes = {
            field: value
            for field, value in changes.items()
            if getattr(ticket, field) != value
        }

        if not actual_changes:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The supplied values are identical "
                    "to the current ticket values."
                ),
            )

        old_version = ticket.version

        for field, value in actual_changes.items():
            setattr(ticket, field, value)

        ticket.version = old_version + 1

        db.flush()
        db.refresh(ticket)

        write_audit_event(
            db,
            company_id=ticket.company_id,
            action="ticket.update",
            entity_type="ticket",
            entity_id=ticket.id,
            metadata={
                "changed_fields": sorted(actual_changes.keys()),
                "from_version": old_version,
                "to_version": ticket.version,
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