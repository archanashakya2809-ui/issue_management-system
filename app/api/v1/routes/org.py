from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.schemas.org import CompanyCreate, CompanyUpdate, OrgUserCreate, OrgUserUpdate
from app.services.org_service import create_company, update_company

from app.api.deps import CurrentUser, DbSession, require_permission
from app.models.user import User
from app.schemas.org import (
    CategoryCreate,
    CategoryItem,
    CategoryUpdate,
    CompanyItem,
    DepartmentCreate,
    DepartmentItem,
    DepartmentUpdate,
    DomainCreate,
    DomainItem,
    DomainUpdate,
    TeamCreate,
    TeamItem,
    TeamUpdate,
)
from app.schemas.system_admin import (
    RoleItem,
    SysUserCreate,
    SysUserListItem,
    SysUserListResponse,
    SysUserUpdate,
)
from app.services.org_service import (
    create_category,
    create_department,
    create_domain,
    create_team,
    list_categories,
    list_companies,
    list_departments,
    list_domains,
    list_teams,
    update_category,
    update_department,
    update_domain,
    update_team,
)
from app.services.user_service import (
    sys_change_user_status,
    sys_create_user,
    sys_delete_user,
    sys_list_users,
    sys_update_user,
)

# ── Company-admin role guard ──────────────────────────────────────────────────
# Only these roles may be created/assigned by a Company Admin.
# System Admin is the sole authority for company_admin and system_admin roles.
_CA_ALLOWED_ROLES = frozenset({"employee", "support_agent", "team_lead", "department_manager"})


def _assert_ca_role_allowed(role_code: str) -> None:
    if role_code.lower() not in _CA_ALLOWED_ROLES:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Company Admin cannot assign the '{role_code}' role. "
                "Only a System Admin may grant Company Admin or System Admin roles."
            ),
        )


def _assert_target_not_elevated(db: Session, user_id: UUID, company_id: UUID) -> None:
    """Block Company Admin from modifying users who already hold elevated roles."""
    elevated = db.execute(
        text("""
            SELECT 1 FROM public.user_role_assignments ura
            JOIN public.roles r ON r.id = ura.role_id
            WHERE ura.user_id    = :uid
              AND ura.company_id = :cid
              AND ura.revoked_at IS NULL
              AND lower(r.code) IN ('company_admin', 'system_admin')
        """),
        {"uid": user_id, "cid": company_id},
    ).scalar_one_or_none()
    if elevated:
        raise HTTPException(
            status_code=403,
            detail="Only a System Admin can modify users who hold Company Admin or System Admin roles.",
        )

router = APIRouter(prefix="/org", tags=["organization"])


# ── Departments ──────────────────────────────────────────────────────────────

@router.get("/departments", response_model=list[DepartmentItem])
def read_departments(
    current_user: Annotated[User, Depends(require_permission("department.view"))],
    db: DbSession,
) -> list[DepartmentItem]:
    return list_departments(db, company_id=current_user.company_id)


@router.post("/departments", response_model=DepartmentItem, status_code=201)
def add_department(
    payload: DepartmentCreate,
    current_user: Annotated[User, Depends(require_permission("department.manage"))],
    db: DbSession,
) -> DepartmentItem:
    return create_department(db, company_id=current_user.company_id, name=payload.name, description=payload.description)


@router.patch("/departments/{dept_id}", response_model=DepartmentItem)
def edit_department(
    dept_id: UUID,
    payload: DepartmentUpdate,
    current_user: Annotated[User, Depends(require_permission("department.manage"))],
    db: DbSession,
) -> DepartmentItem:
    return update_department(
        db,
        company_id=current_user.company_id,
        dept_id=dept_id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
    )


# ── Teams ────────────────────────────────────────────────────────────────────

@router.get("/teams", response_model=list[TeamItem])
def read_teams(
    current_user: Annotated[User, Depends(require_permission("team.view"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
) -> list[TeamItem]:
    return list_teams(db, company_id=current_user.company_id, department_id=department_id)


@router.post("/teams", response_model=TeamItem, status_code=201)
def add_team(
    payload: TeamCreate,
    current_user: Annotated[User, Depends(require_permission("team.manage"))],
    db: DbSession,
) -> TeamItem:
    return create_team(
        db,
        company_id=current_user.company_id,
        name=payload.name,
        department_id=payload.department_id,
        description=payload.description,
    )


@router.patch("/teams/{team_id}", response_model=TeamItem)
def edit_team(
    team_id: UUID,
    payload: TeamUpdate,
    current_user: Annotated[User, Depends(require_permission("team.manage"))],
    db: DbSession,
) -> TeamItem:
    return update_team(db, company_id=current_user.company_id, team_id=team_id, name=payload.name, status=payload.status)


# ── Domains ──────────────────────────────────────────────────────────────────

@router.get("/domains", response_model=list[DomainItem])
def read_domains(
    current_user: Annotated[User, Depends(require_permission("domain.view"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
) -> list[DomainItem]:
    return list_domains(db, company_id=current_user.company_id, department_id=department_id)


@router.post("/domains", response_model=DomainItem, status_code=201)
def add_domain(
    payload: DomainCreate,
    current_user: Annotated[User, Depends(require_permission("domain.manage"))],
    db: DbSession,
) -> DomainItem:
    return create_domain(
        db,
        company_id=current_user.company_id,
        name=payload.name,
        department_id=payload.department_id,
        description=payload.description,
    )


@router.patch("/domains/{domain_id}", response_model=DomainItem)
def edit_domain(
    domain_id: UUID,
    payload: DomainUpdate,
    current_user: Annotated[User, Depends(require_permission("domain.manage"))],
    db: DbSession,
) -> DomainItem:
    return update_domain(db, company_id=current_user.company_id, domain_id=domain_id, name=payload.name, status=payload.status)


# ── Categories ───────────────────────────────────────────────────────────────

@router.get("/categories", response_model=list[CategoryItem])
def read_categories(
    current_user: Annotated[User, Depends(require_permission("domain.view"))],
    db: DbSession,
    domain_id: UUID | None = Query(default=None),
) -> list[CategoryItem]:
    return list_categories(db, company_id=current_user.company_id, domain_id=domain_id)


@router.post("/categories", response_model=CategoryItem, status_code=201)
def add_category(
    payload: CategoryCreate,
    current_user: Annotated[User, Depends(require_permission("domain.manage"))],
    db: DbSession,
) -> CategoryItem:
    return create_category(
        db,
        company_id=current_user.company_id,
        name=payload.name,
        domain_id=payload.domain_id,
        description=payload.description,
    )


@router.patch("/categories/{category_id}", response_model=CategoryItem)
def edit_category(
    category_id: UUID,
    payload: CategoryUpdate,
    current_user: Annotated[User, Depends(require_permission("domain.manage"))],
    db: DbSession,
) -> CategoryItem:
    return update_category(
        db,
        company_id=current_user.company_id,
        category_id=category_id,
        name=payload.name,
        is_active=payload.is_active,
    )


# ── Companies ────────────────────────────────────────────────────────────────

@router.get("/companies", response_model=list[CompanyItem])
def read_companies(
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
) -> list[CompanyItem]:
    """Platform-scoped company list. Only accessible to system admins."""
    return list_companies(db)

@router.post(
    "/companies",
    response_model=CompanyItem,
    status_code=201,
)
def add_company(
    payload: CompanyCreate,
    _current_user: Annotated[
        User, Depends(require_permission("company.manage"))
    ],
    db: DbSession,
) -> CompanyItem:
    """Create a tenant; access is restricted by the existing permission check."""
    return create_company(
        db,
        name=payload.name,
        code=payload.code,
    )


@router.patch("/companies/{company_id}", response_model=CompanyItem)
def edit_company(
    company_id: UUID,
    payload: CompanyUpdate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
) -> CompanyItem:
    """Update a tenant's name, code, or status. Platform company is protected."""
    return update_company(
        db,
        company_id=company_id,
        name=payload.name,
        code=payload.code,
        status=payload.status,
    )


# ── Company-admin user management (tenant-scoped) ─────────────────────────────
# company_id is ALWAYS derived from the authenticated user — never trusted from
# the request body or query params.  Role restrictions are enforced here AND via
# the existing PostgreSQL RLS policies.

@router.get("/company", response_model=CompanyItem)
def read_my_company(current_user: CurrentUser, db: DbSession) -> CompanyItem:
    """Return the authenticated user's own company record."""
    row = db.execute(
        text("""
            SELECT c.id, c.name, c.code, c.status, c.created_at,
                   COUNT(DISTINCT u.id) AS user_count,
                   COUNT(DISTINCT d.id) AS department_count
            FROM public.companies c
            LEFT JOIN public.users u
                ON u.company_id = c.id AND u.deleted_at IS NULL AND u.status = 'active'
            LEFT JOIN public.departments d
                ON d.company_id = c.id AND d.status = 'active'
            WHERE c.id = :cid
            GROUP BY c.id, c.name, c.code, c.status, c.created_at
        """),
        {"cid": current_user.company_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Company not found.")
    return CompanyItem(
        id=row["id"],
        name=row["name"],
        code=row["code"],
        status=row["status"],
        created_at=row["created_at"],
        user_count=int(row["user_count"] or 0),
        department_count=int(row["department_count"] or 0),
    )


@router.get("/roles", response_model=list[RoleItem])
def ca_list_roles(
    _current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> list[RoleItem]:
    """Return only the roles a Company Admin is permitted to assign."""
    rows = db.execute(
        text("""
            SELECT id, lower(code) AS code, name, is_active
            FROM public.roles
            WHERE is_active = TRUE
              AND lower(code) IN ('employee', 'support_agent', 'team_lead', 'department_manager')
            ORDER BY code
        """),
    ).mappings().all()
    return [RoleItem(id=r["id"], code=r["code"], name=r["name"], is_active=r["is_active"]) for r in rows]


@router.get("/users", response_model=SysUserListResponse)
def ca_list_users(
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
    team_id: UUID | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None),
    role_code: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SysUserListResponse:
    return sys_list_users(
        db,
        company_id=current_user.company_id,
        department_id=department_id,
        team_id=team_id,
        q=q,
        status=status,
        role_code=role_code,
        limit=limit,
        offset=offset,
    )


@router.post("/users", response_model=SysUserListItem, status_code=201)
def ca_create_user(
    payload: OrgUserCreate,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> SysUserListItem:
    _assert_ca_role_allowed(payload.role_code)
    sys_payload = SysUserCreate(
        company_id=current_user.company_id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        employee_code=payload.employee_code,
        department_id=payload.department_id,
        team_id=payload.team_id,
        role_code=payload.role_code,
        password=payload.password,
        status=payload.status,
    )
    return sys_create_user(db, current_user=current_user, payload=sys_payload)


@router.patch("/users/{user_id}", response_model=SysUserListItem)
def ca_edit_user(
    user_id: UUID,
    payload: OrgUserUpdate,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> SysUserListItem:
    if payload.role_code:
        _assert_ca_role_allowed(payload.role_code)
    _assert_target_not_elevated(db, user_id, current_user.company_id)
    sys_payload = SysUserUpdate(
        first_name=payload.first_name,
        last_name=payload.last_name,
        employee_code=payload.employee_code,
        department_id=payload.department_id,
        team_id=payload.team_id,
        role_code=payload.role_code,
        status=payload.status,
    )
    return sys_update_user(
        db,
        current_user=current_user,
        user_id=user_id,
        company_id=current_user.company_id,
        payload=sys_payload,
    )


@router.post("/users/{user_id}/activate", status_code=204)
def ca_activate_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> None:
    _assert_target_not_elevated(db, user_id, current_user.company_id)
    sys_change_user_status(
        db, current_user=current_user,
        user_id=user_id, company_id=current_user.company_id,
        new_status="active",
    )


@router.post("/users/{user_id}/deactivate", status_code=204)
def ca_deactivate_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> None:
    _assert_target_not_elevated(db, user_id, current_user.company_id)
    sys_change_user_status(
        db, current_user=current_user,
        user_id=user_id, company_id=current_user.company_id,
        new_status="inactive",
    )


@router.delete("/users/{user_id}", status_code=204)
def ca_delete_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> None:
    _assert_target_not_elevated(db, user_id, current_user.company_id)
    sys_delete_user(
        db, current_user=current_user,
        user_id=user_id, company_id=current_user.company_id,
    )
