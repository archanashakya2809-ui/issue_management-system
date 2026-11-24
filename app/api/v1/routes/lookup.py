"""Read-only, tenant-scoped reference data used by ticket creation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.api.deps import CurrentUser, DbSession, require_permission
from app.services.permission_service import user_has_permission
from app.models.user import User
from app.schemas.lookup import LookupItem

router = APIRouter(prefix="/lookup", tags=["reference data"])


def require_status_lookup_access(
    db: DbSession,
    current_user: CurrentUser,
) -> User:
    """Allow status lookup when any permission admitted by ticket_statuses RLS exists.

    The PostgreSQL SELECT policy permits ticket.view, ticket.create, or
    workflow.view. Keep the API dependency aligned with that policy.
    """
    allowed_codes = ("ticket.view", "ticket.create", "workflow.view")
    if any(
        user_has_permission(
            db,
            user_id=current_user.id,
            company_id=current_user.company_id,
            permission_code=code,
        )
        for code in allowed_codes
    ):
        return current_user

    raise HTTPException(
        status_code=403,
        detail="Permission required: ticket.view, ticket.create or workflow.view",
    )


@router.get("/departments", response_model=list[LookupItem])
def list_ticket_departments(
    current_user: Annotated[User, Depends(require_permission("ticket.create"))],
    db: DbSession,
) -> list[LookupItem]:
    """List active departments in the caller's company for ticket destination."""
    rows = db.execute(
        text(
            """
            SELECT id, name
            FROM public.departments
            WHERE company_id = :company_id
              AND status = 'active'
            ORDER BY name, id
            """
        ),
        {"company_id": current_user.company_id},
    ).mappings().all()
    return [LookupItem(id=row["id"], name=row["name"]) for row in rows]


@router.get("/domains", response_model=list[LookupItem])
def list_domains(
    current_user: Annotated[User, Depends(require_permission("ticket.create"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
) -> list[LookupItem]:
    """List active company domains, optionally filtered by destination department.

    Omitting department_id preserves the shared LookupProvider contract: it loads
    company-wide lookup maps used by ticket adapters and other screens. The ticket
    creation form supplies department_id to enforce the cascading selection UX.
    """
    rows = db.execute(
        text(
            """
            SELECT d.id, d.name
            FROM public.domains AS d
            JOIN public.departments AS dep
              ON dep.company_id = d.company_id
             AND dep.id = d.department_id
            WHERE d.company_id = :company_id
              AND d.status = 'active'
              AND dep.status = 'active'
              AND (CAST(:department_id AS uuid) IS NULL OR d.department_id = CAST(:department_id AS uuid))
            ORDER BY d.name, d.id
            """
        ),
        {"company_id": current_user.company_id, "department_id": department_id},
    ).mappings().all()
    return [LookupItem(id=row["id"], name=row["name"]) for row in rows]


@router.get("/categories", response_model=list[LookupItem])
def list_categories(
    current_user: Annotated[User, Depends(require_permission("ticket.create"))],
    db: DbSession,
    domain_id: UUID | None = Query(default=None),
) -> list[LookupItem]:
    """List active categories, optionally narrowed to one active company domain.

    Omitting domain_id keeps the existing LookupProvider's company-wide mapping
    request working. The ticket creation page always passes its selected domain.
    """
    if domain_id is not None:
        domain_exists = db.execute(
            text(
                """
                SELECT 1
                FROM public.domains d
                JOIN public.departments dep
                  ON dep.company_id = d.company_id
                 AND dep.id = d.department_id
                WHERE d.company_id = :company_id
                  AND d.id = :domain_id
                  AND d.status = 'active'
                  AND dep.status = 'active'
                """
            ),
            {"company_id": current_user.company_id, "domain_id": domain_id},
        ).scalar_one_or_none()
        if domain_exists is None:
            raise HTTPException(status_code=404, detail="Domain not found in this company.")

    rows = db.execute(
        text(
            """
            SELECT id, name
            FROM public.ticket_categories
            WHERE company_id = :company_id
              AND is_active = TRUE
              AND (CAST(:domain_id AS uuid) IS NULL OR domain_id = CAST(:domain_id AS uuid))
            ORDER BY name, id
            """
        ),
        {"company_id": current_user.company_id, "domain_id": domain_id},
    ).mappings().all()
    return [LookupItem(id=row["id"], name=row["name"]) for row in rows]


@router.get("/priorities", response_model=list[LookupItem])
def list_priorities(
    current_user: Annotated[User, Depends(require_permission("ticket.create"))],
    db: DbSession,
) -> list[LookupItem]:
    """List active priorities for the authenticated user's company."""
    rows = db.execute(
        text(
            """
            SELECT id, name
            FROM public.ticket_priorities
            WHERE company_id = :company_id
              AND is_active = TRUE
            ORDER BY level, name
            """
        ),
        {"company_id": current_user.company_id},
    ).mappings().all()
    return [LookupItem(id=row["id"], name=row["name"]) for row in rows]


@router.get("/statuses", response_model=list[LookupItem])
def list_statuses(
    current_user: Annotated[User, Depends(require_status_lookup_access)],
    db: DbSession,
) -> list[LookupItem]:
    """List active statuses for users allowed to view/create tickets or view workflow."""
    rows = db.execute(
        text(
            """
            SELECT id, name, code
            FROM public.ticket_statuses
            WHERE company_id = :company_id
              AND is_active = TRUE
            ORDER BY display_order ASC NULLS LAST, name
            """
        ),
        {"company_id": current_user.company_id},
    ).mappings().all()
    return [
        LookupItem(id=row["id"], name=row["name"], code=row["code"])
        for row in rows
    ]
