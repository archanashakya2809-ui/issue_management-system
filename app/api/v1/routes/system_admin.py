from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, require_permission
from app.models.user import User
from app.schemas.platform_settings import PlatformSettingsRead, PlatformSettingsUpdate
from app.schemas.org import CategoryItem, DepartmentItem, DomainItem, TeamItem
from app.schemas.system_admin import (
    AuditLogListResponse,
    PlatformAdminItem,
    RoleItem,
    SysOrgCategoryCreate,
    SysOrgCategoryUpdate,
    SysOrgDeptCreate,
    SysOrgDeptUpdate,
    SysOrgDomainCreate,
    SysOrgDomainUpdate,
    SysOrgTeamCreate,
    SysOrgTeamUpdate,
    SysUserCreate,
    SysUserListItem,
    SysUserListResponse,
    SysUserUpdate,
    SysUserRestore,
    SystemHealthResponse,
    WorkflowStatusItem,
    WorkflowStatusUpdate,
    WorkflowTransitionCreate,
    WorkflowTransitionItem,
    WorkflowTransitionUpdate,
)
from app.services.org_service import (
    create_category,
    create_department,
    create_domain,
    create_team,
    list_categories,
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
    sys_list_roles,
    sys_list_users,
    sys_list_deleted_users,
    sys_restore_user,
    sys_update_user,
)
from app.services.platform_settings_service import get_platform_settings, update_platform_settings
from app.services.system_admin_service import (
    create_workflow_transition,
    get_system_health,
    list_audit_logs,
    list_platform_admins,
    list_security_events,
    list_workflow_statuses,
    list_workflow_transitions,
    update_workflow_status,
    update_workflow_transition,
)

router = APIRouter(prefix="/system", tags=["system-admin"])


# ── Workflow Statuses ─────────────────────────────────────────────────────────

@router.get("/workflow/statuses", response_model=list[WorkflowStatusItem])
def read_workflow_statuses(
    _current_user: Annotated[User, Depends(require_permission("workflow.manage"))],
    db: DbSession,
) -> list[WorkflowStatusItem]:
    return list_workflow_statuses(db)


@router.patch("/workflow/statuses/{status_id}", response_model=WorkflowStatusItem)
def edit_workflow_status(
    status_id: UUID,
    payload: WorkflowStatusUpdate,
    _current_user: Annotated[User, Depends(require_permission("workflow.manage"))],
    db: DbSession,
) -> WorkflowStatusItem:
    return update_workflow_status(
        db,
        status_id=status_id,
        payload_name=payload.name,
        payload_is_active=payload.is_active,
    )


# ── Workflow Transitions ──────────────────────────────────────────────────────

@router.get("/workflow/transitions", response_model=list[WorkflowTransitionItem])
def read_workflow_transitions(
    _current_user: Annotated[User, Depends(require_permission("workflow.manage"))],
    db: DbSession,
) -> list[WorkflowTransitionItem]:
    return list_workflow_transitions(db)


@router.post("/workflow/transitions", response_model=WorkflowTransitionItem, status_code=201)
def add_workflow_transition(
    payload: WorkflowTransitionCreate,
    current_user: Annotated[User, Depends(require_permission("workflow.manage"))],
    db: DbSession,
) -> WorkflowTransitionItem:
    return create_workflow_transition(
        db,
        company_id=current_user.company_id,
        from_status_id=payload.from_status_id,
        to_status_id=payload.to_status_id,
        required_permission=payload.required_permission,
        required_role_code=payload.required_role_code,
    )


@router.patch("/workflow/transitions/{transition_id}", response_model=WorkflowTransitionItem)
def edit_workflow_transition(
    transition_id: UUID,
    payload: WorkflowTransitionUpdate,
    _current_user: Annotated[User, Depends(require_permission("workflow.manage"))],
    db: DbSession,
) -> WorkflowTransitionItem:
    return update_workflow_transition(
        db,
        transition_id=transition_id,
        is_active=payload.is_active,
        required_permission=payload.required_permission,
    )


# ── Audit Logs (platform-wide, system admin) ──────────────────────────────────

@router.get("/audit-logs", response_model=AuditLogListResponse)
def read_platform_audit_logs(
    _current_user: Annotated[User, Depends(require_permission("audit.view"))],
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditLogListResponse:
    return list_audit_logs(db, limit=limit, offset=offset)


# ── Audit Logs (company-scoped, company admin) ────────────────────────────────

@router.get("/company-audit-logs", response_model=AuditLogListResponse)
def read_company_audit_logs(
    current_user: Annotated[User, Depends(require_permission("audit.view"))],
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditLogListResponse:
    return list_audit_logs(
        db,
        company_id=current_user.company_id,
        limit=limit,
        offset=offset,
    )


# ── Security Events ───────────────────────────────────────────────────────────

@router.get("/security-events", response_model=AuditLogListResponse)
def read_security_events(
    _current_user: Annotated[User, Depends(require_permission("security.manage"))],
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditLogListResponse:
    return list_security_events(db, limit=limit, offset=offset)


# ── Platform Administrators ───────────────────────────────────────────────────

@router.get("/platform-admins", response_model=list[PlatformAdminItem])
def read_platform_admins(
    _current_user: Annotated[User, Depends(require_permission("admin.manage"))],
    db: DbSession,
) -> list[PlatformAdminItem]:
    return list_platform_admins(db)


# ── System Health ─────────────────────────────────────────────────────────────

@router.get("/health", response_model=SystemHealthResponse)
def read_system_health(
    _current_user: Annotated[User, Depends(require_permission("system.health.view"))],
    db: DbSession,
) -> SystemHealthResponse:
    return get_system_health(db)


# ── System-admin org management (cross-tenant) ───────────────────────────────
# These endpoints accept an explicit company_id so the system admin can
# manage org structure for any tenant, not just the platform company.

@router.get("/org/departments", response_model=list[DepartmentItem])
def sys_list_departments(
    company_id: UUID,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
) -> list[DepartmentItem]:
    return list_departments(db, company_id=company_id)


@router.post("/org/departments", response_model=DepartmentItem, status_code=201)
def sys_add_department(
    payload: SysOrgDeptCreate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
) -> DepartmentItem:
    return create_department(
        db,
        company_id=payload.company_id,
        name=payload.name,
        description=payload.description,
    )


@router.patch("/org/departments/{dept_id}", response_model=DepartmentItem)
def sys_edit_department(
    dept_id: UUID,
    payload: SysOrgDeptUpdate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> DepartmentItem:
    return update_department(
        db,
        company_id=company_id,
        dept_id=dept_id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
    )


@router.get("/org/teams", response_model=list[TeamItem])
def sys_list_teams(
    company_id: UUID,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
) -> list[TeamItem]:
    return list_teams(db, company_id=company_id, department_id=department_id)


@router.post("/org/teams", response_model=TeamItem, status_code=201)
def sys_add_team(
    payload: SysOrgTeamCreate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
) -> TeamItem:
    return create_team(
        db,
        company_id=payload.company_id,
        name=payload.name,
        department_id=payload.department_id,
        description=payload.description,
    )


@router.patch("/org/teams/{team_id}", response_model=TeamItem)
def sys_edit_team(
    team_id: UUID,
    payload: SysOrgTeamUpdate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> TeamItem:
    return update_team(
        db,
        company_id=company_id,
        team_id=team_id,
        name=payload.name,
        status=payload.status,
    )


@router.get("/org/domains", response_model=list[DomainItem])
def sys_list_domains(
    company_id: UUID,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
    department_id: UUID | None = Query(default=None),
) -> list[DomainItem]:
    return list_domains(db, company_id=company_id, department_id=department_id)


@router.post("/org/domains", response_model=DomainItem, status_code=201)
def sys_add_domain(
    payload: SysOrgDomainCreate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
) -> DomainItem:
    return create_domain(
        db,
        company_id=payload.company_id,
        name=payload.name,
        department_id=payload.department_id,
        description=payload.description,
    )


@router.patch("/org/domains/{domain_id}", response_model=DomainItem)
def sys_edit_domain(
    domain_id: UUID,
    payload: SysOrgDomainUpdate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> DomainItem:
    return update_domain(
        db,
        company_id=company_id,
        domain_id=domain_id,
        name=payload.name,
        status=payload.status,
    )


@router.get("/org/categories", response_model=list[CategoryItem])
def sys_list_categories(
    company_id: UUID,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
    domain_id: UUID | None = Query(default=None),
) -> list[CategoryItem]:
    return list_categories(db, company_id=company_id, domain_id=domain_id)


@router.post("/org/categories", response_model=CategoryItem, status_code=201)
def sys_add_category(
    payload: SysOrgCategoryCreate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
) -> CategoryItem:
    return create_category(
        db,
        company_id=payload.company_id,
        name=payload.name,
        domain_id=payload.domain_id,
        description=payload.description,
    )


@router.patch("/org/categories/{category_id}", response_model=CategoryItem)
def sys_edit_category(
    category_id: UUID,
    payload: SysOrgCategoryUpdate,
    _current_user: Annotated[User, Depends(require_permission("company.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> CategoryItem:
    return update_category(
        db,
        company_id=company_id,
        category_id=category_id,
        name=payload.name,
        is_active=payload.is_active,
    )


# ── System-admin user management (cross-tenant) ───────────────────────────────

@router.get("/roles", response_model=list[RoleItem])
def list_roles(
    _current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> list[RoleItem]:
    return sys_list_roles(db)


@router.get("/users", response_model=SysUserListResponse)
def read_users(
    _current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    company_id: UUID | None = Query(default=None),
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
        company_id=company_id,
        department_id=department_id,
        team_id=team_id,
        q=q,
        status=status,
        role_code=role_code,
        limit=limit,
        offset=offset,
    )


@router.get("/users/deleted", response_model=SysUserListResponse)
def read_deleted_users(
    _current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    company_id: UUID | None = Query(default=None),
    department_id: UUID | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None),
    role_code: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SysUserListResponse:
    return sys_list_deleted_users(
        db, company_id=company_id, department_id=department_id, q=q,
        status=status, role_code=role_code, limit=limit, offset=offset,
    )


@router.post("/users/{user_id}/restore", response_model=SysUserListItem)
def restore_deleted_user(
    user_id: UUID,
    payload: SysUserRestore,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> SysUserListItem:
    return sys_restore_user(
        db, current_user=current_user, user_id=user_id,
        company_id=company_id, payload=payload,
    )



@router.post("/users", response_model=SysUserListItem, status_code=201)
def create_user(
    payload: SysUserCreate,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
) -> SysUserListItem:
    return sys_create_user(db, current_user=current_user, payload=payload)


@router.patch("/users/{user_id}", response_model=SysUserListItem)
def edit_user(
    user_id: UUID,
    payload: SysUserUpdate,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> SysUserListItem:
    return sys_update_user(db, current_user=current_user, user_id=user_id, company_id=company_id, payload=payload)


@router.post("/users/{user_id}/activate", status_code=204)
def activate_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> None:
    sys_change_user_status(db, current_user=current_user, user_id=user_id, company_id=company_id, new_status="active")


@router.post("/users/{user_id}/deactivate", status_code=204)
def deactivate_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> None:
    sys_change_user_status(db, current_user=current_user, user_id=user_id, company_id=company_id, new_status="inactive")


@router.delete("/users/{user_id}", status_code=204)
def remove_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_permission("user.manage"))],
    db: DbSession,
    company_id: UUID = Query(...),
) -> None:
    sys_delete_user(db, current_user=current_user, user_id=user_id, company_id=company_id)


# ── Platform Settings ────────────────────────────────────────────────────────

@router.get("/settings", response_model=PlatformSettingsRead)
def read_platform_settings(
    current_user: Annotated[User, Depends(require_permission("system.config.manage"))],
    db: DbSession,
) -> PlatformSettingsRead:
    return get_platform_settings(db, company_id=current_user.company_id)


@router.put("/settings", response_model=PlatformSettingsRead)
def save_platform_settings(
    payload: PlatformSettingsUpdate,
    current_user: Annotated[User, Depends(require_permission("system.config.manage"))],
    db: DbSession,
) -> PlatformSettingsRead:
    return update_platform_settings(db, current_user=current_user, payload=payload)
