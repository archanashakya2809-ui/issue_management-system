import re
import secrets
import uuid as _uuid
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.org import (
    CategoryItem,
    CompanyItem,
    DepartmentItem,
    DomainItem,
    TeamItem,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "item")[:40]


def _unique_code(db: Session, table: str, company_id: UUID, name: str) -> str:
    """Return a code unique within (company_id, table). Must be called
    within the same transaction so the GUC app.user_id is still active."""
    base = _slugify(name)
    for candidate in (base, f"{base}-{secrets.token_hex(2)}", f"{base}-{secrets.token_hex(3)}"):
        exists = db.execute(
            text(f"SELECT 1 FROM public.{table} WHERE company_id = :c AND code = :k LIMIT 1"),
            {"c": company_id, "k": candidate},
        ).scalar_one_or_none()
        if not exists:
            return candidate
    return f"item-{_uuid.uuid4().hex[:8]}"


_DEPT_LEAD_SQL = """
    SELECT trim(u.first_name || ' ' || COALESCE(u.last_name, ''))
    FROM public.user_role_assignments ura
    JOIN public.roles r  ON r.id  = ura.role_id
    JOIN public.users u  ON u.id  = ura.user_id
    WHERE ura.company_id          = :cid
      AND r.code                  = 'DEPARTMENT_MANAGER'
      AND ura.scope_department_id = :did
      AND ura.revoked_at IS NULL
      AND u.deleted_at   IS NULL
    LIMIT 1
"""

_TEAM_LEAD_SQL = """
    SELECT trim(u.first_name || ' ' || COALESCE(u.last_name, ''))
    FROM public.user_role_assignments ura
    JOIN public.roles r ON r.id = ura.role_id
    JOIN public.users u ON u.id = ura.user_id
    WHERE ura.company_id      = :cid
      AND r.code              = 'TEAM_LEAD'
      AND ura.scope_team_id   = :tid
      AND ura.revoked_at IS NULL
      AND u.deleted_at   IS NULL
    LIMIT 1
"""


# ── Departments ──────────────────────────────────────────────────────────────

def list_departments(db: Session, *, company_id: UUID) -> list[DepartmentItem]:
    rows = db.execute(
        text("""
            SELECT
                d.id,
                d.name,
                d.status,
                COUNT(DISTINCT t.id)    AS team_count,
                (
                    SELECT trim(u.first_name || ' ' || COALESCE(u.last_name, ''))
                    FROM public.user_role_assignments ura
                    JOIN public.roles r ON r.id = ura.role_id
                    JOIN public.users u ON u.id = ura.user_id
                    WHERE ura.company_id          = d.company_id
                      AND r.code                 = 'DEPARTMENT_MANAGER'
                      AND ura.scope_department_id = d.id
                      AND ura.revoked_at IS NULL
                      AND u.deleted_at   IS NULL
                    LIMIT 1
                )                       AS lead_name
            FROM public.departments d
            LEFT JOIN public.teams t
                ON t.department_id = d.id AND t.status = 'active'
            WHERE d.company_id = :company_id
              AND d.status     = 'active'
            GROUP BY d.id, d.name, d.status, d.company_id
            ORDER BY d.name
        """),
        {"company_id": company_id},
    ).mappings().all()

    return [
        DepartmentItem(
            id=row["id"],
            name=row["name"],
            status=row["status"],
            lead_name=row["lead_name"],
            team_count=int(row["team_count"] or 0),
        )
        for row in rows
    ]


def create_department(
    db: Session,
    *,
    company_id: UUID,
    name: str,
    description: str | None = None,
) -> DepartmentItem:
    dept_id = _uuid.uuid4()
    code = _unique_code(db, "departments", company_id, name)
    try:
        # RETURNING captures the row BEFORE db.commit().
        # After commit the GUC (is_local=TRUE) resets, making post-commit
        # SELECTs invisible through RLS.
        row = db.execute(
            text("""
                INSERT INTO public.departments (id, company_id, name, code, description)
                VALUES (:id, :cid, :name, :code, :desc)
                RETURNING id, name, status
            """),
            {"id": dept_id, "cid": company_id, "name": name.strip(), "code": code, "desc": description},
        ).mappings().one()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if "uq_departments_company_name" in str(exc):
            raise HTTPException(409, "A department with this name already exists.")
        raise HTTPException(500, "Failed to create department.")

    return DepartmentItem(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        lead_name=None,
        team_count=0,
    )


def update_department(
    db: Session,
    *,
    company_id: UUID,
    dept_id: UUID,
    name: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> DepartmentItem:
    updates: dict = {}
    if name is not None:
        updates["name"] = name.strip()
    if description is not None:
        updates["description"] = description
    if status is not None:
        updates["status"] = status
    if not updates:
        raise HTTPException(422, "Provide at least one field to update.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        # UPDATE...RETURNING + supplemental SELECTs all run before db.commit()
        # while the GUC is still active, avoiding RLS blocking post-commit reads.
        row = db.execute(
            text(
                f"UPDATE public.departments SET {set_clause} "
                "WHERE id = :id AND company_id = :cid "
                "RETURNING id, name, status"
            ),
            {**updates, "id": dept_id, "cid": company_id},
        ).mappings().one_or_none()
        if row is None:
            db.rollback()
            raise HTTPException(404, "Department not found.")

        team_count = db.execute(
            text("SELECT COUNT(*) FROM public.teams WHERE department_id = :id AND status = 'active'"),
            {"id": dept_id},
        ).scalar() or 0
        lead_name = db.execute(
            text(_DEPT_LEAD_SQL), {"cid": company_id, "did": dept_id}
        ).scalar_one_or_none()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if "uq_departments_company_name" in str(exc):
            raise HTTPException(409, "A department with this name already exists.")
        raise HTTPException(500, "Failed to update department.")

    return DepartmentItem(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        lead_name=lead_name,
        team_count=int(team_count),
    )


# ── Teams ────────────────────────────────────────────────────────────────────

def list_teams(
    db: Session,
    *,
    company_id: UUID,
    department_id: UUID | None = None,
) -> list[TeamItem]:
    params: dict = {"company_id": company_id}
    dept_filter = ""
    if department_id is not None:
        dept_filter = "AND t.department_id = :department_id"
        params["department_id"] = department_id

    rows = db.execute(
        text(f"""
            SELECT
                t.id,
                t.name,
                t.department_id,
                dep.name                                        AS department_name,
                t.status,
                COUNT(DISTINCT tm.user_id)
                    FILTER (WHERE tm.left_at IS NULL)           AS member_count,
                (
                    SELECT trim(u.first_name || ' ' || COALESCE(u.last_name, ''))
                    FROM public.user_role_assignments ura
                    JOIN public.roles r ON r.id = ura.role_id
                    JOIN public.users u ON u.id = ura.user_id
                    WHERE ura.company_id    = t.company_id
                      AND r.code           = 'TEAM_LEAD'
                      AND ura.scope_team_id = t.id
                      AND ura.revoked_at IS NULL
                      AND u.deleted_at   IS NULL
                    LIMIT 1
                )                                               AS lead_name
            FROM public.teams t
            JOIN public.departments dep ON dep.id = t.department_id
            LEFT JOIN public.team_members tm ON tm.team_id = t.id
            WHERE t.company_id = :company_id
              AND t.status     = 'active'
              {dept_filter}
            GROUP BY t.id, t.name, t.department_id, dep.name, t.status
            ORDER BY dep.name, t.name
        """),
        params,
    ).mappings().all()

    return [
        TeamItem(
            id=row["id"],
            name=row["name"],
            department_id=row["department_id"],
            department_name=row["department_name"],
            status=row["status"],
            lead_name=row["lead_name"],
            member_count=int(row["member_count"] or 0),
        )
        for row in rows
    ]


def create_team(
    db: Session,
    *,
    company_id: UUID,
    name: str,
    department_id: UUID,
    description: str | None = None,
) -> TeamItem:
    # Pre-fetch department name within the active GUC transaction.
    # Also validates the department belongs to this company.
    dept_row = db.execute(
        text(
            "SELECT name FROM public.departments "
            "WHERE id = :id AND company_id = :cid AND status = 'active'"
        ),
        {"id": department_id, "cid": company_id},
    ).mappings().one_or_none()
    if dept_row is None:
        raise HTTPException(400, "Department not found in this company.")
    dept_name = dept_row["name"]

    team_id = _uuid.uuid4()
    code = _unique_code(db, "teams", company_id, name)
    try:
        row = db.execute(
            text("""
                INSERT INTO public.teams (id, company_id, department_id, name, code, description)
                VALUES (:id, :cid, :did, :name, :code, :desc)
                RETURNING id, name, department_id, status
            """),
            {
                "id": team_id, "cid": company_id, "did": department_id,
                "name": name.strip(), "code": code, "desc": description,
            },
        ).mappings().one()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        msg = str(exc)
        if "uq_teams_company_name" in msg:
            raise HTTPException(409, "A team with this name already exists.")
        if "foreign key" in msg.lower():
            raise HTTPException(400, "Department not found in this company.")
        raise HTTPException(500, "Failed to create team.")

    return TeamItem(
        id=row["id"],
        name=row["name"],
        department_id=row["department_id"],
        department_name=dept_name,
        status=row["status"],
        lead_name=None,
        member_count=0,
    )


def update_team(
    db: Session,
    *,
    company_id: UUID,
    team_id: UUID,
    name: str | None = None,
    status: str | None = None,
) -> TeamItem:
    updates: dict = {}
    if name is not None:
        updates["name"] = name.strip()
    if status is not None:
        updates["status"] = status
    if not updates:
        raise HTTPException(422, "Provide at least one field to update.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        row = db.execute(
            text(
                f"UPDATE public.teams SET {set_clause} "
                "WHERE id = :id AND company_id = :cid "
                "RETURNING id, name, department_id, status"
            ),
            {**updates, "id": team_id, "cid": company_id},
        ).mappings().one_or_none()
        if row is None:
            db.rollback()
            raise HTTPException(404, "Team not found.")

        dept_name = db.execute(
            text("SELECT name FROM public.departments WHERE id = :id"),
            {"id": row["department_id"]},
        ).scalar_one_or_none()
        member_count = db.execute(
            text("SELECT COUNT(*) FROM public.team_members WHERE team_id = :id AND left_at IS NULL"),
            {"id": team_id},
        ).scalar() or 0
        lead_name = db.execute(
            text(_TEAM_LEAD_SQL), {"cid": company_id, "tid": team_id}
        ).scalar_one_or_none()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if "uq_teams_company_name" in str(exc):
            raise HTTPException(409, "A team with this name already exists.")
        raise HTTPException(500, "Failed to update team.")

    return TeamItem(
        id=row["id"],
        name=row["name"],
        department_id=row["department_id"],
        department_name=dept_name,
        status=row["status"],
        lead_name=lead_name,
        member_count=int(member_count),
    )


# ── Domains ──────────────────────────────────────────────────────────────────

def list_domains(
    db: Session,
    *,
    company_id: UUID,
    department_id: UUID | None = None,
) -> list[DomainItem]:
    params: dict = {"company_id": company_id}
    dept_filter = ""
    if department_id is not None:
        dept_filter = "AND d.department_id = :department_id"
        params["department_id"] = department_id

    rows = db.execute(
        text(f"""
            SELECT
                d.id,
                d.name,
                d.department_id,
                dep.name                AS department_name,
                d.status,
                COUNT(DISTINCT tc.id)   AS category_count
            FROM public.domains d
            JOIN public.departments dep ON dep.id = d.department_id
            LEFT JOIN public.ticket_categories tc
                ON tc.domain_id = d.id AND tc.is_active = TRUE
            WHERE d.company_id = :company_id
              AND d.status     = 'active'
              {dept_filter}
            GROUP BY d.id, d.name, d.department_id, dep.name, d.status
            ORDER BY dep.name, d.name
        """),
        params,
    ).mappings().all()

    return [
        DomainItem(
            id=row["id"],
            name=row["name"],
            department_id=row["department_id"],
            department_name=row["department_name"],
            status=row["status"],
            category_count=int(row["category_count"] or 0),
        )
        for row in rows
    ]


def create_domain(
    db: Session,
    *,
    company_id: UUID,
    name: str,
    department_id: UUID,
    description: str | None = None,
) -> DomainItem:
    # Pre-fetch department name within the active GUC transaction.
    dept_row = db.execute(
        text(
            "SELECT name FROM public.departments "
            "WHERE id = :id AND company_id = :cid AND status = 'active'"
        ),
        {"id": department_id, "cid": company_id},
    ).mappings().one_or_none()
    if dept_row is None:
        raise HTTPException(400, "Department not found in this company.")
    dept_name = dept_row["name"]

    domain_id = _uuid.uuid4()
    code = _unique_code(db, "domains", company_id, name)
    try:
        row = db.execute(
            text("""
                INSERT INTO public.domains (id, company_id, department_id, name, code, description)
                VALUES (:id, :cid, :did, :name, :code, :desc)
                RETURNING id, name, department_id, status
            """),
            {
                "id": domain_id, "cid": company_id, "did": department_id,
                "name": name.strip(), "code": code, "desc": description,
            },
        ).mappings().one()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        msg = str(exc)
        if "uq_domains_company_name" in msg:
            raise HTTPException(409, "A domain with this name already exists.")
        if "foreign key" in msg.lower():
            raise HTTPException(400, "Department not found in this company.")
        raise HTTPException(500, "Failed to create domain.")

    return DomainItem(
        id=row["id"],
        name=row["name"],
        department_id=row["department_id"],
        department_name=dept_name,
        status=row["status"],
        category_count=0,
    )


def update_domain(
    db: Session,
    *,
    company_id: UUID,
    domain_id: UUID,
    name: str | None = None,
    status: str | None = None,
) -> DomainItem:
    updates: dict = {}
    if name is not None:
        updates["name"] = name.strip()
    if status is not None:
        updates["status"] = status
    if not updates:
        raise HTTPException(422, "Provide at least one field to update.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        row = db.execute(
            text(
                f"UPDATE public.domains SET {set_clause} "
                "WHERE id = :id AND company_id = :cid "
                "RETURNING id, name, department_id, status"
            ),
            {**updates, "id": domain_id, "cid": company_id},
        ).mappings().one_or_none()
        if row is None:
            db.rollback()
            raise HTTPException(404, "Domain not found.")

        dept_name = db.execute(
            text("SELECT name FROM public.departments WHERE id = :id"),
            {"id": row["department_id"]},
        ).scalar_one_or_none()
        category_count = db.execute(
            text("SELECT COUNT(*) FROM public.ticket_categories WHERE domain_id = :id AND is_active = TRUE"),
            {"id": domain_id},
        ).scalar() or 0
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if "uq_domains_company_name" in str(exc):
            raise HTTPException(409, "A domain with this name already exists.")
        raise HTTPException(500, "Failed to update domain.")

    return DomainItem(
        id=row["id"],
        name=row["name"],
        department_id=row["department_id"],
        department_name=dept_name,
        status=row["status"],
        category_count=int(category_count),
    )


# ── Categories ───────────────────────────────────────────────────────────────

def list_categories(
    db: Session,
    *,
    company_id: UUID,
    domain_id: UUID | None = None,
) -> list[CategoryItem]:
    params: dict = {"company_id": company_id}
    domain_filter = ""
    if domain_id is not None:
        domain_filter = "AND tc.domain_id = :domain_id"
        params["domain_id"] = domain_id

    rows = db.execute(
        text(f"""
            SELECT
                tc.id,
                tc.name,
                tc.domain_id,
                dom.name            AS domain_name,
                dom.department_id,
                tc.is_active
            FROM public.ticket_categories tc
            JOIN public.domains dom ON dom.id = tc.domain_id
            WHERE tc.company_id   = :company_id
              AND tc.parent_id IS NULL
              {domain_filter}
            ORDER BY dom.name, tc.name
        """),
        params,
    ).mappings().all()

    return [
        CategoryItem(
            id=row["id"],
            name=row["name"],
            domain_id=row["domain_id"],
            domain_name=row["domain_name"],
            department_id=row["department_id"],
            is_active=bool(row["is_active"]),
        )
        for row in rows
    ]


def create_category(
    db: Session,
    *,
    company_id: UUID,
    name: str,
    domain_id: UUID,
    description: str | None = None,
) -> CategoryItem:
    # Pre-fetch domain name and department_id within the active GUC transaction.
    domain_row = db.execute(
        text(
            "SELECT name, department_id FROM public.domains "
            "WHERE id = :id AND company_id = :cid AND status = 'active'"
        ),
        {"id": domain_id, "cid": company_id},
    ).mappings().one_or_none()
    if domain_row is None:
        raise HTTPException(400, "Domain not found in this company.")
    domain_name = domain_row["name"]
    dept_id = domain_row["department_id"]

    cat_id = _uuid.uuid4()
    base = _slugify(name)
    code = base
    for attempt in range(3):
        if attempt > 0:
            code = f"{base}-{secrets.token_hex(2)}"
        exists = db.execute(
            text(
                "SELECT 1 FROM public.ticket_categories "
                "WHERE company_id = :c AND domain_id = :d AND code = :k LIMIT 1"
            ),
            {"c": company_id, "d": domain_id, "k": code},
        ).scalar_one_or_none()
        if not exists:
            break
    else:
        code = f"cat-{_uuid.uuid4().hex[:8]}"

    try:
        row = db.execute(
            text("""
                INSERT INTO public.ticket_categories (id, company_id, domain_id, name, code, description)
                VALUES (:id, :cid, :did, :name, :code, :desc)
                RETURNING id, name, domain_id, is_active
            """),
            {
                "id": cat_id, "cid": company_id, "did": domain_id,
                "name": name.strip(), "code": code, "desc": description,
            },
        ).mappings().one()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        msg = str(exc)
        if "uq_categories_company_domain_name" in msg:
            raise HTTPException(409, "A category with this name already exists in this domain.")
        if "foreign key" in msg.lower():
            raise HTTPException(400, "Domain not found in this company.")
        raise HTTPException(500, "Failed to create category.")

    return CategoryItem(
        id=row["id"],
        name=row["name"],
        domain_id=row["domain_id"],
        domain_name=domain_name,
        department_id=dept_id,
        is_active=bool(row["is_active"]),
    )


def update_category(
    db: Session,
    *,
    company_id: UUID,
    category_id: UUID,
    name: str | None = None,
    is_active: bool | None = None,
) -> CategoryItem:
    updates: dict = {}
    if name is not None:
        updates["name"] = name.strip()
    if is_active is not None:
        updates["is_active"] = is_active
    if not updates:
        raise HTTPException(422, "Provide at least one field to update.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        row = db.execute(
            text(
                f"UPDATE public.ticket_categories SET {set_clause} "
                "WHERE id = :id AND company_id = :cid "
                "RETURNING id, name, domain_id, is_active"
            ),
            {**updates, "id": category_id, "cid": company_id},
        ).mappings().one_or_none()
        if row is None:
            db.rollback()
            raise HTTPException(404, "Category not found.")

        domain_row = db.execute(
            text("SELECT name, department_id FROM public.domains WHERE id = :id"),
            {"id": row["domain_id"]},
        ).mappings().one_or_none()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        if "uq_categories_company_domain_name" in str(exc):
            raise HTTPException(409, "A category with this name already exists in this domain.")
        raise HTTPException(500, "Failed to update category.")

    return CategoryItem(
        id=row["id"],
        name=row["name"],
        domain_id=row["domain_id"],
        domain_name=domain_row["name"] if domain_row else None,
        department_id=domain_row["department_id"] if domain_row else None,
        is_active=bool(row["is_active"]),
    )


# ── Companies ────────────────────────────────────────────────────────────────

def list_companies(db: Session) -> list[CompanyItem]:
    """Return all non-platform companies. Requires system scope (enforced by caller)."""
    rows = db.execute(
        text("""
            SELECT
                c.id,
                c.name,
                c.code,
                c.status,
                c.created_at,
                COUNT(DISTINCT u.id)    AS user_count,
                COUNT(DISTINCT d.id)    AS department_count
            FROM public.companies c
            LEFT JOIN public.users u
                ON u.company_id = c.id
               AND u.deleted_at IS NULL
               AND u.status     = 'active'
            LEFT JOIN public.departments d
                ON d.company_id = c.id
               AND d.status     = 'active'
            WHERE c.is_platform = FALSE
            GROUP BY c.id, c.name, c.code, c.status, c.created_at
            ORDER BY c.name
        """)
    ).mappings().all()

    return [
        CompanyItem(
            id=row["id"],
            name=row["name"],
            code=row["code"],
            status=row["status"],
            created_at=row["created_at"],
            user_count=int(row["user_count"] or 0),
            department_count=int(row["department_count"] or 0),
        )
        for row in rows
    ]

def update_company(
    db: Session,
    *,
    company_id: UUID,
    name: str | None = None,
    code: str | None = None,
    status: str | None = None,
) -> CompanyItem:
    """Update a non-platform company. Requires system-level caller (enforced by route)."""
    updates: dict = {}
    if name is not None:
        updates["name"] = name.strip()
    if code is not None:
        updates["code"] = code.strip()
    if status is not None:
        updates["status"] = status
    if not updates:
        raise HTTPException(422, "Provide at least one field to update.")

    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    try:
        row = db.execute(
            text(
                f"UPDATE public.companies SET {set_clause} "
                "WHERE id = :id AND is_platform = FALSE "
                "RETURNING id, name, code, status, created_at"
            ),
            {**updates, "id": company_id},
        ).mappings().one_or_none()
        if row is None:
            db.rollback()
            raise HTTPException(404, "Company not found or cannot be modified.")

        user_count = db.execute(
            text(
                "SELECT COUNT(*) FROM public.users "
                "WHERE company_id = :id AND deleted_at IS NULL AND status = 'active'"
            ),
            {"id": company_id},
        ).scalar() or 0
        dept_count = db.execute(
            text(
                "SELECT COUNT(*) FROM public.departments "
                "WHERE company_id = :id AND status = 'active'"
            ),
            {"id": company_id},
        ).scalar() or 0
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        msg = str(exc).lower()
        if "unique" in msg or "duplicate key" in msg:
            raise HTTPException(409, "A company with this name or code already exists.")
        raise HTTPException(500, "Failed to update company.")

    return CompanyItem(
        id=row["id"],
        name=row["name"],
        code=row["code"],
        status=row["status"],
        created_at=row["created_at"],
        user_count=int(user_count),
        department_count=int(dept_count),
    )


def create_company(
    db: Session,
    *,
    name: str,
    code: str,
) -> CompanyItem:
    """Create a non-platform tenant using the authenticated DB transaction."""
    company_id = _uuid.uuid4()
    clean_name = name.strip()
    clean_code = code.strip()

    if not clean_name or not clean_code:
        raise HTTPException(422, "Company name and code are required.")

    try:
        row = db.execute(
            text("""
                INSERT INTO public.companies (
                    id,
                    name,
                    code,
                    status,
                    is_platform,
                    created_at,
                    updated_at
                )
                VALUES (
                    :id,
                    :name,
                    :code,
                    'active',
                    FALSE,
                    now(),
                    now()
                )
                RETURNING id, name, code, status, created_at
            """),
            {
                "id": company_id,
                "name": clean_name,
                "code": clean_code,
            },
        ).mappings().one()

        db.commit()

    except Exception as exc:
        db.rollback()
        message = str(exc).lower()

        if "unique" in message or "duplicate key" in message:
            raise HTTPException(
                status_code=409,
                detail="A company with this name or code already exists.",
            ) from exc

        # Keep internal database details out of the API response.
        raise HTTPException(
            status_code=500,
            detail="Failed to create company. Check the server logs.",
        ) from exc

    return CompanyItem(
        id=row["id"],
        name=row["name"],
        code=row["code"],
        status=row["status"],
        created_at=row["created_at"],
        user_count=0,
        department_count=0,
    )