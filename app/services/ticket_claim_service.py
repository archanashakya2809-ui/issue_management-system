import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.claim import (
    TicketClaimRead,
    TicketClaimReleaseRead,
    TicketClaimStateRead,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database error handling
# ---------------------------------------------------------------------------

def _sqlstate(exc: DBAPIError) -> str | None:
    """Return the PostgreSQL SQLSTATE from a SQLAlchemy DBAPIError."""
    original = getattr(exc, "orig", None)
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


def _postgres_error_message(exc: DBAPIError) -> str:
    """Return PostgreSQL's primary message without SQL or bound parameters."""
    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)

    primary_message = getattr(diagnostic, "message_primary", None)
    if primary_message:
        return str(primary_message)

    if original is not None:
        return str(original).splitlines()[0]

    return "Unknown database error"


def _postgres_constraint_name(exc: DBAPIError) -> str | None:
    """Return a constraint name, when PostgreSQL provides one."""
    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    return str(constraint_name) if constraint_name else None


def _translate_claim_error(
    exc: DBAPIError,
    *,
    operation: str,
) -> HTTPException:
    """Log the real database rejection and return a safe HTTP response.

    PostgreSQL's primary error message is written to the backend log for
    diagnosis. SQL text and bound parameters are deliberately not logged.
    """
    state = _sqlstate(exc)
    database_message = _postgres_error_message(exc)
    constraint_name = _postgres_constraint_name(exc)

    logger.warning(
        "Ticket claim database operation rejected | operation=%s | "
        "SQLSTATE=%s | message=%s | constraint=%s",
        operation,
        state or "unknown",
        database_message,
        constraint_name or "none",
    )

    if state == "P0002":
        return HTTPException(status_code=404, detail="Ticket not found.")

    if state == "42501":
        return HTTPException(
            status_code=403,
            detail="You are not authorized to perform this claim operation.",
        )

    if state == "23514":
        return HTTPException(
            status_code=422,
            detail="The supplied claim or release data is invalid.",
        )

    if state in {"P0001", "23505", "40001"}:
        return HTTPException(
            status_code=409,
            detail=(
                "The ticket claim state changed or conflicts with another operation. "
                "Refresh the ticket and try again."
            ),
        )

    logger.error(
        "Unexpected ticket claim database error | operation=%s | SQLSTATE=%s | message=%s",
        operation,
        state or "unknown",
        database_message,
    )
    return HTTPException(
        status_code=500,
        detail="The ticket claim operation could not be completed.",
    )


# ---------------------------------------------------------------------------
# Current user's claimed-ticket list
# ---------------------------------------------------------------------------

def list_my_claimed_tickets(
    db: Session,
    *,
    current_user: User,
    limit: int,
    offset: int,
) -> list[dict]:
    """List tickets with an unreleased voluntary claim owned by current_user.

    Claim ownership comes only from ticket_claims. Legacy ticket_assignments
    and supervisory responsibility records are intentionally not consulted.

    Resolved/closed tickets are not deleted from claim history. In accordance
    with the approved retain-on-resolution behavior, an unreleased claim stays
    visible here with its actual ticket status until it is explicitly released.
    """
    if current_user.company_id is None:
        return []

    try:
        rows = db.execute(
            text(
                """
                SELECT
                    t.id,
                    t.company_id,
                    t.ticket_number,
                    t.requester_id,
                    t.requester_department_id,
                    t.destination_department_id,
                    t.domain_id,
                    t.category_id,
                    t.priority_id,
                    t.status_id,
                    t.subject,
                    t.description,
                    t.version,
                    t.created_at,
                    t.updated_at,
                    t.resolved_at,
                    t.closed_at,
                    tc.claimed_by,
                    concat_ws(' ', u.first_name, u.last_name) AS claimed_by_name,
                    tc.claim_number,
                    tc.claimed_at
                FROM public.ticket_claims AS tc
                JOIN public.tickets AS t
                  ON t.id = tc.ticket_id
                 AND t.company_id = tc.company_id
                JOIN public.users AS u
                  ON u.id = tc.claimed_by
                 AND u.company_id = tc.company_id
                WHERE tc.company_id = :company_id
                  AND tc.claimed_by = :user_id
                  AND tc.released_at IS NULL
                  AND public.app_is_eligible_department_agent(
                        t.id,
                        :user_id
                      )
                  AND public.app_ticket_access(
                        t.id,
                        'ticket.view'::varchar
                      )
                ORDER BY tc.claimed_at DESC, t.updated_at DESC, t.ticket_number DESC
                LIMIT :limit
                OFFSET :offset
                """
            ),
            {
                "company_id": current_user.company_id,
                "user_id": current_user.id,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()

        return [dict(row) for row in rows]

    except DBAPIError as exc:
        db.rollback()
        raise _translate_claim_error(
            exc,
            operation="list_my_claimed_tickets",
        ) from exc


# ---------------------------------------------------------------------------
# Claim-state read
# ---------------------------------------------------------------------------

def get_ticket_claim_state(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
) -> TicketClaimStateRead:
    """Return claim status and current-user claim/release permissions.

    Claim eligibility is based on the shared department queue and the
    SECURITY DEFINER eligibility helper. Ticket assignments and supervisory
    responsibilities are deliberately not used as claim participants.

    A previous voluntary release is reported for display/history purposes,
    but does not prevent the same eligible Support Agent from reclaiming later.
    """
    if current_user.company_id is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    try:
        result = db.execute(
            text(
                """
                WITH ticket_context AS MATERIALIZED (
                    SELECT
                        t.id AS ticket_id,
                        t.company_id,
                        s.code AS ticket_status,
                        s.is_terminal,
                        q.queue_status
                    FROM public.tickets AS t
                    JOIN public.ticket_statuses AS s
                      ON s.company_id = t.company_id
                     AND s.id = t.status_id
                    LEFT JOIN public.ticket_department_queue AS q
                      ON q.company_id = t.company_id
                     AND q.ticket_id = t.id
                    WHERE t.company_id = :company_id
                      AND t.id = :ticket_id
                      AND public.app_ticket_access(
                            t.id,
                            'ticket.view'::varchar
                          )
                ),

                eligible_agents AS (
                    SELECT u.id AS user_id
                    FROM public.users AS u
                    JOIN ticket_context AS ctx
                      ON ctx.company_id = u.company_id
                    WHERE u.status = 'active'
                      AND u.deleted_at IS NULL
                      AND public.app_is_eligible_department_agent(
                            ctx.ticket_id,
                            u.id
                          )
                ),

                active_claim AS (
                    SELECT
                        tc.id,
                        tc.claimed_by,
                        tc.claim_number,
                        tc.claimed_at
                    FROM public.ticket_claims AS tc
                    JOIN ticket_context AS ctx
                      ON ctx.company_id = tc.company_id
                     AND ctx.ticket_id = tc.ticket_id
                    WHERE tc.released_at IS NULL
                    LIMIT 1
                ),

                claim_history AS (
                    SELECT
                        COUNT(*)::integer AS claim_count,
                        COALESCE(
                            BOOL_OR(
                                tc.claimed_by = :user_id
                                AND tc.released_at IS NOT NULL
                            ),
                            FALSE
                        ) AS has_current_user_released
                    FROM public.ticket_claims AS tc
                    JOIN ticket_context AS ctx
                      ON ctx.company_id = tc.company_id
                     AND ctx.ticket_id = tc.ticket_id
                )

                SELECT
                    ctx.ticket_id,
                    ac.claimed_by AS current_resolver_id,
                    ac.claim_number AS current_claim_number,
                    ac.claimed_at,
                    (ac.id IS NOT NULL) AS is_claimed,

                    (
                        ac.id IS NULL
                        AND ctx.queue_status = 'OPEN'
                        AND ctx.is_terminal = FALSE
                        AND ctx.ticket_status NOT IN (
                            'DRAFT',
                            'RESOLVED',
                            'CLOSED',
                            'CANCELLED'
                        )
                        AND EXISTS (
                            SELECT 1 FROM eligible_agents
                        )
                    ) AS claim_available,

                    (
                        ac.id IS NULL
                        AND ctx.queue_status = 'OPEN'
                        AND ctx.is_terminal = FALSE
                        AND ctx.ticket_status NOT IN (
                            'DRAFT',
                            'RESOLVED',
                            'CLOSED',
                            'CANCELLED'
                        )
                        AND EXISTS (
                            SELECT 1
                            FROM eligible_agents AS ea
                            WHERE ea.user_id = :user_id
                        )
                    ) AS can_current_user_claim,

                    (
                        ac.claimed_by = :user_id
                        AND ctx.queue_status = 'CLAIMED'
                        AND ctx.is_terminal = FALSE
                        AND ctx.ticket_status NOT IN (
                            'DRAFT',
                            'RESOLVED',
                            'CLOSED',
                            'CANCELLED'
                        )
                    ) AS can_current_user_release,

                    ch.has_current_user_released,
                    ch.claim_count,

                    (
                        SELECT COUNT(*)::integer
                        FROM eligible_agents AS ea
                        WHERE ea.user_id IS DISTINCT FROM ac.claimed_by
                    ) AS remaining_claimants_count

                FROM ticket_context AS ctx
                LEFT JOIN active_claim AS ac ON TRUE
                CROSS JOIN claim_history AS ch
                """
            ),
            {
                "company_id": current_user.company_id,
                "ticket_id": ticket_id,
                "user_id": current_user.id,
            },
        ).mappings().one_or_none()

    except DBAPIError as exc:
        db.rollback()
        raise _translate_claim_error(exc, operation="claim_state") from exc

    if result is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    return TicketClaimStateRead(**dict(result))


# ---------------------------------------------------------------------------
# Claim operation
# ---------------------------------------------------------------------------

def claim_ticket(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
) -> TicketClaimRead:
    """Claim through the atomic PostgreSQL function and read its history row.

    The deployed app_claim_ticket(uuid) function returns the new claim UUID.
    It owns the ticket/queue locking, eligibility validation, claim history,
    assignment update, workflow and audit operations.
    """
    if current_user.company_id is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    try:
        claim_id = db.execute(
            text("SELECT public.app_claim_ticket(:ticket_id) AS claim_id"),
            {"ticket_id": ticket_id},
        ).scalar_one()

        claim_row = db.execute(
            text(
                """
                SELECT
                    id,
                    ticket_id,
                    company_id,
                    claimed_by,
                    claim_number,
                    claimed_at
                FROM public.ticket_claims
                WHERE id = :claim_id
                  AND company_id = :company_id
                  AND ticket_id = :ticket_id
                """
            ),
            {
                "claim_id": claim_id,
                "company_id": current_user.company_id,
                "ticket_id": ticket_id,
            },
        ).mappings().one_or_none()

        if claim_row is None:
            raise HTTPException(
                status_code=500,
                detail="The claim was created but its history record could not be read.",
            )

        response = TicketClaimRead(
            id=claim_row["id"],
            ticket_id=claim_row["ticket_id"],
            company_id=claim_row["company_id"],
            claimed_by=claim_row["claimed_by"],
            claim_number=claim_row["claim_number"],
            claimed_at=claim_row["claimed_at"],
        )

        db.commit()
        return response

    except HTTPException:
        db.rollback()
        raise
    except DBAPIError as exc:
        db.rollback()
        raise _translate_claim_error(exc, operation="claim") from exc
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Voluntary release operation
# ---------------------------------------------------------------------------

def release_ticket_claim(
    db: Session,
    *,
    current_user: User,
    ticket_id: UUID,
    reason: str,
) -> TicketClaimReleaseRead:
    """Release the current user's claim through the protected DB function.

    The deployed app_release_ticket_claim(uuid, text) function returns the
    UUID of the released claim. The service then reads that history row under
    the ticket_claims SELECT RLS policy.
    """
    if current_user.company_id is None:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    normalized_reason = reason.strip() if reason else ""
    if len(normalized_reason) < 5:
        raise HTTPException(
            status_code=422,
            detail="Release reason must contain at least five characters.",
        )

    try:
        claim_id = db.execute(
            text(
                """
                SELECT public.app_release_ticket_claim(
                    :ticket_id,
                    :reason
                ) AS claim_id
                """
            ),
            {
                "ticket_id": ticket_id,
                "reason": normalized_reason,
            },
        ).scalar_one()

        released_row = db.execute(
            text(
                """
                SELECT
                    id,
                    ticket_id,
                    company_id,
                    claimed_by,
                    claim_number,
                    released_at,
                    release_reason
                FROM public.ticket_claims
                WHERE id = :claim_id
                  AND company_id = :company_id
                  AND ticket_id = :ticket_id
                  AND released_at IS NOT NULL
                """
            ),
            {
                "claim_id": claim_id,
                "company_id": current_user.company_id,
                "ticket_id": ticket_id,
            },
        ).mappings().one_or_none()

        if released_row is None:
            raise HTTPException(
                status_code=500,
                detail="The claim was released but its history record could not be read.",
            )

        response = TicketClaimReleaseRead(
            claim_id=released_row["id"],
            ticket_id=released_row["ticket_id"],
            company_id=released_row["company_id"],
            released_by=released_row["claimed_by"],
            claim_number=released_row["claim_number"],
            released_at=released_row["released_at"],
            release_reason=released_row["release_reason"],
        )

        db.commit()
        return response

    except HTTPException:
        db.rollback()
        raise
    except DBAPIError as exc:
        db.rollback()
        raise _translate_claim_error(exc, operation="release") from exc
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Shared department unassigned queue
# ---------------------------------------------------------------------------

def list_unassigned_queue_tickets(
    db: Session,
    *,
    current_user: User,
    limit: int,
    offset: int,
) -> list[dict]:
    """List open shared-queue tickets visible to the authenticated user.

    A ticket is in this queue only when:
    - it has a row in ticket_department_queue for the user's company;
    - the queue row is OPEN;
    - the ticket is not terminal; and
    - no unreleased voluntary claim exists.

    Visibility is constrained by app_ticket_access(..., 'ticket.view') and
    company_id. This function does not use ticket_assignments as the source of
    claim ownership and does not assign a ticket. Whether a Support Agent may
    claim a returned ticket is checked separately by the claim-state endpoint
    and the SECURITY DEFINER claim function.

    Team Leads / Department Managers retain their allowed supervisory visibility;
    Company Admin visibility remains governed by the database access helper.
    """
    if current_user.company_id is None:
        return []

    try:
        rows = db.execute(
            text(
                """
                SELECT
                    t.id,
                    t.company_id,
                    t.ticket_number,
                    t.requester_id,
                    t.requester_department_id,
                    t.destination_department_id,
                    t.domain_id,
                    t.category_id,
                    t.priority_id,
                    t.status_id,
                    t.subject,
                    t.description,
                    t.version,
                    t.created_at,
                    t.updated_at,
                    t.resolved_at,
                    t.closed_at
                FROM public.ticket_department_queue AS q
                JOIN public.tickets AS t
                  ON t.id = q.ticket_id
                 AND t.company_id = q.company_id
                JOIN public.ticket_statuses AS s
                  ON s.id = t.status_id
                 AND s.company_id = t.company_id
                WHERE q.company_id = :company_id
                  AND t.company_id = :company_id
                  AND q.queue_status = 'OPEN'
                  AND s.is_terminal = FALSE
                  AND s.code NOT IN (
                      'DRAFT',
                      'RESOLVED',
                      'CLOSED',
                      'CANCELLED'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.ticket_claims AS tc
                      WHERE tc.company_id = t.company_id
                        AND tc.ticket_id = t.id
                        AND tc.released_at IS NULL
                  )
                  AND public.app_ticket_access(
                      t.id,
                      'ticket.view'::varchar
                  )
                ORDER BY t.created_at DESC, t.ticket_number DESC, t.id DESC
                LIMIT :limit
                OFFSET :offset
                """
            ),
            {
                "company_id": current_user.company_id,
                "limit": limit,
                "offset": offset,
            },
        ).mappings().all()
        return [dict(row) for row in rows]

    except DBAPIError as exc:
        db.rollback()
        state = _sqlstate(exc)
        message = _postgres_error_message(exc)
        logger.error(
            "Unable to read shared department queue | SQLSTATE=%s | message=%s",
            state or "unknown",
            message,
        )
        raise HTTPException(
            status_code=500,
            detail="The unassigned ticket queue could not be loaded.",
        ) from exc
